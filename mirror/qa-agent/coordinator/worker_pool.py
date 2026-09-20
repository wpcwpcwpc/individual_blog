"""
QA Agent System — Worker Pool

Central lifecycle registry for all Worker agents spawned by the Coordinator.
Tracks creation, execution, completion, and failure of each Worker.

Design reference: D2 (WorkerPool — Central Lifecycle Registry)
coordinator-session-restore: added MongoDB write-through and restore_from_db()
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WorkerStatus(Enum):
    """Lifecycle states for a Worker agent."""
    CREATING = "creating"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    TIMEOUT = "timeout"


@dataclass
class WorkerEntry:
    """Registry entry for a single Worker agent.

    Tracks full lifecycle from creation to completion, including the
    session_id needed to reconstruct the Agent for send_message continuation.
    """
    worker_id: str               # Unique ID (e.g. "w-abc123")
    agent_name: str              # From AgentDefinition.name
    description: str             # Human-readable task description
    status: WorkerStatus
    definition: Any              # AgentDefinition (typed as Any to avoid circular import)
    session_id: str              # Agno session ID — KEY for warm continuation
    created_at: float            # time.time() at creation
    completed_at: Optional[float] = None
    result: Optional[str] = None     # Final text response (on success)
    error: Optional[str] = None      # Error message (on failure)
    turns_used: int = 0
    tools_used: List[str] = field(default_factory=list)
    # task 10.1: artifact sharing fields
    context_ids: List[str] = field(default_factory=list)      # Input artifact IDs
    output_artifact_id: Optional[str] = None                   # Produced artifact ID

    @property
    def duration_s(self) -> Optional[float]:
        """Wall-clock duration in seconds, or None if still running."""
        if self.completed_at is not None:
            return round(self.completed_at - self.created_at, 2)
        return None

    @property
    def is_active(self) -> bool:
        """True if Worker is in a non-terminal state."""
        return self.status in (WorkerStatus.CREATING, WorkerStatus.RUNNING)

    @property
    def is_continuable(self) -> bool:
        """True if Worker can receive send_message (warm path)."""
        return self.status in (WorkerStatus.COMPLETED, WorkerStatus.STOPPED)


def generate_worker_id() -> str:
    """Generate a short unique Worker ID like 'w-abc123'."""
    short = uuid.uuid4().hex[:6]
    return f"w-{short}"


class WorkerPool:
    """Singleton registry of all active and completed Workers.

    Thread-safe via asyncio.Lock. All mutation methods are async.

    MongoDB write-through:
    When ``active_coordinator_session_id`` is set (via ``set_persistence()``),
    every ``register()`` and ``update_status()`` call also persists the change
    to ``coordinator_workers`` collection.  Persistence failures are logged as
    WARNING and never block in-memory operations.
    """

    def __init__(self) -> None:
        self._workers: Dict[str, WorkerEntry] = {}
        self._lock = asyncio.Lock()
        # Set by create_coordinator_agent() / restore_session(); None means no persistence.
        self.active_coordinator_session_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Persistence control
    # ------------------------------------------------------------------

    def set_persistence(self, coordinator_session_id: Optional[str]) -> None:
        """Enable or disable MongoDB write-through for subsequent operations.

        Call with the Coordinator's session_id after creating/restoring a
        Coordinator Agent.  Pass None to disable persistence (e.g. in tests).
        """
        self.active_coordinator_session_id = coordinator_session_id
        if coordinator_session_id:
            logger.info(
                "WorkerPool: MongoDB persistence enabled for coordinator %s",
                coordinator_session_id,
            )
        else:
            logger.debug("WorkerPool: MongoDB persistence disabled")

    # ------------------------------------------------------------------
    # Registration & Updates
    # ------------------------------------------------------------------

    async def register(self, entry: WorkerEntry) -> None:
        """Register a new Worker entry in the pool."""
        async with self._lock:
            if entry.worker_id in self._workers:
                logger.warning(
                    "Worker '%s' already registered, overwriting", entry.worker_id
                )
            self._workers[entry.worker_id] = entry
            logger.info(
                "WorkerPool: registered '%s' (agent=%s, status=%s)",
                entry.worker_id, entry.agent_name, entry.status.value,
            )

        # Write-through to MongoDB (outside lock to avoid I/O under lock)
        coordinator_sid = self.active_coordinator_session_id
        if coordinator_sid:
            try:
                from db.coordinator_workers import upsert_worker
                await upsert_worker(coordinator_sid, _entry_to_dict(entry))
            except Exception:
                logger.warning(
                    "WorkerPool: MongoDB persist failed for register '%s'",
                    entry.worker_id, exc_info=True,
                )

    async def update_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        *,
        result: Optional[str] = None,
        error: Optional[str] = None,
        turns_used: Optional[int] = None,
        tools_used: Optional[List[str]] = None,
    ) -> None:
        """Update a Worker's lifecycle status and optional fields."""
        completed_at_snapshot: Optional[float] = None

        async with self._lock:
            entry = self._workers.get(worker_id)
            if entry is None:
                logger.warning(
                    "WorkerPool: cannot update unknown worker '%s'", worker_id
                )
                return

            old_status = entry.status
            entry.status = status

            if result is not None:
                entry.result = result
            if error is not None:
                entry.error = error
            if turns_used is not None:
                entry.turns_used = turns_used
            if tools_used is not None:
                entry.tools_used = tools_used

            # Mark completion time for terminal states
            if status in (
                WorkerStatus.COMPLETED,
                WorkerStatus.FAILED,
                WorkerStatus.STOPPED,
                WorkerStatus.TIMEOUT,
            ):
                entry.completed_at = time.time()
                completed_at_snapshot = entry.completed_at

            logger.info(
                "WorkerPool: '%s' status %s → %s",
                worker_id, old_status.value, status.value,
            )

        # Write-through to MongoDB (outside lock)
        coordinator_sid = self.active_coordinator_session_id
        if coordinator_sid:
            try:
                from db.coordinator_workers import update_worker_status
                await update_worker_status(
                    worker_id,
                    status.value,
                    result=result,
                    error=error,
                    turns_used=turns_used,
                    tools_used=tools_used,
                    completed_at=completed_at_snapshot,
                )
            except Exception:
                logger.warning(
                    "WorkerPool: MongoDB persist failed for update_status '%s'",
                    worker_id, exc_info=True,
                )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    async def get(self, worker_id: str) -> Optional[WorkerEntry]:
        """Look up a Worker by ID."""
        async with self._lock:
            return self._workers.get(worker_id)

    async def get_active(self) -> List[WorkerEntry]:
        """Return all Workers in non-terminal states."""
        async with self._lock:
            return [e for e in self._workers.values() if e.is_active]

    async def get_all(self) -> List[WorkerEntry]:
        """Return all Workers (active and completed)."""
        async with self._lock:
            return list(self._workers.values())

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    async def restore_from_db(self, coordinator_session_id: str) -> int:
        """Load persisted Worker entries from MongoDB into the in-memory pool.

        Called during ``restore_session()`` to reconstruct WorkerPool state.

        Steps:
        1. Downgrade any RUNNING/CREATING workers to FAILED (process_restart).
        2. Load all documents for this coordinator session.
        3. Reconstruct WorkerEntry objects (AgentDefinition looked up from registry).
        4. Register them in the in-memory pool.

        Args:
            coordinator_session_id: The Coordinator's Agno session_id.

        Returns:
            Number of Worker entries loaded (0 if none found or error).
        """
        try:
            from db.coordinator_workers import downgrade_active_workers, find_by_coordinator

            # Step 1: downgrade in-flight workers
            downgraded = await downgrade_active_workers(coordinator_session_id)
            if downgraded:
                logger.info(
                    "WorkerPool.restore_from_db: downgraded %d active workers "
                    "(coordinator=%s)",
                    downgraded, coordinator_session_id,
                )

            # Step 2: load all worker documents
            rows = await find_by_coordinator(coordinator_session_id)
            if not rows:
                logger.info(
                    "WorkerPool.restore_from_db: no workers found for coordinator %s",
                    coordinator_session_id,
                )
                return 0

            # Step 3+4: reconstruct and register
            loaded = 0
            for row in rows:
                try:
                    entry = _entry_from_dict(row)
                    async with self._lock:
                        self._workers[entry.worker_id] = entry
                    loaded += 1
                except Exception:
                    logger.warning(
                        "WorkerPool.restore_from_db: failed to restore worker '%s'",
                        row.get("worker_id"), exc_info=True,
                    )

            logger.info(
                "WorkerPool.restore_from_db: restored %d/%d workers for coordinator %s",
                loaded, len(rows), coordinator_session_id,
            )
            return loaded

        except Exception:
            logger.warning(
                "WorkerPool.restore_from_db: failed for coordinator %s",
                coordinator_session_id, exc_info=True,
            )
            return 0

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def cleanup_expired(self, max_age_s: float = 3600) -> int:
        """Remove completed/failed Workers older than max_age_s.

        Returns the number of entries removed.
        """
        now = time.time()
        to_remove = []

        async with self._lock:
            for wid, entry in self._workers.items():
                if not entry.is_active and (now - entry.created_at) > max_age_s:
                    to_remove.append(wid)

            for wid in to_remove:
                del self._workers[wid]

        if to_remove:
            logger.info(
                "WorkerPool: cleaned up %d expired entries: %s",
                len(to_remove), to_remove,
            )
        return len(to_remove)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Total number of Workers in the pool (sync-safe read).

        M3 fix: wrap with try/except RuntimeError to defend against rare
        race when called during dict mutation by another coroutine.
        """
        try:
            return len(self._workers)
        except RuntimeError:
            logger.warning("WorkerPool.size: race during read, returning 0")
            return 0

    def __len__(self) -> int:
        try:
            return len(self._workers)
        except RuntimeError:
            logger.warning("WorkerPool.__len__: race during read, returning 0")
            return 0

    def __contains__(self, worker_id: str) -> bool:
        try:
            return worker_id in self._workers
        except RuntimeError:
            logger.warning("WorkerPool.__contains__: race during read, returning False")
            return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _entry_to_dict(entry: WorkerEntry) -> Dict[str, Any]:
    """Convert a WorkerEntry to a plain dict for DAO calls."""
    return {
        "worker_id": entry.worker_id,
        "agent_name": entry.agent_name,
        "description": entry.description,
        "status": entry.status.value,
        "session_id": entry.session_id,
        "created_at": entry.created_at,
        "completed_at": entry.completed_at,
        "result": entry.result,
        "error": entry.error,
        "turns_used": entry.turns_used,
        "tools_used": entry.tools_used,
        "context_ids": entry.context_ids,
        "output_artifact_id": entry.output_artifact_id,
    }


def _entry_from_dict(row: Dict[str, Any]) -> WorkerEntry:
    """Reconstruct a WorkerEntry from a persisted dict (DB row).

    Looks up AgentDefinition from the agent registry by agent_name.
    Sets definition=None and logs WARNING if not found (agent may have been
    renamed or removed since the session was created).
    """
    from agents.registry import agent_registry  # local import to avoid circular

    agent_name = row.get("agent_name", "")
    definition = agent_registry.get(agent_name)
    if definition is None:
        logger.warning(
            "WorkerPool.restore: agent '%s' not found in registry — "
            "definition=None; send_message to this worker will fail",
            agent_name,
        )

    # Map string status back to enum; default FAILED on unknown values
    status_str = row.get("status", "failed")
    try:
        status = WorkerStatus(status_str)
    except ValueError:
        logger.warning(
            "WorkerPool.restore: unknown status '%s' for worker '%s', defaulting to FAILED",
            status_str, row.get("worker_id"),
        )
        status = WorkerStatus.FAILED

    return WorkerEntry(
        worker_id=row["worker_id"],
        agent_name=agent_name,
        description=row.get("description", ""),
        status=status,
        definition=definition,
        session_id=row.get("session_id", ""),
        created_at=float(row.get("created_at") or time.time()),
        completed_at=row.get("completed_at"),
        result=row.get("result"),
        error=row.get("error"),
        turns_used=int(row.get("turns_used") or 0),
        tools_used=list(row.get("tools_used") or []),
        context_ids=list(row.get("context_ids") or []),
        output_artifact_id=row.get("output_artifact_id"),
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
worker_pool = WorkerPool()
