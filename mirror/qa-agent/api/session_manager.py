"""
QA Agent System — Session Manager

Manages agent session lifecycle:
  - session_id → (agent/team instance, status, metadata)
  - State machine: idle → running → interrupt_pending → running → completed/failed
  - Timeout cleanup for stale interrupt sessions
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from api.schemas import AgentMode, SessionStatus

logger = logging.getLogger(__name__)


@dataclass
class SessionRecord:
    """All state for a single agent session."""
    session_id: str
    agent_name: str
    mode: str
    status: SessionStatus
    game_version: str = ""
    module: str = ""
    email: str = ""              #归属用户 email（用于级联删除和 user_id 绑定）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    agent: Optional[Any] = None     # Agno Agent or Team instance
    error: Optional[str] = None
    _ws_queues: List[asyncio.Queue] = field(default_factory=list)
    _task: Optional[asyncio.Task] = None  # background agent task handle
    # Truncate idempotency slot — last truncate outcome keyed by request_id.
    # In-memory only; a restart loses it (a retry then 404s / acts on the new state).
    last_truncate_outcome: Optional[Dict[str, Any]] = None
    # harden-truncate-session-lock: set (under _lock) while a truncate's storage
    # IO is in progress; try_set_running rejects run starts so no run can append
    # messages into a section that is about to be truncated away.
    truncating: bool = False

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_status_dict(self, interrupt_payload: Optional[Dict] = None) -> Dict:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "agent_name": self.agent_name,
            "mode": self.mode,
            "game_version": self.game_version,
            "module": self.module,
            "interrupt_payload": interrupt_payload,
            "error": self.error,
        }


class SessionManager:
    """Thread-safe session lifecycle manager."""

    def __init__(self, interrupt_timeout_minutes: int = 30) -> None:
        self._sessions: Dict[str, SessionRecord] = {}
        self._timeout_seconds = interrupt_timeout_minutes * 60
        self._lock = asyncio.Lock()
        # Abort event state
        self._abort_events: Dict[str, asyncio.Event] = {}
        # Per-session locks for storage-IO critical sections (e.g. truncate).
        # The global _lock must never be held across storage IO; per-session
        # locks serialize work
        # within one session without stalling other sessions' lifecycle ops.
        self._session_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Locking
    # ------------------------------------------------------------------

    def session_lock(self, session_id: str) -> asyncio.Lock:
        """Get (lazily create) the per-session lock for storage-IO sections.

        Lock ordering: acquire this BEFORE ``self._lock``, never the reverse —
        callers awaiting this lock may afterwards take ``_lock`` (e.g.
        ``get_if_present``), so nothing may hold ``_lock`` while awaiting a
        session lock. Creation uses a sync ``setdefault``: the event loop is
        single-threaded and there is no await between get and set, so the
        first-acquire race the design worried about cannot occur.
        """
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def get_if_present(self, session_id: str) -> Optional[SessionRecord]:
        """Atomically read a record while confirming it still exists.

        Used inside a per-session lock to re-fetch the record before storage
        IO — the session may have been deleted (``_sessions`` entry removed
        under ``_lock``) between lock acquisition and re-validation.
        """
        async with self._lock:
            return self._sessions.get(session_id)

    # ------------------------------------------------------------------
    # Create / Delete
    # ------------------------------------------------------------------

    async def create_session(
        self,
        agent_name: str,
        mode: str = "normal",
        game_version: str = "",
        module: str = "",
        email: str = "",
    ) -> str:
        """Create a new session record and return session_id."""
        session_id = str(uuid.uuid4())
        record = SessionRecord(
            session_id=session_id,
            agent_name=agent_name,
            mode=mode,
            status=SessionStatus.IDLE,
            game_version=game_version,
            module=module,
            email=email,
        )
        async with self._lock:
            self._sessions[session_id] = record

        logger.info("Session created: %s (agent=%s, mode=%s)", session_id, agent_name, mode)
        return session_id

    async def attach_agent(self, session_id: str, agent: Any) -> None:
        """Attach an Agent/Team instance to a session."""
        record = self._get(session_id)
        if record:
            record.agent = agent
            record.touch()

    async def register_existing(
        self,
        session_id: str,
        agent: Any,
        meta: Dict[str, Any],
    ) -> SessionRecord:
        """Register an already-existing session (e.g. restored from persistence).

        Unlike create_session(), this method:
        - Uses the provided session_id instead of generating a new UUID
        - Does NOT trigger any MongoDB write
        - Is idempotent: if the session_id already exists, returns the existing record

        Args:
            session_id: The original session UUID.
            agent: Reconstructed Agno Agent instance (already has history loaded).
            meta: Dict with keys agent_name, mode, game_version, module.

        Returns:
            The SessionRecord for this session.
        """
        async with self._lock:
            if session_id in self._sessions:
                logger.debug("register_existing: session %s already active, skipping", session_id)
                return self._sessions[session_id]

            record = SessionRecord(
                session_id=session_id,
                agent_name=meta.get("agent_name", ""),
                mode=meta.get("mode", "normal"),
                status=SessionStatus.IDLE,
                game_version=meta.get("game_version", ""),
                module=meta.get("module", ""),
                email=meta.get("email", ""),
                agent=agent,
            )
            self._sessions[session_id] = record

        logger.info(
            "Session restored: %s (agent=%s, mode=%s)",
            session_id, meta.get("agent_name"), meta.get("mode"),
        )
        return record

    async def remove_from_memory(self, session_id: str) -> bool:
        """Fast in-memory removal for the delete-user-session hot path.

        Sets the abort signal, cancels the running task (without waiting for
        the grace period — the cancelled task's teardown continues in the
        background), drops the SessionRecord and the abort event. Does NOT
        touch persisted storage (event store / seq watermark); the caller
        owns the cascade cleanup.

        "删除会话接口 MUST 快速返回".
        """
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False

            # If a background task is still running, signal + cancel it.
            # No grace wait here — teardown continues in the background.
            if record._task is not None and not record._task.done():
                abort_ev = self._abort_events.get(session_id)
                if abort_ev is not None:
                    abort_ev.set()
                record._task.cancel()

            del self._sessions[session_id]

        self._abort_events.pop(session_id, None)
        # Drop the per-session lock entry — a truncate holding the old lock
        # object finishes alone; new entrants re-create via get_if_present 404.
        self._session_locks.pop(session_id, None)
        logger.info("Session removed from memory: %s", session_id)
        return True

    async def delete_session(self, session_id: str) -> bool:
        """Remove a session record.

        If the session has a running agent task, it will be cancelled first
        (via abort signal + task.cancel) with a short grace period.
        Also cleans up abort events, resume contexts, and event store.
        """
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False

            # If a background task is still running, cancel it gracefully
            if record._task is not None and not record._task.done():
                # Set abort signal so stream_adapter exits its loop
                abort_ev = self._abort_events.get(session_id)
                if abort_ev is not None:
                    abort_ev.set()
                # Cancel the asyncio task
                record._task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(record._task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass  # best-effort

            del self._sessions[session_id]

        # Cleanup outside the lock to avoid holding it during I/O
        self._abort_events.pop(session_id, None)
        # Drop the per-session lock entry (see remove_from_memory).
        self._session_locks.pop(session_id, None)

        # Clear event store (both memory and MongoDB persisted events)
        try:
            from api.event_store import event_store
            await event_store.clear_persisted(session_id)
        except Exception:
            logger.warning(
                "delete_session: failed to clear persisted events (session=%s)",
                session_id, exc_info=True,
            )
        # clear_persisted re-writes the seq watermark;
        # remove it so a deleted session leaves no orphan watermark behind.
        try:
            from db.models import SessionSeqWatermark

            await SessionSeqWatermark.find(
                SessionSeqWatermark.session_id == session_id
            ).delete()
        except Exception:
            logger.warning(
                "delete_session: failed to delete seq watermark (session=%s)",
                session_id, exc_info=True,
            )

        logger.info("Session deleted: %s", session_id)
        return True

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    async def set_running(self, session_id: str) -> None:
        record = self._get(session_id)
        if record:
            record.status = SessionStatus.RUNNING
            record.touch()

    async def try_set_running(self, session_id: str) -> bool:
        """Atomically check-and-set session to RUNNING.

        Returns True if the transition succeeded (session was idle/completed/failed),
        False if the session is already running or in interrupt_pending state.
        This prevents duplicate concurrent runs caused by race conditions on
        simultaneous POST /messages requests.
        """
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False
            if record.status == SessionStatus.RUNNING:
                return False
            if record.status == SessionStatus.INTERRUPT_PENDING:
                return False
            if record.truncating:
                # Truncate IO in progress (harden-truncate-session-lock): a run
                # started now would append messages that _truncate_storage is
                # about to delete past keep_count.
                return False
            record.status = SessionStatus.RUNNING
            record.touch()
            return True

    async def set_interrupt_pending(self, session_id: str) -> None:
        record = self._get(session_id)
        if record:
            record.status = SessionStatus.INTERRUPT_PENDING
            record.touch()

    async def set_completed(self, session_id: str) -> None:
        record = self._get(session_id)
        if record:
            record.status = SessionStatus.COMPLETED
            record.touch()

    async def set_failed(self, session_id: str, error: str = "") -> None:
        record = self._get(session_id)
        if record:
            record.status = SessionStatus.FAILED
            record.error = error
            record.touch()

    async def set_aborted(self, session_id: str) -> None:
        """Mark session as aborted (user-initiated interruption)."""
        record = self._get(session_id)
        if record:
            record.status = SessionStatus.ABORTED
            record.touch()

    # ------------------------------------------------------------------
    # Task handle management
    # ------------------------------------------------------------------

    def set_task(self, session_id: str, task: asyncio.Task) -> None:
        """Bind a background asyncio.Task to a session."""
        record = self._get(session_id)
        if record:
            record._task = task

    def get_task(self, session_id: str) -> Optional[asyncio.Task]:
        """Return the background task for a session, if any."""
        record = self._get(session_id)
        return record._task if record else None

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, session_id: str) -> Optional[SessionRecord]:
        return self._sessions.get(session_id)

    def get_agent(self, session_id: str) -> Optional[Any]:
        record = self._sessions.get(session_id)
        return record.agent if record else None

    def get_status(self, session_id: str) -> Optional[SessionStatus]:
        record = self._sessions.get(session_id)
        return record.status if record else None

    def count_running(self) -> int:
        """Count in-memory sessions in RUNNING or INTERRUPT_PENDING status."""
        snapshot = list(self._sessions.values())
        return sum(
            1 for r in snapshot
            if r.status in (SessionStatus.RUNNING, SessionStatus.INTERRUPT_PENDING)
        )

    def get_running_session_ids(self) -> List[str]:
        """Return IDs of in-memory sessions in RUNNING or INTERRUPT_PENDING status."""
        snapshot = list(self._sessions.values())
        return [
            r.session_id for r in snapshot
            if r.status in (SessionStatus.RUNNING, SessionStatus.INTERRUPT_PENDING)
        ]

    def list_sessions(self) -> List[Dict]:
        # M2 fix: snapshot values before iterating to avoid RuntimeError
        # if dict is mutated concurrently (e.g. by FastAPI sync route via
        # ThreadPoolExecutor).
        snapshot = list(self._sessions.values())
        return [r.to_status_dict() for r in snapshot]

    def get_session_records_snapshot(self) -> List["SessionRecord"]:
        """Return a snapshot of all SessionRecord objects (for internal cleanup).

        L7 fix: provides a public method for server.py cleanup loop to avoid
        direct access to private _sessions attribute.
        """
        return list(self._sessions.values())

    # ------------------------------------------------------------------
    # WebSocket queue management
    # ------------------------------------------------------------------

    def add_ws_queue(self, session_id: str, queue: asyncio.Queue) -> None:
        """Register a WebSocket event queue for a session."""
        record = self._sessions.get(session_id)
        if record:
            record._ws_queues.append(queue)
        else:
            logger.warning(
                "add_ws_queue: no session record, WS events will be dropped "
                "(session=%s)", session_id,
            )

    def remove_ws_queue(self, session_id: str, queue: asyncio.Queue) -> None:
        """Unregister a WebSocket event queue."""
        record = self._sessions.get(session_id)
        if record and queue in record._ws_queues:
            record._ws_queues.remove(queue)

    async def push_ws_event(self, session_id: str, event: Dict) -> None:
        """Push an event to all WebSocket queues for a session."""
        record = self._sessions.get(session_id)
        if not record:
            logger.debug(
                "push_ws_event dropped (no session record): session=%s event_type=%s",
                session_id, event.get("event_type"),
            )
            return
        for queue in list(record._ws_queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("WS queue full for session %s, dropping event", session_id)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup_expired_interrupts(self) -> int:
        """Mark sessions stuck in interrupt_pending past timeout as failed.

        Returns number of sessions cleaned up.
        """
        now = time.time()
        expired = []

        # M2 fix: snapshot items() to allow concurrent dict mutation
        for sid, record in list(self._sessions.items()):
            if record.status == SessionStatus.INTERRUPT_PENDING:
                age = now - record.updated_at
                if age > self._timeout_seconds:
                    expired.append(sid)

        for sid in expired:
            await self.set_failed(sid, error=f"Interrupt timed out after {self._timeout_seconds}s")
            logger.warning("Session %s timed out in interrupt_pending state", sid)

        return len(expired)

    # ------------------------------------------------------------------
    # Abort event management
    # ------------------------------------------------------------------

    def set_abort(self, session_id: str) -> None:
        """Signal a session to abort at the next safe check point."""
        event = self._abort_events.get(session_id)
        if event is None:
            event = asyncio.Event()
            self._abort_events[session_id] = event
        event.set()
        logger.info("Session %s: abort signal set", session_id)

    def is_aborted(self, session_id: str) -> bool:
        """Return True if the abort signal has been set for this session."""
        event = self._abort_events.get(session_id)
        return event is not None and event.is_set()

    def clear_abort(self, session_id: str) -> None:
        """Clear the abort signal (call before resuming a session)."""
        event = self._abort_events.get(session_id)
        if event is not None:
            event.clear()
            logger.info("Session %s: abort signal cleared", session_id)

    def get_abort_event(self, session_id: str) -> asyncio.Event:
        """Return (creating if needed) the abort asyncio.Event for a session."""
        if session_id not in self._abort_events:
            self._abort_events[session_id] = asyncio.Event()
        return self._abort_events[session_id]

    # ------------------------------------------------------------------
    # Persistence check
    # ------------------------------------------------------------------

    async def exists_in_storage(self, session_id: str) -> bool:
        """Check whether a session_id exists in the persistent storage backend.

        Uses the Agno storage API (supports MongoDB, SQLite, PostgreSQL).
        Returns False (without raising) on any error.
        """
        try:
            from agno.db.base import SessionType
            from core.storage import get_storage

            storage = get_storage()
            session = storage.get_session(
                session_id=session_id,
                session_type=SessionType.AGENT,
            )
            return session is not None
        except Exception:
            logger.warning("exists_in_storage failed for session %s", session_id, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get(self, session_id: str) -> Optional[SessionRecord]:
        return self._sessions.get(session_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
session_manager = SessionManager()
