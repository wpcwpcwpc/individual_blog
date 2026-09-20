"""
QA Agent System — Skill Discovery Tools

Provides two globally-injected tools for runtime Skill discovery and loading:
- find_skill: Search the global SkillLoader index by keyword query
- load_skill: Load a specific Skill's prompt with Phase-aware progressive loading

These tools are injected into all Agents by default (same level as grep/glob).
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from agno.run.base import RunContext
from agno.tools import tool

from skills.expander import expand_skill_prompt
from skills.phase_cache import build_phase_cache, parse_skill_phases
from skills.runtime_vars import substitute_runtime_placeholders

logger = logging.getLogger(__name__)


def _get_skill_loader():
    """Retrieve the global SkillLoader from FastAPI app.state.

    Returns:
        SkillLoader instance or None if not available.
    """
    try:
        from api.server import app
        return getattr(app.state, "skill_loader", None)
    except Exception:
        logger.warning("Failed to access app.state.skill_loader")
        return None


def _resolve_owner_email(run_context: Optional[RunContext]) -> str | None:
    """从 run_context 提取 owner email（load_skill DB fallback 用）。

    标准流程 server.py 传 user_id=email，直接用。
    task_trigger 类 agent 传 [project]_email 格式，取最后一个 _ 之后部分。
    非 email 格式返回 None（跳过 DB fallback）。

    Args:
        run_context: agno RunContext（None 时返回 None）。

    Returns:
        email 字符串或 None。
    """
    if run_context is None:
        return None
    user_id = getattr(run_context, "user_id", None)
    if not user_id:
        return None
    # task_trigger 格式 [project]_email → 取 rsplit("_",1)[-1]（project 名无 @）
    if "_" in user_id:
        tail = user_id.rsplit("_", 1)[-1]
        if "@" in tail:
            return tail
        return None
    # 标准流程：user_id 即 email
    if "@" in user_id:
        return user_id
    return None


async def _db_fallback_load_skill(name: str, owner_email: str):
    """DB fallback：本地 miss 时从 user_skills 物化加载。

    Returns:
        SkillDefinition 或 None（未命中/物化失败）。
    """
    try:
        from db.mongo import get_motor_db
        from skills.materializer import load_skill_from_db
        from pathlib import Path
        from core.config import settings
        db = get_motor_db()
        if db is None:
            return None
        target_dir = Path(settings.user_skills_dir).expanduser()
        return await load_skill_from_db(db, owner_email, name, _get_skill_loader(), target_dir)
    except Exception:
        logger.warning("load_skill DB fallback 异常", exc_info=True)
        return None


@tool(
    name="find_skill",
    description=(
        "Search for available Skills (specialized capability packs) by natural-language query. "
        "Call this when a task needs domain expertise; then use load_skill on the best match."
    ),
)
async def find_skill(agent, query: str = "") -> str:
    """Search for available Skills by keyword query.

    搜索范围：本地 SkillLoader 已加载 skill + DB user_skills（当前用户上传的 skill）。
    DB 来源 skill 被 load_skill 加载时走 DB fallback 物化。

    Args:
        agent: The Agno Agent instance (injected by framework).
        query: Natural-language search query.

    Returns:
        JSON string with matching Skill list.
    """
    loader = _get_skill_loader()
    if loader is None:
        return json.dumps({"error": "SkillLoader not available"})

    results = loader.search(query, top_k=5)

    # DB fallback：合并当前用户上传的 skill（本地未物化也参与搜索）
    db_results: list[dict] = []
    owner_email = _resolve_owner_email(getattr(agent, "run_context", None))
    if owner_email:
        try:
            from db.mongo import get_motor_db
            from skills.materializer import find_user_skills_from_db
            db = get_motor_db()
            if db is not None:
                db_results = await find_user_skills_from_db(db, owner_email, query, top_k=5)
        except Exception:
            logger.warning("find_skill DB fallback 异常", exc_info=True)

    if not results and not db_results:
        # Return all available skill names as hint
        all_skills = loader.all()
        return json.dumps({
            "matches": [],
            "hint": f"No skills matched '{query}'. Available skills: "
                    + ", ".join(s.name for s in all_skills),
        })

    matches = [
        {
            "name": s.name,
            "description": s.description,
            "when_to_use": s.when_to_use,
            "args": s.args,
            "load_mode": s.load_mode,
        }
        for s in results
    ]
    matches.extend(db_results)

    logger.info("find_skill(query=%r) → %d matches", query, len(matches))
    return json.dumps({"matches": matches}, ensure_ascii=False)


@tool(
    name="load_skill",
    description=(
        "Load a Skill's full instruction prompt by exact name. Supports phased loading. "
        "Call after find_skill, then follow the returned instructions verbatim."
    ),
)
async def load_skill(agent, run_context: Optional[RunContext] = None, skill_name: str = "", arguments: str = "") -> str:
    """Load a Skill's expanded prompt by name.

    本地 miss 时走 DB fallback：从 user_skills 按 (owner_email, name) 查 zip_data →
    物化到本地 → 解析缓存进 SkillLoader → 返回。owner_email 取自 run_context.user_id。
    run_context 为 None（单测）跳过 fallback。幂等：已缓存不重复物化。

    Supports Phase-aware progressive loading: if the Skill contains Phase
    markers, only Phase 1 is returned and subsequent Phases are cached in
    session_state for automatic injection via task_update.

    Args:
        agent: The Agno Agent instance (injected by framework).
        run_context: The agno RunContext (auto-injected by framework).
        skill_name: Exact Skill name to load.
        arguments: Space-separated arguments for the Skill.

    Returns:
        Expanded prompt content string, or JSON error if not found.
    """
    loader = _get_skill_loader()
    if loader is None:
        return json.dumps({"error": "SkillLoader not available"})

    skill = loader.get(skill_name)

    # ── DB fallback：本地 miss 时从 user_skills 物化加载 ──
    if skill is None:
        owner_email = _resolve_owner_email(run_context)
        if owner_email is not None:
            skill = await _db_fallback_load_skill(skill_name, owner_email)

    if skill is None:
        all_names = [s.name for s in loader.all()]
        return json.dumps({
            "error": f"Skill '{skill_name}' not found",
            "available_skills": all_names,
        }, ensure_ascii=False)

    logger.info("load_skill('%s', arguments=%r)", skill_name, arguments)

    # Substitute runtime placeholders BEFORE template expansion
    # (prevents $ARGUMENTS injection of ${SESSION_ID}/${USER_ID})
    template_with_runtime = substitute_runtime_placeholders(
        skill.prompt_template, run_context
    )

    # Expand the prompt template
    expanded = expand_skill_prompt(
        template=template_with_runtime,
        base_dir=skill.base_dir,
        args=skill.args,
        arguments=arguments,
    )

    # Phase-aware expansion
    parse_result = parse_skill_phases(expanded)

    if parse_result is not None and parse_result.phases:
        # Phase markers found → progressive loading
        logger.info(
            "Skill '%s' has %d phases, using phase-aware loading",
            skill_name, len(parse_result.phases),
        )

        parts: List[str] = []
        if parse_result.global_preamble:
            parts.append(parse_result.global_preamble)
        parts.append(parse_result.phases[0].content)
        if parse_result.global_constraints:
            parts.append(parse_result.global_constraints)

        # Add required/suggested read prompts for Phase 1
        required = parse_result.required_reads.get(0, [])
        suggested = parse_result.suggested_reads.get(0, [])
        if required or suggested:
            from skills.skill_tool import _format_read_prompts
            parts.append(_format_read_prompts(required, suggested, skill.base_dir))

        # Build and store SkillPhaseCache in session_state
        cache = build_phase_cache(
            skill_name=skill_name,
            skill_base_dir=str(skill.base_dir),
            parse_result=parse_result,
        )

        state = getattr(agent, "session_state", None)
        if state is not None:
            state.active_skill_name = skill_name
            state.active_skill_phase = 0
            state.skill_phase_cache = cache.to_dict()
            logger.debug(
                "Stored SkillPhaseCache for '%s' (phases=%d)",
                skill_name, cache.phase_count,
            )

        result = "\n\n".join(parts)
    else:
        # No Phase markers → full expansion
        logger.debug("Skill '%s' has no phase markers, full expansion", skill_name)
        result = expanded

    logger.debug("load_skill('%s') → %d chars", skill_name, len(result))
    return result
