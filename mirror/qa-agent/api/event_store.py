"""
QA Agent System — PersistentEventStore

Per-session, append-only event log with sequential IDs.
Events are stored **both** in memory (for low-latency WS replay) and
persisted in **micro-batches** to MongoDB ``session_events`` collection
(for recovery after page refresh, crash, or abort).

Every event pushed during an agent run is stored here so that:
  1. WS consumers can **replay** missed events after a reconnect
     (using ``last_event_id``).
  2. Events are never silently lost when no consumer is connected.
  3. The frontend can switch between sessions freely — each session's
     event history is preserved independently.
  4. After a page refresh or process restart, events can be recovered
     from MongoDB to restore in-progress run context.

Write strategy (V2 — buffered):
  - ``append()`` puts the event into an in-memory write buffer.
  - A background ``_flush_loop()`` task flushes the buffer to MongoDB
    every ``FLUSH_INTERVAL_S`` seconds, OR immediately when any session's
    buffer reaches ``BATCH_SIZE`` events.
  - On graceful shutdown, ``flush_all()`` drains every buffer before
    closing the loop.
  - ``stop()`` cancels the background loop and triggers a final flush.

This dramatically reduces the loss window: the previous fire-and-forget
``create_task`` per event lost the entire run on hard kill; now the worst
case is a single ``FLUSH_INTERVAL_S`` window (≈300 ms).

Events are kept in memory for the lifetime of the session record.
``clear(session_id)`` clears memory only.  The per-session seq counter is
**preserved** across clears so subsequent runs continue numbering above all
previously issued seq_ids (clients may hold a stale
high-water ``last_event_id``; resetting the counter makes the WS live-dedup
drop an entire new run's events).
``clear_persisted(session_id)`` clears memory, MongoDB AND persists a seq
watermark (``SessionSeqWatermark``) so a restarted process reseeds the
counter above every previously issued seq_id (see ``ensure_counter_seeded``).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flush tuning
# ---------------------------------------------------------------------------
BATCH_SIZE = 20            # Flush a session's buffer when it reaches this size
FLUSH_INTERVAL_S = 0.3     # Background flush cadence
MAX_FLUSH_RETRIES = 2      # In-place retries inside _flush_loop before dropping
SHUTDOWN_TIMEOUT_S = 2.0   # Max time flush_all() will block during shutdown


class PersistentEventStore:
    """Thread-safe, per-session, append-only event log with MongoDB persistence."""

    def __init__(self) -> None:
        # session_id → list of (seq_id, event_dict) — full replay log (memory)
        self._logs: Dict[str, List[Tuple[int, dict]]] = {}
        # session_id → next seq counter
        self._counters: Dict[str, int] = {}
        # session_id → pending events not yet written to MongoDB
        self._write_buffer: Dict[str, List[dict]] = {}
        # session_id → consecutive failed flush attempts (reset on success)
        self._retry_counts: Dict[str, int] = {}
        # Combined lock guarding logs/counters/write_buffer
        self._lock = threading.Lock()
        # Background flush task handle (started by start(), cancelled by stop())
        self._flush_task: Optional[asyncio.Task] = None
        # Signal that triggers an immediate flush (set when a buffer reaches BATCH_SIZE)
        self._flush_event: Optional[asyncio.Event] = None
        # Lifecycle flag
        self._stopped: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background flush loop. Idempotent.

        Must be called from inside a running asyncio event loop (e.g. the
        FastAPI lifespan startup hook).
        """
        if self._flush_task is not None and not self._flush_task.done():
            return  # already running

        self._stopped = False
        self._flush_event = asyncio.Event()
        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="event-store-flush-loop"
        )
        logger.info("PersistentEventStore: flush loop started")

    async def stop(self) -> None:
        """Stop the background flush loop and drain remaining buffer.

        Safe to call multiple times.
        """
        self._stopped = True

        # Final drain — best-effort under SHUTDOWN_TIMEOUT_S
        try:
            await asyncio.wait_for(self.flush_all(), timeout=SHUTDOWN_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning(
                "PersistentEventStore: flush_all timed out after %.1fs during stop",
                SHUTDOWN_TIMEOUT_S,
            )
        except Exception:
            logger.warning("PersistentEventStore: flush_all error during stop", exc_info=True)

        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
        self._flush_task = None
        logger.info("PersistentEventStore: flush loop stopped")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, session_id: str, event: dict) -> int:
        """Append an event and return the assigned ``seq_id``.

        The ``seq_id`` is a session-scoped, monotonically increasing integer,
        **continuing across runs** — the counter is never reset when logs are
        cleared.  It is injected into the event dict
        **in-place** (``event["seq_id"] = seq_id``) for convenience.

        After the synchronous in-memory write, the event is added to the
        session's write buffer.  The background ``_flush_loop`` will pick
        it up on its next tick (or sooner if the buffer reached BATCH_SIZE).

        M5 fix: if the store has been stopped (shutdown), reject new appends
        and return -1 instead of silently buffering events that will never flush.
        """
        if self._stopped:
            logger.warning(
                "PersistentEventStore.append: rejected (store stopped) "
                "session=%s event_type=%s",
                session_id, event.get("event_type", "?"),
            )
            return -1

        trigger_flush = False
        with self._lock:
            if session_id not in self._logs:
                self._logs[session_id] = []
                self._write_buffer[session_id] = []
            # Counter init is deliberately separate from the log init above:
            # after clear() the log is rebuilt but the counter survives, so a
            # new run continues from the previous high-water seq_id.
            if session_id not in self._counters:
                self._counters[session_id] = 0

            self._counters[session_id] += 1
            seq_id = self._counters[session_id]
            event["seq_id"] = seq_id
            self._logs[session_id].append((seq_id, event))

            # L11 fix: use shallow copy for high-frequency events (token/heartbeat)
            # to reduce GC pressure. These events have simple flat structures.
            # For other events, keep deepcopy to decouple from caller mutations.
            event_type = event.get("event_type", "")
            if event_type in ("token", "heartbeat"):
                self._write_buffer[session_id].append(event.copy())
            else:
                self._write_buffer[session_id].append(copy.deepcopy(event))

            if len(self._write_buffer[session_id]) >= BATCH_SIZE:
                trigger_flush = True

        # Wake the flush loop early when the buffer hits BATCH_SIZE.
        if trigger_flush and self._flush_event is not None:
            try:
                # set() is thread-safe for asyncio.Event when called from the
                # same loop; callers of append() always run in the event loop.
                self._flush_event.set()
            except Exception:
                pass

        return seq_id

    async def _flush_loop(self) -> None:
        """Background task: periodically flush all session buffers to MongoDB."""
        while not self._stopped:
            try:
                # Sleep up to FLUSH_INTERVAL_S, but wake early if signalled.
                assert self._flush_event is not None
                try:
                    await asyncio.wait_for(
                        self._flush_event.wait(), timeout=FLUSH_INTERVAL_S
                    )
                except asyncio.TimeoutError:
                    pass
                self._flush_event.clear()

                await self._flush_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("PersistentEventStore: flush loop iteration error", exc_info=True)
                # Brief back-off to avoid tight error loops
                await asyncio.sleep(FLUSH_INTERVAL_S)

    async def _flush_once(self) -> None:
        """Drain every session's write buffer once."""
        # Snapshot the buffer under the lock; reset it so concurrent appends
        # accumulate into a fresh list.
        # Use timeout to avoid deadlock when signal handler interrupts main
        # thread holding the lock (e.g., SIGTERM during append()).
        if not self._lock.acquire(timeout=1.0):
            logger.warning(
                "PersistentEventStore: _flush_once lock timeout, skipping flush"
            )
            return
        try:
            pending: Dict[str, List[dict]] = {}
            for sid, buf in self._write_buffer.items():
                if buf:
                    pending[sid] = buf
                    self._write_buffer[sid] = []
        finally:
            self._lock.release()

        if not pending:
            return

        for session_id, events in pending.items():
            success = await self._bulk_write(session_id, events)
            if success:
                self._retry_counts.pop(session_id, None)
                continue

            # Failed — decide whether to re-queue.
            remaining_retries = self._retry_counts.get(session_id, 0)
            if not self._lock.acquire(timeout=1.0):
                logger.warning(
                    "PersistentEventStore: re-queue lock timeout for session=%s, "
                    "dropping %d events",
                    session_id, len(events),
                )
                continue
            try:
                if remaining_retries < MAX_FLUSH_RETRIES:
                    self._retry_counts[session_id] = remaining_retries + 1
                    # Prepend so order is preserved relative to fresh appends
                    self._write_buffer[session_id] = events + self._write_buffer.get(session_id, [])
                else:
                    logger.warning(
                        "PersistentEventStore: dropping %d events for session=%s "
                        "after %d failed flush attempts",
                        len(events), session_id, MAX_FLUSH_RETRIES,
                    )
                    self._retry_counts.pop(session_id, None)
            finally:
                self._lock.release()

    async def _bulk_write(self, session_id: str, events: List[dict]) -> bool:
        """Insert a batch of events for one session. Returns True on success."""
        try:
            from db.models import SessionEvent

            docs = [
                SessionEvent(
                    session_id=session_id,
                    seq_id=ev.get("seq_id", 0),
                    event_type=ev.get("event_type", ""),
                    agent_name=ev.get("agent_name", ""),
                    data=ev,
                )
                for ev in events
            ]
            # Beanie's insert_many honours ordered=False under the hood when we
            # use the motor collection directly. We use Beanie's API for
            # consistency; if a duplicate (session_id, seq_id) collides, the
            # whole call may fail — but seq_id is monotonic per session within
            # a single process run, so collisions only happen on recovery
            # re-runs (handled by clear_persisted before retry).
            await SessionEvent.insert_many(docs)
            return True
        except Exception:
            logger.warning(
                "PersistentEventStore: bulk write failed (session=%s, count=%d)",
                session_id, len(events), exc_info=True,
            )
            return False

    async def flush_all(self) -> None:
        """Force-flush every session's buffer to MongoDB. Used at shutdown."""
        # Repeat until buffers are empty (subsequent appends could arrive
        # during a flush; we want shutdown to actually drain).
        pending = 0
        for _ in range(MAX_FLUSH_RETRIES + 1):
            await self._flush_once()
            # Use timeout to avoid deadlock in signal handler context
            if not self._lock.acquire(timeout=1.0):
                logger.warning("PersistentEventStore: flush_all lock timeout, skipping")
                return
            try:
                pending = sum(len(b) for b in self._write_buffer.values())
            finally:
                self._lock.release()
            if pending == 0:
                return
        logger.warning(
            "PersistentEventStore: flush_all left %d events undrained",
            pending,
        )

    # ------------------------------------------------------------------
    # Read / Replay
    # ------------------------------------------------------------------

    def replay(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> List[dict]:
        """Return events with ``seq_id > after_seq``, in order (memory only).

        Args:
            session_id: Target session.
            after_seq:  Exclusive lower bound.  Pass 0 to get ALL events.

        Returns:
            List of event dicts (each already has ``seq_id`` injected).
        """
        with self._lock:
            log = self._logs.get(session_id)
            if not log:
                return []
            # Events are appended in order, so a simple filter is sufficient.
            return [ev for seq, ev in log if seq > after_seq]

    async def replay_async(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> List[dict]:
        """Return events with ``seq_id > after_seq``, with MongoDB fallback.

        Prefers in-memory data (fast path).  Falls back to MongoDB when the
        session has no in-memory events (e.g. after process restart).

        Args:
            session_id: Target session.
            after_seq:  Exclusive lower bound.  Pass 0 to get ALL events.

        Returns:
            List of event dicts sorted by seq_id ascending.
        """
        # Fast path: memory
        with self._lock:
            has_memory = session_id in self._logs and len(self._logs[session_id]) > 0

        if has_memory:
            return self.replay(session_id, after_seq)

        # Cold path: MongoDB
        try:
            from db.models import SessionEvent

            docs = await SessionEvent.find(
                SessionEvent.session_id == session_id,
                SessionEvent.seq_id > after_seq,
            ).sort(+SessionEvent.seq_id).to_list()

            return [doc.data for doc in docs]
        except Exception:
            logger.warning(
                "Failed to replay events from MongoDB (session=%s)",
                session_id,
                exc_info=True,
            )
            return []

    def latest_seq(self, session_id: str) -> int:
        """Return the latest seq_id for a session, or 0 if none.

        The counter survives ``clear()``/``clear_persisted()`` — this keeps
        the last-seq probe consistent with clients that still hold the
        pre-clear high-water mark.
        """
        with self._lock:
            return self._counters.get(session_id, 0)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear(self, session_id: str) -> int:
        """Remove all stored events for a session (memory only).

        The per-session seq counter is **preserved**:
        subsequent runs continue numbering above every previously issued
        seq_id, so clients holding a stale high-water ``last_event_id``
        still receive all new events (WS live-dedup keeps its semantics).

        Returns the number of events that were cleared.
        """
        with self._lock:
            log = self._logs.pop(session_id, [])
            self._write_buffer.pop(session_id, None)
            count = len(log)

        if count:
            logger.debug("EventStore: cleared %d in-memory events for session %s", count, session_id)
        return count

    async def clear_persisted(self, session_id: str) -> int:
        """Remove all events for a session from both memory and MongoDB.

        Returns the number of in-memory events that were cleared.
        """
        # First flush any pending writes for this session — otherwise an event
        # currently in the write buffer would be lost AND not present in Mongo,
        # but worse: if the flush task fires after the delete, those events
        # would re-appear as orphans. We snapshot+drop the buffer here.
        with self._lock:
            self._write_buffer.pop(session_id, None)

        count = self.clear(session_id)

        try:
            from db.models import SessionEvent

            result = await SessionEvent.find(
                SessionEvent.session_id == session_id,
            ).delete()
            deleted = result.deleted_count if result else 0
            if deleted:
                logger.debug(
                    "EventStore: cleared %d persisted events for session %s",
                    deleted,
                    session_id,
                )
        except Exception:
            logger.warning(
                "Failed to clear persisted events from MongoDB (session=%s)",
                session_id,
                exc_info=True,
            )

        # Persist the seq watermark so a restarted process resumes the
        # counter above every previously issued seq_id. Failure is non-fatal —
        # the in-memory counter still continues; only restart recovery degrades.
        next_seq = self.latest_seq(session_id) + 1
        try:
            from db.models import SessionSeqWatermark

            doc = await SessionSeqWatermark.find_one(
                SessionSeqWatermark.session_id == session_id
            )
            if doc is not None:
                doc.next_seq = next_seq
                await doc.save()
            else:
                await SessionSeqWatermark(
                    session_id=session_id, next_seq=next_seq
                ).insert()
        except Exception:
            logger.warning(
                "EventStore: failed to persist seq watermark (session=%s, next_seq=%d)",
                session_id,
                next_seq,
                exc_info=True,
            )

        return count

    async def ensure_counter_seeded(self, session_id: str) -> None:
        """Seed the session's seq counter from persistent state if absent.

        Called on the run-start path (``send_message``) before the first
        ``append()`` of a run.  After a process restart the in-memory
        counters are gone; this resumes numbering above every previously
        issued seq_id so clients holding a stale high-water
        ``last_event_id`` (sessionStorage / reconnect URL) keep receiving
        all new events.

        Seed = max(watermark.next_seq - 1, max seq_id in persisted events).
        No watermark and no persisted events → counter starts from 0
        (first append → seq 1, legacy-compatible).
        """
        with self._lock:
            if session_id in self._counters:
                return

        last_seq = 0
        try:
            from db.models import SessionEvent, SessionSeqWatermark

            wm = await SessionSeqWatermark.find_one(
                SessionSeqWatermark.session_id == session_id
            )
            if wm is not None and wm.next_seq > 1:
                last_seq = max(last_seq, wm.next_seq - 1)
            latest = (
                await SessionEvent.find(
                    SessionEvent.session_id == session_id
                )
                .sort(-SessionEvent.seq_id)
                .first_or_none()
            )
            if latest is not None:
                last_seq = max(last_seq, latest.seq_id)
        except Exception:
            # Seeding failed (e.g. Mongo unavailable) — leave the counter
            # absent so append() starts from 0, matching legacy behaviour.
            logger.warning(
                "EventStore: counter seed failed (session=%s), starting from 0",
                session_id,
                exc_info=True,
            )
            return

        with self._lock:
            # Only set if still absent — a concurrent append may have
            # initialised the counter while we were reading Mongo.
            if session_id not in self._counters and last_seq > 0:
                self._counters[session_id] = last_seq
                logger.info(
                    "EventStore: seeded seq counter for session=%s at %d",
                    session_id,
                    last_seq,
                )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
event_store = PersistentEventStore()
