"""QA Agent System — Partial Run Persistence

Shared helper that builds a RunOutput from a list of Agno Messages and
``$push``-es it onto the ``agno_sessions`` document's ``runs[]`` array via
Motor.

Used by two paths that both need to persist a partial / interrupted run:

  - ``api/agent_os_adapter.py`` — client disconnect during SSE streaming
    (single-Agent AgentOS path).
  - ``api/startup_recovery.py`` — process-crash salvage that aggregates
    ``session_events`` back into ``agno_sessions``.

Both paths MUST produce an identical ``RunOutput`` dict so that Agno's
``AgentSession.from_dict()`` can read the recovered run on the next
``get_session()`` call.

Key invariant — inject ``"agent_id": null`` into the run dict (Bug B):
``AgentSession.from_dict()`` skips runs whose dict lacks the ``"agent_id"``
key (it checks key presence, not value), so recovered messages would be
silently invisible without this injection.

"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from uuid import uuid4

from agno.models.message import Message
from agno.run.agent import RunOutput

logger = logging.getLogger(__name__)


# ── Public API ──────────────────────────────────────────────────────────────


async def persist_partial_run(
    session_id: str,
    messages: list[Message],
    run_id: Optional[str] = None,
    *,
    agent_name: Optional[str] = None,
) -> None:
    """Build a RunOutput from ``messages`` and ``$push`` it onto ``agno_sessions.runs[]``.

    Args:
        session_id: Target ``agno_sessions`` document.
        messages: Pre-built Agno Message list (e.g. from
            ``AbortMessagesCollector.to_messages()`` or from event aggregation).
        run_id: ``RunOutput.run_id``; generated when ``None``.
        agent_name: Optional agent name tag on the run.

    The run dict is injected with ``"agent_id": null`` to satisfy
    ``AgentSession.from_dict()`` (which skips runs missing the key — Bug B).
    The existing document's ``user_id`` is preserved; for documents without
    one (insert path), ``user_id`` is resolved from ``user_sessions`` so
    Agno's ``get_session(session_id, user_id)`` filter still matches.
    """
    if not messages:
        logger.info("persist_partial_run: no messages for session=%s, skipping", session_id)
        return

    assistant_text = "".join(
        (m.content or "")
        for m in messages
        if getattr(m, "role", None) == "assistant" and isinstance(m.content, str)
    )
    run = RunOutput(
        run_id=run_id or str(uuid4()),
        session_id=session_id,
        agent_id=None,
        agent_name=agent_name or None,
        messages=messages,
        content=assistant_text,
    )
    run_dict = run.to_dict()
    # Bug B fix: AgentSession.from_dict() requires the "agent_id" key to exist
    # (value may be None). RunOutput.to_dict() omits it.
    if "agent_id" not in run_dict:
        run_dict["agent_id"] = None

    await _mongo_push_run(session_id, run_dict)


# ── Motor write ─────────────────────────────────────────────────────────────


async def _mongo_push_run(session_id: str, run_dict: dict) -> None:
    """``$push`` ``run_dict`` onto ``agno_sessions[session_id].runs`` via Motor.

    Reuses the Motor client backing Beanie (already initialised by ``init_db``)
    rather than opening a new connection, so we stay within one pool.

    For an existing document we only ``$push`` the run and bump ``updated_at``.
    If no document exists yet (e.g. first run of an adapter session whose agno
    doc was not yet persisted when the client disconnected), we insert a
    complete document with the correct ``user_id`` so Agno's later
    ``get_session(session_id, user_id)`` filter can find it.
    """
    try:
        from db.models import UserSession  # any Beanie Document to reach the client

        motor_client = UserSession.get_motor_collection().database.client
        coll = motor_client["qa_agent_db"]["agno_sessions"]

        user_id = await _read_user_id_for_session(session_id)

        result = await coll.update_one(
            {"session_id": session_id},
            {
                "$push": {"runs": run_dict},
                "$set": {"updated_at": int(time.time())},
                "$setOnInsert": {"user_id": user_id},
            },
            upsert=False,
        )

        if result.matched_count > 0:
            # Fix a null user_id on an existing document so Agno's filter matches.
            await coll.update_one(
                {"session_id": session_id, "$or": [{"user_id": None}, {"user_id": {"$exists": False}}]},
                {"$set": {"user_id": user_id}},
            )
            logger.info(
                "persist_partial_run: $push OK session=%s matched=%d modified=%d",
                session_id, result.matched_count, result.modified_count,
            )
        else:
            # No existing document — insert a complete one with proper user_id.
            logger.info(
                "persist_partial_run: no existing agno_sessions doc for session=%s — inserting new (user_id=%s)",
                session_id, user_id,
            )
            await coll.insert_one({
                "session_id": session_id,
                "session_type": "agent",
                "agent_id": None,
                "user_id": user_id,
                "runs": [run_dict],
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            })

    except Exception:
        logger.warning(
            "persist_partial_run: _mongo_push_run failed (session=%s)",
            session_id, exc_info=True,
        )


async def _read_user_id_for_session(session_id: str) -> Optional[str]:
    """Resolve the email / ``user_id`` for a session from ``user_sessions``.

    Used when creating a new ``agno_sessions`` document so Agno's
    ``upsert_session()`` filter (which includes ``user_id``) can match it on
    subsequent runs, preventing recovered history from being clobbered.

    Uses the raw Motor collection rather than Beanie's ``find_one(...)``:
    Beanie's projection parameter expects a Pydantic ``projection_model``,
    not a dict, and silently swallows mismatched kwargs.

    Returns ``None`` for adapter sessions that are deliberately not registered
    in ``user_sessions`` (per the agent-os-adapter spec). In that case the
    existing ``agno_sessions`` document already carries the real ``user_id``
    written by Agno during ``agent.arun()``, so the insert branch (the only
    consumer of this value) is not reached for adapter sessions.
    """
    try:
        from db.models import UserSession

        coll = UserSession.get_motor_collection()
        doc = await coll.find_one(
            {"sessions.session_id": session_id},
            {"email": 1},
        )
        if doc is not None:
            email = doc.get("email")
            logger.debug(
                "persist_partial_run: resolved user_id for session=%s → %s",
                session_id, email,
            )
            return email
    except Exception:
        logger.warning(
            "persist_partial_run: _read_user_id_for_session failed (session=%s)",
            session_id, exc_info=True,
        )
    return None
