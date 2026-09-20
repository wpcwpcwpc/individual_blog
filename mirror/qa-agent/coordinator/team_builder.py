"""
QA Agent System — Coordinator Team Builder

Creates an Agno Team(mode=TeamMode.coordinate) with a Leader Agent
and dynamically assembled Worker Agents.

**DEPRECATED**: The Team-based approach is kept for backward compatibility.
New code should use ``coordinator.coordinator_agent.create_coordinator_agent()``
which provides full Worker lifecycle tracking, send_message continuation,
and structured notifications.

The Coordinator pattern:
  Leader → delegates tasks → [Worker A, Worker B, Worker C] (parallel)
         ← collects results ←
         → generates comprehensive report
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def create_coordinator_session(
    task: str,
    *,
    session_id: Optional[str] = None,
    game_version: str = "",
    module: str = "",
    config=None,
) -> Any:
    """Create a Coordinator-mode Agno Agent (recommended path).

    This is the new recommended way to create a Coordinator. It uses a
    standard Agno Agent with orchestration tools instead of an Agno Team,
    providing full Worker lifecycle tracking, send_message continuation,
    and structured notifications.

    Args:
        task: The high-level task description for the Coordinator.
        session_id: Override session ID (auto-generated if None).
        game_version: Game version for context.
        module: Game module for context.
        config: Settings override.

    Returns:
        Configured Agno Agent instance (Coordinator mode).
    """
    from coordinator.coordinator_agent import create_coordinator_agent

    return await create_coordinator_agent(
        task=task,
        session_id=session_id,
        game_version=game_version,
        module=module,
        config=config,
    )


async def create_coordinator_team(
    task: str,
    worker_agent_names: Optional[List[str]] = None,
    *,
    session_id: Optional[str] = None,
    game_version: str = "",
    module: str = "",
    config=None,
    session_overrides: Optional[Dict[str, Any]] = None,
    workspace_root: Optional[str] = None,
) -> Any:
    """Create an Agno Team in coordinate mode for parallel QA task execution.

    .. deprecated::
        Use :func:`create_coordinator_session` instead for full Worker
        lifecycle tracking and send_message continuation.

    Includes Phase 0: PlanAgent pre-planning (if task is provided).

    Args:
        task: The high-level task description for the Coordinator.
        worker_agent_names: Names of agents to use as workers.
                            Defaults to [GeneralAgent].
        session_id: Override session ID (auto-generated if None).
        game_version: Game version for context.
        module: Game module for context.
        config: Settings override.

    Returns:
        Configured Agno Team instance.
    """
    from agno.team import Team
    from agno.team.team import TeamMode

    from agents.base import create_agent_from_definition
    from agents.registry import agent_registry
    from coordinator.prompts import COORDINATOR_SYSTEM_PROMPT
    from core.engine import create_normal_agent

    sid = session_id or str(uuid.uuid4())

    # --- Resolve Leader model via orchestrate slot ---
    from core.model_slots import get_model_slot_registry, ModelSlot
    leader_model = get_model_slot_registry().resolve(
        ModelSlot.ORCHESTRATE, session_overrides
    )

    # --- Phase 0: PlanAgent pre-planning ---
    plan_context = ""
    if task and task.strip():
        plan_context = await _run_plan_phase(
            task=task,
            session_id=sid,
            game_version=game_version,
            module=module,
            config=config,
            session_overrides=session_overrides,
        )

    # --- Build Worker Agents (model resolved via slot registry) ---
    worker_names = worker_agent_names or ["GeneralAgent"]
    workers = []

    for name in worker_names:
        definition = agent_registry.get(name)
        if definition is None:
            logger.warning("Worker agent '%s' not found in registry, skipping", name)
            continue

        # Workers get a sub-session derived from coordinator session
        worker_sid = f"{sid}__worker__{name}"
        worker = await create_agent_from_definition(
            definition,
            session_id=worker_sid,
            task_description=task,
            game_version=game_version,
            module=module,
            config=config,
            session_overrides=session_overrides,
        )

        # Tag worker in session_state and inherit workspace_root
        if hasattr(worker, "session_state") and worker.session_state:
            worker.session_state.agent_type = "worker"
            worker.session_state.worker_id = worker_sid
            if workspace_root:
                worker.session_state.workspace_root = workspace_root

        workers.append(worker)
        logger.info("Created worker: %s (session=%s)", name, worker_sid)

    if not workers:
        raise ValueError("No valid worker agents could be created for Coordinator team")

    # Inject plan context into coordinator instructions if available
    final_instructions = COORDINATOR_SYSTEM_PROMPT
    if plan_context:
        final_instructions = (
            COORDINATOR_SYSTEM_PROMPT
            + "\n\n"
            + "## Pre-Generated Execution Plan\n"
            + "PlanAgent has analyzed the task and produced the following plan. "
            + "Use it to guide your delegation strategy.\n\n"
            + plan_context
        )

    team = Team(
        name="QACoordinatorTeam",
        model=leader_model,
        mode=TeamMode.coordinate,
        members=workers,
        instructions=final_instructions,
        session_id=sid,
        markdown=True,
    )

    logger.info(
        "Created Coordinator Team (session=%s, workers=%d: %s, has_plan=%s)",
        sid, len(workers), worker_names, bool(plan_context),
    )
    return team


async def _run_plan_phase(
    task: str,
    session_id: str,
    game_version: str = "",
    module: str = "",
    config=None,
    session_overrides=None,
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

    logger.info("Phase 0: Running PlanAgent for task planning (session=%s)", session_id)

    try:
        plan_agent = await create_agent_from_definition(
            plan_def,
            session_id=f"{session_id}__planner",
            task_description=task,
            game_version=game_version,
            module=module,
            config=config,
            session_overrides=session_overrides,
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
        logger.exception("Phase 0 PlanAgent failed (session=%s), continuing without plan", session_id)
        return ""


async def create_coordinator_session(
    task: str,
    *,
    session_id: Optional[str] = None,
    game_version: str = "",
    module: str = "",
    config=None,
) -> Any:
    """Create a Coordinator-mode Agno Agent (recommended path).

    This is the new recommended way to create a Coordinator. It uses a
    standard Agno Agent with orchestration tools instead of an Agno Team,
    providing full Worker lifecycle tracking, send_message continuation,
    and structured notifications.

    Args:
        task: The high-level task description for the Coordinator.
        session_id: Override session ID (auto-generated if None).
        game_version: Game version for context.
        module: Game module for context.
        config: Settings override.

    Returns:
        Configured Agno Agent instance (Coordinator mode).
    """
    from coordinator.coordinator_agent import create_coordinator_agent

    return await create_coordinator_agent(
        task=task,
        session_id=session_id,
        game_version=game_version,
        module=module,
        config=config,
    )


async def create_coordinator_team(
    task: str,
    worker_agent_names: Optional[List[str]] = None,
    *,
    session_id: Optional[str] = None,
    game_version: str = "",
    module: str = "",
    config=None,
    session_overrides: Optional[Dict[str, Any]] = None,
    workspace_root: Optional[str] = None,
) -> Any:
    """Create an Agno Team in coordinate mode for parallel QA task execution.

    .. deprecated::
        Use :func:`create_coordinator_session` instead for full Worker
        lifecycle tracking and send_message continuation.

    Includes Phase 0: PlanAgent pre-planning (if task is provided).

    Args:
        task: The high-level task description for the Coordinator.
        worker_agent_names: Names of agents to use as workers.
                            Defaults to [GeneralAgent].
        session_id: Override session ID (auto-generated if None).
        game_version: Game version for context.
        module: Game module for context.
        config: Settings override.

    Returns:
        Configured Agno Team instance.
    """
    from agno.team import Team
    from agno.team.team import TeamMode

    from agents.base import create_agent_from_definition
    from agents.registry import agent_registry
    from coordinator.prompts import COORDINATOR_SYSTEM_PROMPT
    from core.engine import create_normal_agent

    sid = session_id or str(uuid.uuid4())

    # --- Resolve Leader model via orchestrate slot ---
    from core.model_slots import get_model_slot_registry, ModelSlot
    leader_model = get_model_slot_registry().resolve(
        ModelSlot.ORCHESTRATE, session_overrides
    )

    # --- Phase 0: PlanAgent pre-planning ---
    plan_context = ""
    if task and task.strip():
        plan_context = await _run_plan_phase(
            task=task,
            session_id=sid,
            game_version=game_version,
            module=module,
            config=config,
            session_overrides=session_overrides,
        )

    # --- Build Worker Agents (model resolved via slot registry) ---
    worker_names = worker_agent_names or ["GeneralAgent"]
    workers = []

    for name in worker_names:
        definition = agent_registry.get(name)
        if definition is None:
            logger.warning("Worker agent '%s' not found in registry, skipping", name)
            continue

        # Workers get a sub-session derived from coordinator session
        worker_sid = f"{sid}__worker__{name}"
        worker = await create_agent_from_definition(
            definition,
            session_id=worker_sid,
            task_description=task,
            game_version=game_version,
            module=module,
            config=config,
            session_overrides=session_overrides,
        )

        # Tag worker in session_state and inherit workspace_root
        if hasattr(worker, "session_state") and worker.session_state:
            worker.session_state.agent_type = "worker"
            worker.session_state.worker_id = worker_sid
            if workspace_root:
                worker.session_state.workspace_root = workspace_root

        workers.append(worker)
        logger.info("Created worker: %s (session=%s)", name, worker_sid)

    if not workers:
        raise ValueError("No valid worker agents could be created for Coordinator team")

    # --- Build Team post_hooks ---
    async def coordinator_post_hook(team: Any, response: Any) -> None:
        """After coordinator completes: L3 verify + write conclusion."""
        await _coordinator_verify_and_conclude(
            team_response=response,
            session_id=sid,
            game_version=game_version,
            module=module,
        )

    # --- Create Team ---
    # Note: Agno Team mode=COORDINATE means the Team acts as coordinator,
    # delegating tasks to members automatically. No separate "leader" agent needed.
    # The Coordinator (Team Leader) receives optimized prompts enforcing
    # the 4-phase workflow and is restricted to orchestration-only tools:
    #   delegate_task, send_message_to_member, get_member_status

    # Inject plan context into coordinator instructions if available
    final_instructions = COORDINATOR_SYSTEM_PROMPT
    if plan_context:
        final_instructions = (
            COORDINATOR_SYSTEM_PROMPT
            + "\n\n"
            + "## Pre-Generated Execution Plan\n"
            + "PlanAgent has analyzed the task and produced the following plan. "
            + "Use it to guide your delegation strategy.\n\n"
            + plan_context
        )

    team = Team(
        name="QACoordinatorTeam",
        model=leader_model,
        mode=TeamMode.coordinate,
        members=workers,
        instructions=final_instructions,
        session_id=sid,
        # Enable markdown for structured reports
        markdown=True,
        # Post hooks for L3 verification
        post_hooks=[coordinator_post_hook],
    )

    logger.info(
        "Created Coordinator Team (session=%s, workers=%d: %s, has_plan=%s)",
        sid, len(workers), worker_names, bool(plan_context),
    )
    return team


async def _run_plan_phase(
    task: str,
    session_id: str,
    game_version: str = "",
    module: str = "",
    config=None,
    session_overrides=None,
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

    logger.info("Phase 0: Running PlanAgent for task planning (session=%s)", session_id)

    try:
        plan_agent = await create_agent_from_definition(
            plan_def,
            session_id=f"{session_id}__planner",
            task_description=task,
            game_version=game_version,
            module=module,
            config=config,
            session_overrides=session_overrides,
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
        logger.exception("Phase 0 PlanAgent failed (session=%s), continuing without plan", session_id)
        return ""


async def _coordinator_verify_and_conclude(
    team_response: Any,
    session_id: str,
    game_version: str = "",
    module: str = "",
) -> None:
    """Post-hook: L3 human verification + Milvus conclusion write."""
    from hooks.interrupt_manager import InterruptType, interrupt_manager

    # Extract report content
    report = ""
    if hasattr(team_response, "content") and team_response.content:
        report = str(team_response.content)
    else:
        report = str(team_response or "")

    if not report:
        logger.warning("Coordinator produced empty report (session=%s)", session_id)
        return

    logger.info("Coordinator requesting L3 verification (session=%s)", session_id)

    # Trigger L3 human verification
    payload = {
        "result_summary": report[:3000],
        "agent_name": "CoordinatorLeader",
        "agent_type": "coordinator",
        "game_version": game_version,
        "module": module,
        "interrupt_context": "综合测试报告已生成，请人工审核并给出最终结论",
    }

    record = await interrupt_manager.request_interrupt(
        session_id=session_id,
        interrupt_type=InterruptType.RESULT_VERIFY,
        payload=payload,
    )

    action = record.action or "pass"
    notes = record.notes or ""

    logger.info(
        "Coordinator L3 verdict: %s (session=%s, notes=%s)",
        action, session_id, notes,
    )

    # Only write conclusion after human confirms
    if action in ("pass", "fail", "pass_with_issues"):
        try:
            from memory.conclusion_writer import write_conclusion
            import time

            await write_conclusion({
                "session_id": session_id,
                "verdict": action,
                "human_confirmed": True,   # L3 has been completed
                "confirmed_by": "human_reviewer",
                "key_findings": _extract_findings(report),
                "bugs_found": _extract_bugs(report),
                "game_version": game_version,
                "module": module,
                "notes": notes,
                "timestamp": time.time(),
            })
        except Exception:
            logger.exception("Failed to write Coordinator conclusion (session=%s)", session_id)
    else:
        logger.info(
            "Coordinator conclusion NOT written (action=%s requires re-work)", action
        )


def _extract_findings(report: str) -> str:
    """Extract key findings section from report."""
    # Simple heuristic: look for findings/发现 section
    lines = report.split("\n")
    in_findings = False
    findings_lines = []

    for line in lines:
        if any(kw in line.lower() for kw in ["发现", "findings", "问题", "bug"]):
            in_findings = True
        if in_findings:
            findings_lines.append(line)
            if len(findings_lines) > 20:
                break

    result = "\n".join(findings_lines)[:4000]
    return result or report[:1000]


def _extract_bugs(report: str) -> str:
    """Extract bug list from report."""
    lines = report.split("\n")
    bug_lines = [
        line for line in lines
        if any(kw in line.lower() for kw in ["bug", "缺陷", "异常", "crash", "错误", "fail"])
    ]
    return "\n".join(bug_lines[:30])[:4000] or "None"
