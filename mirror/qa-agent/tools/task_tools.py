"""
QA Agent System — Task Management Tools

Provides task_list and task_update tools for structured task tracking.
Agent uses these to create, view, and progress through a TaskPlan.

The TaskPlan is persisted in agent.session_state.task_plan (Dict).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from agno.tools import tool

from core.task_plan import TERMINAL_STATUSES, TaskPlan
from skills.phase_cache import SkillPhaseCache

logger = logging.getLogger(__name__)

# Valid statuses for task_update
VALID_UPDATE_STATUSES = {"create", "done", "failed", "skipped", "active"}

# Guide shown when no plan exists
NO_PLAN_GUIDE = (
    "No task plan yet. Create one by calling:\n\n"
    '  task_update(task_id=0, status="create", summary=\'{'
    '"goal": "your goal here", '
    '"strategy": "your approach here", '
    '"tasks": ["step 1", "step 2", "step 3"]'
    "}')\n\n"
    "Tips:\n"
    "- goal: What you want to achieve (1 sentence)\n"
    "- strategy: How you plan to achieve it (1-2 sentences)\n"
    "- tasks: Ordered list of 3-10 concrete steps\n"
)

# JSON format example shown on parse error
CREATE_JSON_EXAMPLE = (
    'Expected JSON format in summary:\n'
    '{\n'
    '  "goal": "Verify combat damage calculation",\n'
    '  "strategy": "Read config tables, write test script, execute the script",\n'
    '  "tasks": [\n'
    '    "Read combat config tables to find damage formula",\n'
    '    "Write verification script for damage calculation",\n'
    '    "Execute the test and collect results",\n'
    '    "Analyze results and generate report"\n'
    '  ]\n'
    '}'
)


def _get_task_plan(agent) -> Optional[TaskPlan]:
    """Load TaskPlan from agent session_state, or None."""
    state = getattr(agent, "session_state", None)
    if state is None:
        return None
    plan_dict = getattr(state, "task_plan", None)
    if plan_dict is None:
        return None
    try:
        return TaskPlan.from_dict(plan_dict)
    except Exception as e:
        logger.warning("Failed to deserialize task_plan: %s", e)
        return None


def _save_task_plan(agent, plan: TaskPlan) -> None:
    """Save TaskPlan to agent session_state."""
    state = getattr(agent, "session_state", None)
    if state is None:
        logger.warning("Cannot save task_plan: agent has no session_state")
        return
    state.task_plan = plan.to_dict()


# ═══════════════════════════════════════════════════════════════════
# task_list — Read-only view of current plan
# ═══════════════════════════════════════════════════════════════════


@tool(
    name="task_list",
    description=(
        "View the current task plan with each task's status "
        "(pending/active/done/failed/skipped)."
    ),
)
def task_list(agent) -> str:
    """Return the current TaskPlan status summary.

    Returns:
        Text summary of the plan, or creation guide if none exists.
    """
    plan = _get_task_plan(agent)

    if plan is None:
        return NO_PLAN_GUIDE

    return plan.to_summary()


# ═══════════════════════════════════════════════════════════════════
# task_update — Create plan or update task status
# ═══════════════════════════════════════════════════════════════════


@tool(
    name="task_update",
    description=(
        "Create a task plan or update a task's status. "
        "Create: task_id=0, status='create', summary=<JSON plan>. "
        "Update: task_id=N, status in {active|done|failed|skipped}, summary=<result>. "
        "Optional artifacts=<JSON> persists across compression."
    ),
)
def task_update(
    agent,
    task_id: int,
    status: str,
    summary: str = "",
    artifacts: str = "",
) -> str:
    """Create a plan or update a task's status.

    Args:
        task_id: Task ID to update. Use 0 with status='create' to create a new plan.
        status: New status: 'create', 'done', 'failed', 'skipped', or 'active'.
        summary: For 'create': JSON string with plan definition.
                 For others: brief result summary.
        artifacts: Optional JSON string with structured key-value data from this task's
                   output. E.g. '{"revisions": [2007859], "files": ["items.xlsx"]}'.
                   Persists across interrupts and context compression.

    Returns:
        Confirmation message with updated plan status.
    """
    # ── Validate status ─────────────────────────────────────────────
    if status not in VALID_UPDATE_STATUSES:
        return (
            f"Invalid status '{status}'. "
            f"Valid values: {', '.join(sorted(VALID_UPDATE_STATUSES))}."
        )

    # ── CREATE mode ─────────────────────────────────────────────────
    if task_id == 0 and status == "create":
        return _handle_create(agent, summary)

    # ── UPDATE mode — need existing plan ────────────────────────────
    if status == "create":
        return "To create a plan, use task_id=0 with status='create'."

    plan = _get_task_plan(agent)
    if plan is None:
        return (
            "No task plan exists yet. Create one first:\n"
            '  task_update(task_id=0, status="create", summary=\'{"goal": "...", ...}\')'
        )

    # ── Validate task_id ────────────────────────────────────────────
    try:
        task = plan._get_task(task_id)
    except KeyError:
        valid_ids = [str(t.id) for t in plan.tasks]
        return (
            f"Task ID {task_id} not found. "
            f"Use task_list() to see available tasks. "
            f"Valid IDs: {', '.join(valid_ids)}."
        )

    # ── Parse artifacts (optional) ──────────────────────────────────
    parsed_artifacts = None
    if artifacts and artifacts.strip():
        try:
            parsed_artifacts = json.loads(artifacts)
            if not isinstance(parsed_artifacts, dict):
                logger.warning("artifacts is not a dict, ignoring: %s", type(parsed_artifacts))
                parsed_artifacts = None
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse artifacts JSON (task_id=%d): %s", task_id, e)
            parsed_artifacts = None

    # ── Update the task ─────────────────────────────────────────────
    plan.mark_task(task_id, status, summary, artifacts=parsed_artifacts)  # type: ignore[arg-type]
    _save_task_plan(agent, plan)

    # ── Build response ──────────────────────────────────────────────
    done_count, total = plan.progress
    response_lines = [
        f"✓ Task {task_id} marked as {status}.",
    ]
    if summary:
        response_lines.append(f"  Summary: {summary}")
    if parsed_artifacts:
        artifact_keys = ", ".join(parsed_artifacts.keys())
        response_lines.append(f"  📦 Artifacts saved: [{artifact_keys}]")

    response_lines.append(f"  Progress: {done_count}/{total} tasks complete.")

    if plan.is_complete:
        response_lines.append(
            "\n🎉 All tasks complete! Generate your final summary/report now."
        )
    elif plan.active_task:
        response_lines.append(
            f"\n▶ Next: Task {plan.active_task.id} — {plan.active_task.description}"
        )

    # ── Phase advance (if skill is active) ──────────────────────────
    if status in TERMINAL_STATUSES:
        phase_injection = _maybe_advance_skill_phase(agent, task_id)
        if phase_injection:
            response_lines.append(phase_injection)

    return "\n".join(response_lines)


# ═══════════════════════════════════════════════════════════════════
# Phase advance — inject next Phase on task completion
# ═══════════════════════════════════════════════════════════════════


def _maybe_advance_skill_phase(agent, task_id: int) -> Optional[str]:
    """Check if completing this task should trigger a Skill Phase advance.

    Phase advance happens when:
    - session_state.active_skill_name is set (a Skill is active)
    - task_id maps to the current phase (task_id == active_skill_phase + 1)
    - There is a next Phase in the cache

    Args:
        agent: The Agno Agent instance.
        task_id: The task ID that was just completed.

    Returns:
        Phase injection text if advance happened, None otherwise.
    """
    state = getattr(agent, "session_state", None)
    if state is None:
        return None

    skill_name = getattr(state, "active_skill_name", None)
    if not skill_name:
        return None

    current_phase = getattr(state, "active_skill_phase", 0)
    cache_dict = getattr(state, "skill_phase_cache", None)

    if cache_dict is None:
        return None

    # Check task_id ↔ phase mapping (1-based task_id, 0-based phase)
    if task_id != current_phase + 1:
        return None

    try:
        cache = SkillPhaseCache.from_dict(cache_dict)
    except Exception as e:
        logger.warning("Failed to deserialize SkillPhaseCache: %s", e)
        return None

    next_phase = current_phase + 1

    # ── Last Phase completed → clean up ─────────────────────────────
    if next_phase >= cache.phase_count:
        state.active_skill_name = None
        state.active_skill_phase = 0
        state.skill_phase_cache = None
        logger.info("Skill '%s' all phases complete, clearing state", skill_name)
        return None

    # ── Advance to next Phase ───────────────────────────────────────
    from pathlib import Path

    skill_md_path = Path(cache.skill_base_dir) / "SKILL.md"
    phase_content = cache.get_phase_content(next_phase, skill_md_path)

    if not phase_content:
        logger.warning(
            "Failed to read Phase %d content for skill '%s'",
            next_phase + 1, skill_name,
        )
        return None

    # Update session state
    state.active_skill_phase = next_phase
    cache.current_phase = next_phase
    state.skill_phase_cache = cache.to_dict()

    # Build injection text
    parts = [
        f"\n{'=' * 60}",
        f"## Phase {next_phase + 1} 指令已加载: {cache.phase_titles[next_phase]}",
        f"{'=' * 60}\n",
        phase_content,
    ]

    # Add read prompts
    base_dir = Path(cache.skill_base_dir)
    required = cache.required_reads.get(next_phase, [])
    suggested = cache.suggested_reads.get(next_phase, [])

    if required:
        parts.append("\n⚠️ 请立即读取以下文件（必须在执行本阶段前完成）：")
        for path in required:
            abs_path = (base_dir / path).resolve()
            parts.append(f"- `{abs_path}`")

    if suggested:
        parts.append("\n📖 建议读取以下文件（可按需查阅）：")
        for path in suggested:
            abs_path = (base_dir / path).resolve()
            parts.append(f"- `{abs_path}`")

    parts.append(
        "\n💡 请在 task_update 中通过 artifacts 参数保存本阶段关键产出。"
    )

    logger.info(
        "Advanced skill '%s' to Phase %d (%s)",
        skill_name, next_phase + 1, cache.phase_titles[next_phase],
    )

    return "\n".join(parts)


def _handle_create(agent, summary: str) -> str:
    """Handle task_update(task_id=0, status='create', summary=JSON)."""
    # Check if plan already exists
    existing = _get_task_plan(agent)
    if existing is not None and not existing.is_complete:
        done_count, total = existing.progress
        return (
            f"A task plan already exists ({done_count}/{total} tasks complete). "
            "Complete or skip remaining tasks before creating a new plan. "
            "Use task_list() to see current status."
        )

    # Parse the JSON summary
    if not summary.strip():
        return f"summary parameter is required when creating a plan.\n\n{CREATE_JSON_EXAMPLE}"

    try:
        plan_data = json.loads(summary)
    except json.JSONDecodeError as e:
        return (
            f"Failed to parse plan JSON: {e}\n\n"
            f"{CREATE_JSON_EXAMPLE}"
        )

    # Validate and create TaskPlan
    try:
        plan = TaskPlan.from_create_json(plan_data)
    except (ValueError, KeyError) as e:
        return f"Invalid plan data: {e}\n\n{CREATE_JSON_EXAMPLE}"

    # Validate task count (3-10 recommended)
    if len(plan.tasks) < 2:
        return (
            f"Plan has only {len(plan.tasks)} task(s). "
            "A good plan should have 3-10 concrete steps. "
            "Please provide more detailed steps."
        )
    if len(plan.tasks) > 15:
        return (
            f"Plan has {len(plan.tasks)} tasks — too many. "
            "Consolidate into 3-10 high-level steps. "
            "You can break them down further during execution."
        )

    # Save
    _save_task_plan(agent, plan)

    logger.info(
        "Created task plan: goal='%s', tasks=%d (session=%s)",
        plan.goal, len(plan.tasks),
        getattr(getattr(agent, "session_state", None), "session_id", "?"),
    )

    response = [
        "✅ Task plan created!",
        f"  Goal: {plan.goal}",
        f"  Strategy: {plan.strategy}",
        f"  Tasks: {len(plan.tasks)}",
        "",
    ]

    for task in plan.tasks:
        marker = "▶️" if task.status == "active" else "⬜"
        response.append(f"  {marker} [{task.id}] {task.description}")

    response.append(
        f"\n▶ Starting with Task {plan.active_task.id}: {plan.active_task.description}"
        if plan.active_task else ""
    )

    return "\n".join(response)
