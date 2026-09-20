"""
QA Agent System — Review Gate Tool (tasks 4.1–4.5)

Provides request_review tool: LLM calls this when SKILL.md instructs it to
request human review of a phase artifact. The tool blocks until the reviewer
submits a decision via POST /sessions/{id}/resume.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agno.tools import tool

logger = logging.getLogger(__name__)


class TaskAbortedError(Exception):
    """Raised when a session is aborted while waiting for review."""


@tool(
    name="request_review",
    description=(
        "Request human review of a phase artifact and pause until verdict. "
        "Returns {approved: bool, reviewer_feedback: str}. "
        "On approved=False, revise based on feedback and call again. "
        "Use only when the active Skill phase requires human sign-off."
    ),
)
async def request_review(
    agent,
    phase: str,
    artifact_type: str,
    artifact_id: str,
    summary: str,
    review_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Block the Agent and wait for a human reviewer to approve or reject the artifact.

    Args:
        phase: Name of the current Skill phase (e.g. "test_case_generation").
        artifact_type: Type of artifact (e.g. "test_cases", "automation_script").
        artifact_id: ObjectId string returned by save_artifact.
        summary: Human-readable summary of the artifact for the reviewer.
        review_questions: Optional list of specific questions for the reviewer.

    Returns:
        Dict with keys:
          - status: "review_completed"
          - approved: True / False
          - reviewer_feedback: str (reviewer notes or empty string)

    Raises:
        TaskAbortedError: If the session is aborted while waiting for review.
    """
    from db.models import CheckpointStatus, QaPhaseCheckpoint
    from hooks.interrupt_manager import InterruptType, interrupt_manager

    state = getattr(agent, "session_state", None)
    session_id: str = getattr(state, "session_id", "") if state else ""
    task_id: str = getattr(state, "task_id", "") if state else ""
    phase_index: int = getattr(state, "active_skill_phase", 0) if state else 0

    # ── 4.2: write checkpoint → awaiting_review ─────────────────────────────
    checkpoint = QaPhaseCheckpoint(
        task_id=task_id or None,
        session_id=session_id,
        phase=phase,
        phase_index=phase_index,
        status=CheckpointStatus.AWAITING_REVIEW,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        llm_summary=summary,
        review_questions=review_questions or [],
        resume_hint=summary,
    )
    try:
        await checkpoint.insert()
        logger.info(
            "request_review: checkpoint created (session=%s, phase=%s, artifact=%s)",
            session_id, phase, artifact_id,
        )
    except Exception:
        logger.exception("request_review: failed to write checkpoint — continuing anyway")

    # ── 4.3: block on interrupt_manager (also listens for abort) ────────────
    try:
        record = await interrupt_manager.request_interrupt(
            session_id=session_id,
            interrupt_type=InterruptType.RESULT_VERIFY,
            payload={
                "phase": phase,
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                "summary": summary,
                "review_questions": review_questions or [],
            },
        )
    except TaskAbortedError:
        # ── 4.5: abort path — update checkpoint and re-raise ────────────────
        try:
            await QaPhaseCheckpoint.find_one(
                QaPhaseCheckpoint.session_id == session_id,
                QaPhaseCheckpoint.status == CheckpointStatus.AWAITING_REVIEW,
            ).update({"$set": {"status": CheckpointStatus.ABORTED.value}})
        except Exception:
            pass
        raise

    action = record.action or "timeout"
    feedback = record.notes or ""

    # ── 4.4: build result for LLM ────────────────────────────────────────────
    if action in ("pass", "timeout", "confirm"):
        # Update checkpoint to completed
        try:
            await QaPhaseCheckpoint.find_one(
                QaPhaseCheckpoint.session_id == session_id,
                QaPhaseCheckpoint.status == CheckpointStatus.AWAITING_REVIEW,
            ).update({"$set": {
                "status": CheckpointStatus.COMPLETED.value,
                "review_approved": True,
                "reviewer_feedback": feedback,
            }})
        except Exception:
            pass

        return {
            "status": "review_completed",
            "approved": True,
            "reviewer_feedback": feedback or "审核通过。",
        }

    elif action in ("fail", "rerun"):
        try:
            await QaPhaseCheckpoint.find_one(
                QaPhaseCheckpoint.session_id == session_id,
                QaPhaseCheckpoint.status == CheckpointStatus.AWAITING_REVIEW,
            ).update({"$set": {
                "status": CheckpointStatus.RUNNING.value,  # back to running for redo
                "review_approved": False,
                "reviewer_feedback": feedback,
            }})
        except Exception:
            pass

        return {
            "status": "review_completed",
            "approved": False,
            "reviewer_feedback": feedback or "审核未通过，请根据反馈重新执行本阶段。",
        }

    else:
        # modify / investigate — treat as approved with notes
        try:
            await QaPhaseCheckpoint.find_one(
                QaPhaseCheckpoint.session_id == session_id,
                QaPhaseCheckpoint.status == CheckpointStatus.AWAITING_REVIEW,
            ).update({"$set": {
                "status": CheckpointStatus.COMPLETED.value,
                "review_approved": True,
                "reviewer_feedback": feedback,
            }})
        except Exception:
            pass

        return {
            "status": "review_completed",
            "approved": True,
            "reviewer_feedback": feedback or "审核通过（含修改意见，请参考反馈）。",
        }
