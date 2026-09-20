"""
QA Agent System — Coordinator Agent Factory

Creates a Coordinator-mode Agno Agent with specialized system prompt
and restricted orchestration-only tool set.

The Coordinator is a standard Agno Agent (NOT a Team) that manages
Worker agents via agent_spawn, send_message, task_stop, and get_worker_status.

Design reference: D1 (Coordinator as Agno Agent)
coordinator-session-restore: added skip_plan_phase / plan_context params;
  removed deprecated L3 post_hook.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from agno.agent import Agent

logger = logging.getLogger(__name__)


async def create_coordinator_agent(
    task: str = "",
    *,
    session_id: Optional[str] = None,
    game_version: str = "",
    module: str = "",
    config=None,
    skip_plan_phase: bool = False,
    plan_context: str = "",
) -> Agent:
    """Create a Coordinator-mode Agno Agent.

    The Coordinator:
    - Has the comprehensive QA orchestration system prompt
    - Is restricted to 4 tools: agent_spawn, send_message, task_stop, get_worker_status
    - Delegates all actual work to Worker agents
    - Synthesises Worker results and produces test reports

    Args:
        task: The high-level QA task description.
        session_id: Override session ID (auto-generated if None).
        game_version: Game version context.
        module: Game module being tested.
        config: Settings override.
        skip_plan_phase: If True, skip Phase 0 PlanAgent execution and use the
            ``plan_context`` argument directly.  Used during session restore so
            the planning step is not re-run.
        plan_context: Pre-generated plan text (only used when
            ``skip_plan_phase=True`` or as override).  Truncated to 3000 chars.

    Returns:
        Configured Agno Agent ready for ``agent.arun()``.
    """
    from coordinator.prompts import (
        get_coordinator_system_prompt,
        get_worker_descriptions_from_registry,
    )
    from core.config import settings as _settings
    from core.models import get_model
    from core.session_state import QASessionState
    from core.storage import get_storage
    from core.memory_setup import get_knowledge_agent_kwargs, get_memory_agent_kwargs
    from tools.agent_spawn_tool import agent_spawn
    from tools.send_message_tool import send_message
    from tools.task_stop_tool import task_stop
    from tools.worker_status_tool import get_worker_status

    cfg = config or _settings
    sid = session_id or str(uuid.uuid4())

    # Build dynamic system prompt with current agent registry
    worker_desc = get_worker_descriptions_from_registry()
    instructions = get_coordinator_system_prompt(worker_descriptions=worker_desc)

    # Phase 0: get execution plan (skip on restore)
    if skip_plan_phase:
        effective_plan = (plan_context or "")[:3000]
        logger.info(
            "Coordinator Agent (session=%s): skip_plan_phase=True, "
            "using provided plan_context (%d chars)",
            sid, len(effective_plan),
        )
    else:
        effective_plan = await _run_plan_phase(
            task=task,
            session_id=sid,
            game_version=game_version,
            module=module,
            config=config,
        )
        # Persist plan_context to user_sessions (fire-and-forget, best-effort)
        if effective_plan:
            _persist_plan_context_bg(sid, effective_plan)

    if effective_plan:
        instructions += (
            "\n\n## Pre-Generated Execution Plan\n"
            "PlanAgent has analyzed the task and produced the following plan. "
            "Use it to guide your delegation strategy.\n\n"
            + effective_plan
        )

    # Coordinator-only tools
    coordinator_tools = [
        agent_spawn,
        send_message,
        task_stop,
        get_worker_status,
    ]

    # Build session state
    session_state = QASessionState(
        max_turns=cfg.default_max_turns,
        max_context_tokens=cfg.max_context_tokens,
        context_soft_threshold=cfg.context_soft_threshold,
        context_hard_threshold=cfg.context_hard_threshold,
        permission_mode="default",
        agent_type="coordinator",
        agent_name="QACoordinator",
        session_id=sid,
        game_version=game_version,
        module=module,
        coordinator_session=True,
    )

    # Storage and memory
    db = get_storage(cfg)
    memory_kwargs = get_memory_agent_kwargs(db=db, config=cfg)
    knowledge_kwargs = get_knowledge_agent_kwargs()

    # Pre-hooks: reminder injection
    from hooks.reminder_hook import reminder_pre_hook

    # Build Agent (no L3 post_hook — deprecated)
    agent = Agent(
        id="QACoordinator",
        name="QACoordinator",
        model=get_model(cfg),
        tools=coordinator_tools,
        db=db,
        instructions=instructions,
        session_state=session_state,
        session_id=sid,
        **memory_kwargs,
        **knowledge_kwargs,
        markdown=True,
        pre_hooks=[reminder_pre_hook],
    )

    # Enable WorkerPool MongoDB persistence for this coordinator session
    from coordinator.worker_pool import worker_pool
    worker_pool.set_persistence(sid)

    logger.info(
        "Created Coordinator Agent (session=%s, tools=%d, has_plan=%s, restore=%s)",
        sid, len(coordinator_tools), bool(effective_plan), skip_plan_phase,
    )
    return agent


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _run_plan_phase(
    task: str,
    session_id: str,
    game_version: str = "",
    module: str = "",
    config=None,
) -> str:
    """Phase 0: Spawn PlanAgent to create an execution plan.

    PlanAgent produces a free-text step-by-step plan which is returned as-is
    and injected into the Coordinator's system prompt. No structured parsing
    is performed — the Coordinator consumes the natural-language plan directly.

    Returns:
        Raw plan text (truncated to 3000 chars), or empty string if planning fails.
    """
    from agents.base import create_agent_from_definition
    from agents.registry import agent_registry

    plan_def = agent_registry.get("PlanAgent")
    if plan_def is None:
        logger.warning("PlanAgent not found in registry, skipping Phase 0 planning")
        return ""

    if not task or not task.strip():
        return ""

    logger.info("Phase 0: Running PlanAgent for task planning (session=%s)", session_id)

    try:
        plan_agent = await create_agent_from_definition(
            plan_def,
            session_id=f"{session_id}__planner",
            task_description=task,
            game_version=game_version,
            module=module,
            config=config,
        )

        response = await plan_agent.arun(task)

        # Extract response text
        response_text = ""
        if hasattr(response, "content") and response.content:
            response_text = str(response.content)
        else:
            response_text = str(response or "")

        if not response_text.strip():
            logger.warning("PlanAgent returned empty response (session=%s)", session_id)
            return ""

        plan_context = response_text[:3000]
        logger.info("Phase 0 complete: plan ready (session=%s)", session_id)
        return plan_context

    except Exception:
        logger.exception(
            "Phase 0 PlanAgent failed (session=%s), continuing without plan",
            session_id,
        )
        return ""


def _persist_plan_context_bg(session_id: str, plan_context: str) -> None:
    """Fire-and-forget: persist plan_context to user_sessions after Phase 0.

    The session_id alone is insufficient to look up the user email here, so
    this uses the new update_session_plan_context_by_session_id DAO which
    searches across all user documents.  Failures are logged at WARNING level
    and do not affect agent creation.
    """
    import asyncio

    async def _do_persist() -> None:
        try:
            from db.user_sessions import update_session_plan_context_by_session_id
            await update_session_plan_context_by_session_id(session_id, plan_context[:3000])
        except Exception:
            logger.warning(
                "Failed to persist plan_context for session %s", session_id, exc_info=True
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_persist())
    except RuntimeError:
        pass  # no running loop (e.g. in tests); skip silently