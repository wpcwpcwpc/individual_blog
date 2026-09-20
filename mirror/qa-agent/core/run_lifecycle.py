"""QA Agent System — Run Lifecycle Markers

Reads / writes the per-session run lifecycle marker stored in the agno agent
session's ``session_data.session_state``:

    session_data.session_state.run_status        "idle" | "running" | "interrupted" | "paused"
    session_data.session_state.original_message  <str | None>
    session_data.session_state.paused_run_id    <str | None>  (HITL: which run awaits continue)

This is the **resume detection signal** for the single-Agent AgentOS path
(``api/agent_os_adapter.py``). When a client disconnects mid-stream the
adapter writes ``run_status="interrupted"`` + ``original_message`` here; the
next request with the same ``session_id`` reads it back to decide whether to
forward the original message (resume) or start a fresh turn.

``run_status="paused"`` + ``paused_run_id`` is the HITL pause marker:
written *before* the RunPaused SSE frame is yielded (帧即屏障), so a new
plain message gets 409-guided to the continue endpoint while a continue
request resumes the paused run.

Storage location rationale:
lives on the agno *agent* session_state, next to the run it describes. Keeps
adapter sessions out of ``user_sessions`` (preserving the "no frontend
history" constraint) and out of ``session_events`` (preserving the
"No EventStore for adapter" constraint).

All access is via Motor (reusing Beanie's client) with ``upsert=False`` so a
write against a not-yet-created agno_sessions document is a no-op rather than
a spurious insert. The critical ``"interrupted"`` write always follows
``persist_partial_run`` (which guarantees the document exists).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


#: Nested path in the agno_sessions document.
_RUN_STATUS_PATH = "session_data.session_state.run_status"
_ORIG_MSG_PATH = "session_data.session_state.original_message"
_PAUSED_RUN_ID_PATH = "session_data.session_state.paused_run_id"


def _agno_sessions_coll():
    """Return the Motor ``agno_sessions`` collection (Beanie client reuse).

    Lazy import keeps this module importable before ``init_db()`` has run.
    """
    from db.models import UserSession  # any Beanie Document to reach the client

    motor_client = UserSession.get_motor_collection().database.client
    return motor_client["qa_agent_db"]["agno_sessions"]


async def read_run_status(session_id: str) -> str:
    """Return the persisted ``run_status`` for a session.

    Returns ``"idle"`` when the session has no marker yet (first run, or a
    session whose agno_sessions document does not exist). Never raises.
    """
    try:
        coll = _agno_sessions_coll()
        doc = await coll.find_one(
            {"session_id": session_id},
            {_RUN_STATUS_PATH: 1},
        )
        if doc is None:
            return "idle"
        sd = doc.get("session_data") or {}
        state = sd.get("session_state") or {}
        status = state.get("run_status")
        return status if isinstance(status, str) else "idle"
    except Exception:
        logger.warning(
            "run_lifecycle: read_run_status failed (session=%s)",
            session_id, exc_info=True,
        )
        return "idle"


async def read_original_message(session_id: str) -> Optional[str]:
    """Return the persisted ``original_message`` for a session, or ``None``."""
    try:
        coll = _agno_sessions_coll()
        doc = await coll.find_one(
            {"session_id": session_id},
            {_ORIG_MSG_PATH: 1},
        )
        if doc is None:
            return None
        sd = doc.get("session_data") or {}
        state = sd.get("session_state") or {}
        msg = state.get("original_message")
        return msg if isinstance(msg, str) else None
    except Exception:
        logger.warning(
            "run_lifecycle: read_original_message failed (session=%s)",
            session_id, exc_info=True,
        )
        return None


async def read_paused_run_id(session_id: str) -> Optional[str]:
    """Return the persisted ``paused_run_id`` for a session, or ``None``."""
    try:
        coll = _agno_sessions_coll()
        doc = await coll.find_one(
            {"session_id": session_id},
            {_PAUSED_RUN_ID_PATH: 1},
        )
        if doc is None:
            return None
        sd = doc.get("session_data") or {}
        state = sd.get("session_state") or {}
        rid = state.get("paused_run_id")
        return rid if isinstance(rid, str) else None
    except Exception:
        logger.warning(
            "run_lifecycle: read_paused_run_id failed (session=%s)",
            session_id, exc_info=True,
        )
        return None


async def write_run_lifecycle(
    session_id: str,
    run_status: str,
    *,
    original_message: Optional[str] = None,
    paused_run_id: Optional[str] = None,
) -> None:
    """Persist a run lifecycle transition to the agno agent session_state.

    Args:
        session_id: Target agno_sessions document.
        run_status: One of ``"idle"`` / ``"running"`` / ``"interrupted"``
            / ``"paused"`` (free-form string, no enum gate).
        original_message: When provided (not ``None``), also overwrites the
            stored original_message. Pass ``None`` to leave the existing
            value untouched (used on resume so the prior turn's message is
            preserved).
        paused_run_id: When provided, records which run awaits HITL continue
            (written together with ``run_status="paused"`` so the 409 detail
            on a paused session can point the platform at the exact run).

    Uses ``upsert=False``: a write against a not-yet-created document is a
    no-op. The ``"running"`` marker on a session's very first run may thus
    not persist (the agno_sessions document is created by ``agent.arun``),
    which is acceptable — resume correctness relies on ``"interrupted"``
    (written after ``persist_partial_run`` guarantees the document exists)
    and pause correctness relies on ``"paused"`` (written after agno's pause
    handler has already stored the session + run).
    """
    try:
        coll = _agno_sessions_coll()
        set_fields: dict = {
            _RUN_STATUS_PATH: run_status,
            "updated_at": int(time.time()),
        }
        if original_message is not None:
            set_fields[_ORIG_MSG_PATH] = original_message
        if paused_run_id is not None:
            set_fields[_PAUSED_RUN_ID_PATH] = paused_run_id

        await coll.update_one(
            {"session_id": session_id},
            {"$set": set_fields},
            upsert=False,
        )
    except Exception:
        logger.warning(
            "run_lifecycle: write_run_lifecycle failed (session=%s, status=%s)",
            session_id, run_status, exc_info=True,
        )
