"""
QA Agent System — System Reminder Hook

Generates and injects a <system-reminder> message into the Agent's message
history after Interrupt recovery or Autocompact, ensuring the Agent retains
awareness of current Skill phase, task progress, and prior artifacts.

Trigger sources:
1. interrupt_manager.resolve_interrupt() → sets _reminder_pending = True
2. context_threshold_hook._distill_and_write() → sets _reminder_pending = True

Injection point:
- reminder_pre_hook runs at the start of each Agent turn
- If _reminder_pending is True, generates and injects reminder, then clears flag
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
event_logger = logging.getLogger("qa_agent.events")


def generate_system_reminder(session_state) -> str:
    """Generate a <system-reminder> block from current session state.

    Reads TaskPlan progress, active Skill name/phase, and completed task
    artifacts to build a structured recovery message.

    Args:
        session_state: The QASessionState instance.

    Returns:
        Formatted reminder string wrapped in <system-reminder> tags,
        or empty string if no reminder is needed.
    """
    from core.task_plan import TaskPlan

    # Check if there's a TaskPlan to report on
    plan_dict = getattr(session_state, "task_plan", None)
    if plan_dict is None:
        return ""

    try:
        plan = TaskPlan.from_dict(plan_dict)
    except Exception:
        return ""

    if not plan.tasks:
        return ""

    # Gather state info
    skill_name = getattr(session_state, "active_skill_name", None)
    skill_phase = getattr(session_state, "active_skill_phase", 0)
    reminder_reason = getattr(session_state, "_reminder_reason", None)

    # Get phase title from cache if available
    phase_title = ""
    cache_dict = getattr(session_state, "skill_phase_cache", None)
    if cache_dict and isinstance(cache_dict, dict):
        titles = cache_dict.get("phase_titles", [])
        if skill_phase < len(titles):
            phase_title = titles[skill_phase]

    # Build reminder content
    lines = ["<system-reminder>", "## 会话状态恢复", ""]

    # Skill info
    if skill_name:
        phase_desc = f"Phase {skill_phase + 1}"
        if phase_title:
            phase_desc += f" — {phase_title}"
        lines.append(f"**活跃 Skill**: {skill_name}")
        lines.append(f"**当前阶段**: {phase_desc}")
        lines.append("")

    # Task progress
    done_count, total = plan.progress
    lines.append(f"**任务进度**: {done_count}/{total} 已完成")
    lines.append("")

    # Artifacts summary from completed tasks
    artifacts_text = _format_artifacts_summary(plan.tasks)
    if artifacts_text:
        lines.append("**前序产出**:")
        lines.append(artifacts_text)
        lines.append("")

    # Next action
    if plan.active_task:
        lines.append(f"**下一步**: Task {plan.active_task.id} — {plan.active_task.description}")
        lines.append("")

    # Compact-specific guidance
    if reminder_reason == "compact":
        lines.append(
            "> 上下文已压缩，请基于以上状态信息继续执行，"
            "无需重新询问用户已确认的信息"
        )
        lines.append("")

        # Append activated Skill recovery info on compact
        activated = getattr(session_state, "activated_skills", None) or []
        if activated:
            summaries = getattr(session_state, "skill_summaries", {}) or {}
            lines.append("**已激活 Skills**:")
            for name in activated:
                summary = summaries.get(name, name)
                lines.append(f"- {name}: {summary}")
            lines.append("")
            lines.append(
                "> 以上 Skill 指令在首次激活时已提供，"
                "请基于已有上下文继续执行"
            )
            lines.append("")

    lines.append("</system-reminder>")
    return "\n".join(lines)


def _format_artifacts_summary(tasks: list) -> str:
    """Format artifacts from completed tasks into a readable summary.

    For each completed task with artifacts, outputs key: value pairs.
    Falls back to result_summary if no artifacts exist.

    Args:
        tasks: List of TaskItem instances.

    Returns:
        Formatted string, or empty string if nothing to show.
    """
    lines = []

    for task in tasks:
        if task.status not in ("done", "failed", "skipped"):
            continue

        if task.artifacts:
            for key, value in task.artifacts.items():
                v_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
                if len(v_str) > 200:
                    v_str = v_str[:185] + "...(truncated)"
                lines.append(f"  - {key}: {v_str}")
        elif task.result_summary:
            lines.append(f"  - Task {task.id}: {task.result_summary}")

    return "\n".join(lines)


def _generate_skill_reminder(session_state) -> str:
    """Generate a <skill-reminder> block from activated Skills.

    Args:
        session_state: The QASessionState instance.

    Returns:
        Formatted <skill-reminder> string, or empty string if no skills activated.
    """
    activated = getattr(session_state, "activated_skills", None) or []
    if not activated:
        return ""

    summaries = getattr(session_state, "skill_summaries", {}) or {}
    lines = ["<skill-reminder>", "当前激活的 Skills:"]
    for name in activated:
        summary = summaries.get(name, name)
        lines.append(f"- {name}: {summary}")
    lines.append("</skill-reminder>")
    return "\n".join(lines)


async def reminder_pre_hook(agent: Any, run_input: Any) -> None:
    """Pre-hook: inject System Reminder if _reminder_pending is True.

    Also injects Checkpoint recovery context (tasks 9.1–9.3),
    artifact context from context_ids (task 9.4),
    and Skill activation reminders on every run start.

    Args:
        agent: The Agno Agent instance.
        run_input: The input for this Agent run (Agno hook convention).
    """
    state = getattr(agent, "session_state", None)
    if state is None:
        return

    session_id: str = getattr(state, "session_id", "") or ""

    # ── task 9.1–9.3: inject Checkpoint recovery context ────────────────
    await _maybe_inject_checkpoint_reminder(agent, state, session_id)

    # ── task 9.4: inject context_ids artifacts ───────────────────────────
    await _maybe_inject_artifact_context(agent, state)

    # ── Skill activation reminder ────────────────────────────────────────
    _maybe_inject_skill_reminder(agent, state, session_id)

    # ── existing: inject system reminder if _reminder_pending ────────────
    if not getattr(state, "_reminder_pending", False):
        return

    # Generate reminder
    reminder_text = generate_system_reminder(state)

    if not reminder_text:
        # Clear flag even if nothing to inject
        state._reminder_pending = False
        state._reminder_reason = None
        return

    # Inject into Agent message history
    try:
        _inject_system_message(agent, reminder_text)
        reason = getattr(state, "_reminder_reason", "unknown")
        logger.info(
            "Injected system reminder (reason=%s, session=%s, %d chars)",
            reason,
            getattr(state, "session_id", "?"),
            len(reminder_text),
        )
        event_logger.info(
            "📦 [压缩] 恢复提醒注入: reason=%s session=%s chars=%d",
            reason,
            getattr(state, "session_id", "?"),
            len(reminder_text),
        )
    except Exception:
        logger.warning(
            "Failed to inject system reminder (session=%s)",
            getattr(state, "session_id", "?"),
            exc_info=True,
        )

    # Clear flag
    state._reminder_pending = False
    state._reminder_reason = None


# ---------------------------------------------------------------------------
# Skill activation reminder injection
# ---------------------------------------------------------------------------

def _maybe_inject_skill_reminder(agent: Any, state: Any, session_id: str) -> None:
    """Inject <skill-reminder> if Skills are activated and this is not an activation turn."""
    # Skip if no activated skills
    activated = getattr(state, "activated_skills", None) or []
    if not activated:
        return

    # Skip if this is the turn that just activated skills (full prompt already in user_content)
    if getattr(state, "_skill_just_activated", False):
        state._skill_just_activated = False
        return

    # Generate and inject skill reminder
    reminder_text = _generate_skill_reminder(state)
    if reminder_text:
        try:
            _inject_system_message(agent, reminder_text)
            logger.debug(
                "Injected skill reminder (session=%s, skills=%s)",
                session_id, activated,
            )
        except Exception:
            logger.warning(
                "_maybe_inject_skill_reminder: failed (session=%s)",
                session_id, exc_info=True,
            )


# ---------------------------------------------------------------------------
# Checkpoint recovery injection  (tasks 9.1–9.3)
# ---------------------------------------------------------------------------

async def _maybe_inject_checkpoint_reminder(
    agent: Any, state: Any, session_id: str
) -> None:
    """Check MongoDB for a non-terminal checkpoint and inject recovery context."""
    if not session_id:
        return

    # Only inject once per run (flag _checkpoint_injected on state)
    if getattr(state, "_checkpoint_injected", False):
        return

    try:
        from db.models import CheckpointStatus, QaPhaseCheckpoint

        checkpoint = await QaPhaseCheckpoint.find_latest(session_id)
        if checkpoint is None:
            return
        if checkpoint.status not in (
            CheckpointStatus.INTERRUPTED,
            CheckpointStatus.AWAITING_REVIEW,
        ):
            return

        # ── task 9.2: build reminder text ─────────────────────────────
        reminder = _build_checkpoint_reminder(checkpoint)
        if reminder:
            _inject_system_message(agent, reminder)
            logger.info(
                "Injected checkpoint reminder (session=%s, phase=%s, status=%s)",
                session_id, checkpoint.phase, checkpoint.status.value,
            )

        # Mark as injected so we don't inject again on the same run
        state._checkpoint_injected = True

    except Exception:
        logger.warning(
            "_maybe_inject_checkpoint_reminder: failed (session=%s)",
            session_id, exc_info=True,
        )


def _build_checkpoint_reminder(checkpoint) -> str:
    """Build a <system-reminder> block from a QaPhaseCheckpoint."""
    lines = [
        "<system-reminder>",
        "## 任务恢复通知",
        "",
        f"**当前阶段**: {checkpoint.phase}（状态: {checkpoint.status.value}）",
    ]

    if checkpoint.completed_phases:
        lines.append(f"**已完成阶段**: {', '.join(checkpoint.completed_phases)}")

    if checkpoint.artifact_type and checkpoint.artifact_id:
        lines.append(f"**待审核产出**: {checkpoint.artifact_type}（artifact_id={checkpoint.artifact_id}）")

    if checkpoint.llm_summary:
        lines.append(f"**产出摘要**: {checkpoint.llm_summary}")

    if checkpoint.reviewer_feedback:
        lines.append(f"**审核反馈**: {checkpoint.reviewer_feedback}")

    lines += [
        "",
        "请根据以上上下文继续执行任务，无需重新询问已确认的信息。",
        "</system-reminder>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Artifact context injection  (task 9.4)
# ---------------------------------------------------------------------------

async def _maybe_inject_artifact_context(agent: Any, state: Any) -> None:
    """If session_state.context_ids is set, load artifact summaries and inject."""
    context_ids: list = getattr(state, "context_ids", None) or []
    if not context_ids:
        return

    # Only inject once per run
    if getattr(state, "_artifact_context_injected", False):
        return

    try:
        from tools.artifact_tools import load_artifact_raw

        summaries = []
        for aid in context_ids:
            artifact = await load_artifact_raw(aid)
            if artifact is None:
                logger.warning(
                    "_maybe_inject_artifact_context: artifact %s not found — skipping", aid
                )
                continue
            summary = artifact.get("content_summary") or ""
            artifact_type = artifact.get("artifact_type", "unknown")
            summaries.append(f"- [{artifact_type}] (id={aid}): {summary}")

        if summaries:
            context_text = (
                "<context>\n"
                "## 上游产出（来自 context_ids）\n\n"
                + "\n".join(summaries)
                + "\n\n如需完整内容，请使用 load_artifact(artifact_id=...) 工具。\n"
                "</context>"
            )
            _inject_system_message(agent, context_text)
            logger.info(
                "_maybe_inject_artifact_context: injected %d artifacts (session=%s)",
                len(summaries), getattr(state, "session_id", "?"),
            )

        state._artifact_context_injected = True

    except Exception:
        logger.warning(
            "_maybe_inject_artifact_context: failed (session=%s)",
            getattr(state, "session_id", "?"), exc_info=True,
        )


def _inject_system_message(agent: Any, content: str) -> None:
    """Inject a system message into the Agent's message history.

    Inserts before the last user message if one exists,
    otherwise appends to the end.

    Args:
        agent: The Agno Agent instance.
        content: The message content to inject.
    """
    # Access Agno's memory/message store
    memory = getattr(agent, "memory", None)
    if memory is None:
        return

    messages = getattr(memory, "messages", None)
    if messages is None:
        return

    # Build a system message dict (Agno convention)
    from agno.models.message import Message

    reminder_msg = Message(role="system", content=content)

    # Find the last user message and insert before it
    insert_idx = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role == "user":
            insert_idx = i
            break

    messages.insert(insert_idx, reminder_msg)
