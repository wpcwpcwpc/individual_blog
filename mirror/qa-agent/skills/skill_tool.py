"""
QA Agent System — Skill Tool

Converts SkillDefinitions into Agno @tool functions that, when called,
expand the SKILL.md prompt and return it as a string for the Agent to act on.

Supports Phase-aware progressive loading: if SKILL.md contains Phase markers,
only the current Phase's instructions are returned, with subsequent Phases
injected via task_update's phase-advance mechanism.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

from agno.run.base import RunContext
from agno.tools import tool

from skills.expander import expand_skill_prompt
from skills.loader import SkillDefinition
from skills.phase_cache import build_phase_cache, parse_skill_phases
from skills.runtime_vars import substitute_runtime_placeholders

logger = logging.getLogger(__name__)


def _format_read_prompts(
    required: list[str],
    suggested: list[str],
    base_dir: Path,
) -> str:
    """Format read file prompts for Phase injection.

    Args:
        required: List of required file paths (relative to skill dir).
        suggested: List of suggested file paths.
        base_dir: Skill base directory for resolving absolute paths.

    Returns:
        Formatted prompt string.
    """
    lines = []
    if required:
        lines.append("\n⚠️ 请立即读取以下文件（必须在执行本阶段前完成）：")
        for path in required:
            abs_path = (base_dir / path).resolve()
            lines.append(f"- `{abs_path}`")
    if suggested:
        lines.append("\n📖 建议读取以下文件（可按需查阅）：")
        for path in suggested:
            abs_path = (base_dir / path).resolve()
            lines.append(f"- `{abs_path}`")
    return "\n".join(lines)


def make_skill_tool(skill: SkillDefinition) -> Callable:
    """Create an Agno @tool function from a SkillDefinition.

    The returned tool, when called by the Agent:
    1. Expands the SKILL.md prompt (variable substitution + shell commands)
    2. Attempts Phase-aware parsing; if successful, returns only Phase 1
    3. Falls back to full expansion if no Phase markers found
    4. Sets session_state fields for Phase tracking

    Args:
        skill: A loaded SkillDefinition.

    Returns:
        A decorated Agno tool function.
    """
    skill_name = skill.name
    skill_desc = skill.description
    when_to_use = skill.when_to_use

    # Build LLM-visible description
    tool_description = skill_desc
    if when_to_use:
        tool_description += f"\n\n何时使用: {when_to_use}"
    if skill.args:
        tool_description += f"\n\n参数: {', '.join(skill.args)}"

    @tool(
        name=skill.tool_name,
        description=tool_description,
    )
    def skill_tool_fn(agent, run_context: Optional[RunContext] = None, arguments: str = "") -> str:
        """Execute the skill and return the expanded prompt.

        Args:
            agent: The Agno Agent instance (injected by framework).
            run_context: The agno RunContext (auto-injected by framework).
            arguments: Space-separated arguments for the skill
                       (mapped to args defined in Frontmatter).

        Returns:
            Expanded prompt content for the Agent to act on.
        """
        logger.info("Executing skill '%s' (arguments=%r)", skill_name, arguments)

        # Substitute runtime placeholders BEFORE template expansion
        # (prevents $ARGUMENTS injection of ${SESSION_ID}/${USER_ID})
        template_with_runtime = substitute_runtime_placeholders(
            skill.prompt_template, run_context
        )

        expanded = expand_skill_prompt(
            template=template_with_runtime,
            base_dir=skill.base_dir,
            args=skill.args,
            arguments=arguments,
        )

        # ── Phase-aware expansion ───────────────────────────────────
        parse_result = parse_skill_phases(expanded)

        if parse_result is not None and parse_result.phases:
            # Phase markers found → progressive loading
            logger.info(
                "Skill '%s' has %d phases, using phase-aware loading",
                skill_name, len(parse_result.phases),
            )

            # Build Phase 1 output: preamble + Phase 1 content + constraints
            parts = []
            if parse_result.global_preamble:
                parts.append(parse_result.global_preamble)
            parts.append(parse_result.phases[0].content)
            if parse_result.global_constraints:
                parts.append(parse_result.global_constraints)

            # Add required/suggested read prompts for Phase 1
            required = parse_result.required_reads.get(0, [])
            suggested = parse_result.suggested_reads.get(0, [])
            if required or suggested:
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
            # No Phase markers → full expansion (current behavior)
            logger.debug("Skill '%s' has no phase markers, using full expansion", skill_name)
            result = expanded

        logger.debug(
            "Skill '%s' expanded prompt (%d chars)",
            skill_name, len(result),
        )

        return result

    return skill_tool_fn


def build_skill_tools(skills: List[SkillDefinition]) -> List[Callable]:
    """Convert a list of SkillDefinitions into Agno tool functions.

    Args:
        skills: List of loaded SkillDefinition objects.

    Returns:
        List of Agno @tool functions, one per skill.
    """
    tool_fns = []
    for skill in skills:
        try:
            fn = make_skill_tool(skill)
            tool_fns.append(fn)
            logger.debug("Created skill tool: %s", skill.tool_name)
        except Exception:
            logger.exception("Failed to create tool for skill '%s'", skill.name)

    logger.info("Built %d skill tool(s)", len(tool_fns))
    return tool_fns