"""
QA Agent System — Interrupt Manager

Central coordinator for all human-in-the-loop interrupt flows.
Uses asyncio.Event to pause Agent execution and resume after human review.

Flow:
  1. Hook calls interrupt_manager.request_interrupt(session_id, payload)
  2. Manager creates asyncio.Event, stores it, sends WebSocket event
  3. Agent's hook awaits event.wait()
  4. Human calls POST /sessions/{id}/review
  5. API calls interrupt_manager.resolve_interrupt(session_id, decision)
  6. Manager sets event, hook reads decision and resumes
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, Optional

logger = logging.getLogger(__name__)


class InterruptType(str, Enum):
    PREVIEW_CONFIRM = "preview_confirm"   # L2: show tool preview, ask confirm
    RESULT_VERIFY = "result_verify"       # L3: show result, ask pass/fail
    PLAN_CONFIRM = "plan_confirm"         # plan mode: confirm execution plan


class InterruptAction(str, Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    MODIFY = "modify"
    PASS = "pass"
    FAIL = "fail"
    RERUN = "rerun"
    INVESTIGATE = "investigate"


@dataclass
class InterruptRecord:
    """State for a single pending interrupt."""
    interrupt_id: str
    session_id: str
    interrupt_type: InterruptType
    payload: Dict[str, Any]
    created_at: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    action: Optional[str] = None          # set by resolve_interrupt
    modified_content: Optional[str] = None
    notes: Optional[str] = None
    resolved_at: Optional[float] = None

    @property
    def is_resolved(self) -> bool:
        return self.event.is_set()

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class InterruptManager:
    """Singleton manager for all session interrupt states.

    Usage in Hook::

        record = await interrupt_manager.request_interrupt(
            session_id="abc",
            interrupt_type=InterruptType.PREVIEW_CONFIRM,
            payload={"tool_name": "bash", "preview": "ls -la"},
        )
        # Hook blocks here until human reviews
        if record.action == InterruptAction.CANCEL:
            raise ToolCancelledError()

    Usage in API::

        interrupt_manager.resolve_interrupt(
            session_id="abc",
            action="confirm",
        )
    """

    def __init__(self, timeout_minutes: int = 30) -> None:
        # session_id -> InterruptRecord
        self._pending: Dict[str, InterruptRecord] = {}
        self._timeout_seconds = timeout_minutes * 60
        # Optional WebSocket push callback: set by API layer
        self._ws_push: Optional[Callable[[str, Dict], Coroutine]] = None
        # Optional session_state lookup: set by API layer for reminder integration
        self._session_state_lookup: Optional[Callable[[str], Any]] = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_ws_push(self, callback: Callable[[str, Dict], Coroutine]) -> None:
        """Register the WebSocket push callback (injected by API layer)."""
        self._ws_push = callback

    # ------------------------------------------------------------------
    # Hook → Manager
    # ------------------------------------------------------------------

    async def request_interrupt(
        self,
        session_id: str,
        interrupt_type: InterruptType,
        payload: Dict[str, Any],
        timeout_override: Optional[int] = None,
    ) -> InterruptRecord:
        """Create an interrupt, push WS notification, and await human decision.

        This method BLOCKS (via asyncio.Event.wait) until:
        - Human calls resolve_interrupt(), OR
        - Timeout expires (auto-resolves with action="timeout")

        Args:
            session_id: The session to interrupt.
            interrupt_type: Type of interrupt (L2 preview / L3 result / plan).
            payload: Data to show the human (tool preview, result summary, etc.)
            timeout_override: Custom timeout in seconds (overrides global default).

        Returns:
            The resolved InterruptRecord with `action` and `modified_content` set.
        """
        interrupt_id = str(uuid.uuid4())

        # M4 fix: detect duplicate request for same session.
        # If an unresolved record exists, reuse it (await same event) instead of
        # overwriting — otherwise the original hook would hang forever.
        existing = self._pending.get(session_id)
        if existing is not None and not existing.is_resolved:
            logger.error(
                "request_interrupt: duplicate request for session=%s "
                "(existing interrupt_id=%s type=%s, new_type=%s) — "
                "REUSING existing record, NOT pushing new WS event",
                session_id, existing.interrupt_id,
                existing.interrupt_type.value, interrupt_type.value,
            )
            # Await the existing record's resolution; do not push a new WS event
            # (frontend is already showing the original interrupt).
            timeout = timeout_override or self._timeout_seconds
            try:
                await asyncio.wait_for(existing.event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # The original request_interrupt() handles timeout/cleanup;
                # we just observe the resolved state here.
                pass
            return existing

        record = InterruptRecord(
            interrupt_id=interrupt_id,
            session_id=session_id,
            interrupt_type=interrupt_type,
            payload=payload,
            created_at=time.time(),
        )
        self._pending[session_id] = record
        logger.debug(
            "request_interrupt: new record created session=%s interrupt_id=%s type=%s",
            session_id, interrupt_id, interrupt_type.value,
        )

        # ── task 8.1: persist to MongoDB asynchronously ─────────────────
        asyncio.create_task(
            self._persist_interrupt(record),
            name=f"persist_interrupt_{interrupt_id}",
        )

        # Build WebSocket event
        ws_event = {
            "event_type": "interrupt_request",
            "interrupt_id": interrupt_id,
            "session_id": session_id,
            "interrupt_type": interrupt_type.value,
            "payload": payload,
            "timestamp": record.created_at,
            "actions": self._allowed_actions(interrupt_type),
        }

        # Push to WebSocket if callback is registered
        if self._ws_push:
            try:
                await self._ws_push(session_id, ws_event)
            except Exception:
                logger.exception("Failed to push interrupt_request to WebSocket (session=%s)", session_id)

        # Wait for human decision (with timeout)
        timeout = timeout_override or self._timeout_seconds
        logger.info(
            "Interrupt requested (session=%s, type=%s, id=%s, timeout=%ds)",
            session_id, interrupt_type.value, interrupt_id, timeout,
        )

        try:
            await asyncio.wait_for(record.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Interrupt timed out (session=%s, id=%s)", session_id, interrupt_id)
            record.action = "timeout"
            record.resolved_at = time.time()
            record.event.set()
            # Notify frontend that this interrupt has expired
            if self._ws_push:
                try:
                    await self._ws_push(session_id, {
                        "event_type": "interrupt_timeout",
                        "interrupt_id": interrupt_id,
                        "session_id": session_id,
                        "interrupt_type": interrupt_type.value,
                        "message": "验收等待超时，任务已自动标记为完成。",
                    })
                except Exception:
                    logger.exception(
                        "Failed to push interrupt_timeout to WebSocket (session=%s)", session_id
                    )

        self._pending.pop(session_id, None)
        return record

    # ------------------------------------------------------------------
    # API → Manager
    # ------------------------------------------------------------------

    def resolve_interrupt(
        self,
        session_id: str,
        action: str,
        modified_content: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """Resolve a pending interrupt and unblock the waiting hook.

        Args:
            session_id: The session to resolve.
            action: One of confirm/cancel/modify/pass/fail/rerun/investigate.
            modified_content: Used when action="modify" to replace tool args.
            notes: Optional human notes attached to the review.

        Returns:
            True if an interrupt was found and resolved, False if no pending interrupt.
        """
        record = self._pending.get(session_id)
        if record is None:
            logger.warning("resolve_interrupt: no pending interrupt for session %s", session_id)
            return False

        record.action = action
        record.modified_content = modified_content
        record.notes = notes
        record.resolved_at = time.time()
        record.event.set()  # unblock the awaiting hook

        # ── task 8.2: update MongoDB checkpoint status asynchronously ───
        asyncio.create_task(
            self._update_interrupt_db(session_id, action, notes),
            name=f"update_interrupt_db_{session_id}",
        )

        # Set System Reminder flag for post-interrupt context recovery
        self._set_reminder_pending(session_id, reason="interrupt")

        logger.info(
            "Interrupt resolved (session=%s, action=%s, notes=%s)",
            session_id, action, notes,
        )
        return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_pending(self, session_id: str) -> Optional[InterruptRecord]:
        """Return the current pending interrupt for a session, or None."""
        return self._pending.get(session_id)

    def has_pending(self, session_id: str) -> bool:
        return session_id in self._pending

    def pending_payload(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return the payload of the pending interrupt for API status responses."""
        record = self._pending.get(session_id)
        if record is None:
            return None
        return {
            "interrupt_id": record.interrupt_id,
            "interrupt_type": record.interrupt_type.value,
            "payload": record.payload,
            "created_at": record.created_at,
            "age_seconds": record.age_seconds,
            "actions": self._allowed_actions(record.interrupt_type),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_reminder_pending(self, session_id: str, reason: str) -> None:
        """Set the _reminder_pending flag on the session's state.

        This triggers the reminder_pre_hook to inject a System Reminder
        on the Agent's next turn after interrupt resolution.

        Note: This requires a session_state lookup mechanism. The callback
        is set by the API layer via set_session_state_lookup().
        """
        if self._session_state_lookup:
            try:
                state = self._session_state_lookup(session_id)
                if state is not None:
                    state._reminder_pending = True
                    state._reminder_reason = reason
            except Exception:
                logger.warning(
                    "Failed to set reminder_pending for session %s",
                    session_id, exc_info=True,
                )

    def set_session_state_lookup(
        self, callback: Callable[[str], Any]
    ) -> None:
        """Register a callback to look up session_state by session_id.

        Args:
            callback: Function that takes session_id and returns QASessionState or None.
        """
        self._session_state_lookup = callback

    @staticmethod
    def _allowed_actions(interrupt_type: InterruptType) -> list[str]:
        if interrupt_type == InterruptType.PREVIEW_CONFIRM:
            return ["confirm", "modify", "cancel"]
        elif interrupt_type == InterruptType.RESULT_VERIFY:
            return ["pass", "fail", "rerun", "investigate"]
        elif interrupt_type == InterruptType.PLAN_CONFIRM:
            return ["confirm", "cancel"]
        return ["confirm", "cancel"]

    # ------------------------------------------------------------------
    # MongoDB persistence helpers  (tasks 8.1–8.3)
    # ------------------------------------------------------------------

    async def _persist_interrupt(self, record: InterruptRecord) -> None:
        """Write a new interrupt record to MongoDB (best-effort, non-blocking)."""
        try:
            from db.models import QaPhaseCheckpoint, CheckpointStatus

            # We use QaPhaseCheckpoint as the persistence store for interrupt state.
            # Only persist RESULT_VERIFY type (phase-level reviews worth persisting).
            if record.interrupt_type != InterruptType.RESULT_VERIFY:
                return

            existing = await QaPhaseCheckpoint.find_one(
                QaPhaseCheckpoint.session_id == record.session_id,
                QaPhaseCheckpoint.status == CheckpointStatus.AWAITING_REVIEW,
            )
            if existing is not None:
                # Checkpoint already written by request_review tool — just log
                logger.debug(
                    "_persist_interrupt: checkpoint already exists for session %s",
                    record.session_id,
                )
                return

            # BUG-FIX: Previously no checkpoint was created here, causing all
            # downstream _update_interrupt_db writes to find nothing and silently
            # skip.  Now we insert a checkpoint so that resolve-time updates
            # (pass/fail/rerun/investigate) can locate and update it.
            checkpoint = QaPhaseCheckpoint(
                session_id=record.session_id,
                phase=record.payload.get("phase", "result_verify"),
                phase_index=0,
                status=CheckpointStatus.AWAITING_REVIEW,
                artifact_id=record.payload.get("artifact_id"),
                artifact_type=record.payload.get("artifact_type", "result_verify"),
                llm_summary=record.payload.get("result_summary", record.payload.get("summary", "")),
                review_questions=record.payload.get("review_questions", []),
                resume_hint=record.payload.get("result_summary", record.payload.get("summary", "")),
            )
            await checkpoint.insert()
            logger.info(
                "_persist_interrupt: checkpoint created for session %s (interrupt_id=%s)",
                record.session_id, record.interrupt_id,
            )
        except Exception:
            logger.warning(
                "_persist_interrupt: failed for session %s", record.session_id, exc_info=True
            )

    async def _update_interrupt_db(
        self,
        session_id: str,
        action: str,
        notes: Optional[str],
    ) -> None:
        """Update the MongoDB checkpoint status after an interrupt is resolved (task 8.2)."""
        try:
            from datetime import datetime
            from db.models import QaPhaseCheckpoint, CheckpointStatus

            checkpoint = await QaPhaseCheckpoint.find_one(
                QaPhaseCheckpoint.session_id == session_id,
                QaPhaseCheckpoint.status == CheckpointStatus.AWAITING_REVIEW,
            )
            if checkpoint is None:
                return

            approved = action in ("pass", "confirm", "timeout")
            checkpoint.review_approved = approved
            checkpoint.reviewer_feedback = notes or ""
            checkpoint.status = (
                CheckpointStatus.COMPLETED if approved else CheckpointStatus.RUNNING
            )
            checkpoint.updated_at = datetime.utcnow()
            await checkpoint.save()
        except Exception:
            logger.warning(
                "_update_interrupt_db: failed for session %s", session_id, exc_info=True
            )

    async def load_pending_from_db(self) -> None:
        """On startup, scan MongoDB for non-terminal checkpoints (tasks 8.3, 12.1).

        Scans running, awaiting_review, and interrupted statuses.
        Logs each session so operators / frontend can prompt users to resume.
        Does NOT recreate asyncio.Events (those would be dead — the process restarted).
        """
        try:
            from db.models import QaPhaseCheckpoint, CheckpointStatus

            # task 12.1: scan all non-terminal statuses
            non_terminal_statuses = [
                CheckpointStatus.RUNNING,
                CheckpointStatus.AWAITING_REVIEW,
                CheckpointStatus.INTERRUPTED,
            ]

            pending = await QaPhaseCheckpoint.find(
                {"status": {"$in": [s.value for s in non_terminal_statuses]}}
            ).to_list()

            if not pending:
                logger.info("load_pending_from_db: no non-terminal checkpoints found on startup")
                return

            # task 12.2: log each and optionally emit recovery event
            for cp in pending:
                logger.info(
                    "load_pending_from_db: session=%s phase=%s status=%s "
                    "(artifact_id=%s) — can be resumed via POST /sessions/%s/resume",
                    cp.session_id, cp.phase, cp.status.value,
                    cp.artifact_id, cp.session_id,
                )
        except Exception:
            logger.warning("load_pending_from_db: failed to scan MongoDB", exc_info=True)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
interrupt_manager = InterruptManager()
