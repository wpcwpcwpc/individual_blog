"""
QA Agent System — Startup Recovery Hook

Runs once on every service startup, after ``init_db()`` and before the
FastAPI app accepts traffic. Detects sessions whose previous run was
interrupted by a process crash and salvages buffered events from the
``session_events`` collection into Agno's ``agno_sessions`` storage so
the conversation history is preserved.

Detection signal
----------------
A persistent ``run_status`` field on ``UserSession.sessions[]`` is
written to ``"running"`` when a run starts and back to ``"idle"`` when
it ends cleanly (normal completion or user abort). A crashed process
never gets to flip the marker, so any ``run_status == "running"`` at
startup is an orphan.

Recovery pipeline (per orphan)
------------------------------
1. Read the session's persisted events from MongoDB ``session_events``.
2. Aggregate them into a normalised messages list via
   ``message_aggregator.aggregate_events_to_messages``.
3. Build an Agno ``RunOutput`` dict and ``$push`` it directly onto the
   existing ``agno_sessions`` document using Motor.
4. Delete the merged events from ``session_events``.
5. Flip ``run_status`` to ``"interrupted"``.

Why we bypass Agno's upsert_session()
--------------------------------------
Agno's ``MongoDb.upsert_session()`` has two issues that break recovery:

  Bug A — filter includes ``user_id``. When we build a fresh
           ``AgentSession(session_id=...)`` without knowing the original
           ``user_id``, the filter never matches the existing document.
           The upsert creates a duplicate orphan document instead.

  Bug B — ``RunOutput.to_dict()`` omits the ``"agent_id"`` key.
           ``AgentSession.from_dict()`` skips runs that do not have
           ``"agent_id"`` in their dict, so all recovered messages
           become invisible on the next ``get_session()`` call.

Fix: use Motor directly to ``$push`` the run dict onto the existing
document after manually injecting ``"agent_id": null`` to satisfy
``from_dict``'s condition check.

Idempotency
-----------
- The recovery hook deletes events *after* the push succeeds.
- A crash mid-recovery causes the next startup to redo the push
  (``$push`` appends, so the run appears twice — acceptable, since
   duplicate run_id is visible to the user as a repeated block, not
   silent data loss).
- The ``run_status`` flip happens last; a missed flip causes one extra
  recovery pass on the next boot (idempotent).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def recover_orphan_sessions() -> int:
    """Detect and recover all orphan sessions.

    Returns:
        The number of sessions that were processed. 0 if none.
    """
    orphans = await _find_orphans()
    if not orphans:
        logger.info("Recovery: no orphan sessions detected")
        return 0

    logger.info("Recovery: found %d orphan session(s) — starting salvage", len(orphans))

    processed = 0
    for orphan in orphans:
        email = orphan.get("email")
        session_id = orphan.get("session_id")
        agent_name = orphan.get("agent_name", "")

        if not email or not session_id:
            logger.warning("Recovery: skipping malformed orphan record: %r", orphan)
            continue

        try:
            await _recover_one(email=email, session_id=session_id, agent_name=agent_name)
            processed += 1
        except Exception:
            logger.warning(
                "Recovery: failed to recover session=%s (email=%s)",
                session_id, email, exc_info=True,
            )

    logger.info("Recovery: processed %d/%d orphan session(s)", processed, len(orphans))
    return processed


# ---------------------------------------------------------------------------
# Step 1 — find orphans
# ---------------------------------------------------------------------------

async def _find_orphans() -> List[Dict[str, Any]]:
    try:
        from db.user_sessions import find_orphan_sessions
        return await find_orphan_sessions()
    except Exception:
        logger.warning("Recovery: find_orphan_sessions failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Recover one session (steps 2-5)
# ---------------------------------------------------------------------------

async def _recover_one(email: str, session_id: str, agent_name: str) -> None:
    """Run the salvage pipeline for a single orphan session."""

    # Step 1 — read persisted events -----------------------------------
    events = await _read_persisted_events(session_id)

    if not events:
        # No events in MongoDB — buffer was never flushed before crash.
        # Write a notice message so user sees the gap.
        logger.info(
            "Recovery: session=%s has no persisted events — writing interruption notice",
            session_id,
        )
        await _push_notice_run(
            session_id=session_id,
            agent_name=agent_name,
            text="[系统] 上次会话因服务异常退出而中断，部分消息可能已丢失。",
        )
        await _mark_interrupted(email, session_id)
        return

    # Step 2 — aggregate events → messages ----------------------------
    from core.message_aggregator import aggregate_events_to_messages

    msg_dicts = aggregate_events_to_messages(events)
    if not msg_dicts:
        logger.info(
            "Recovery: session=%s — %d events but 0 messages after aggregation",
            session_id, len(events),
        )
        await _push_notice_run(
            session_id=session_id,
            agent_name=agent_name,
            text="[系统] 上次会话因服务异常退出而中断，无法还原消息内容。",
        )
        await _delete_persisted_events(session_id)
        await _mark_interrupted(email, session_id)
        return

    # Append trailing notice so users see the interruption hint.
    msg_dicts.append({
        "role": "system",
        "content": "[系统] 此轮回复在服务异常退出时中断，恢复后内容可能不完整。",
        "created_at": time.time(),
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
    })

    # Step 3 — push recovered run into agno_sessions -------------------
    await _push_recovered_run(
        session_id=session_id,
        agent_name=agent_name,
        msg_dicts=msg_dicts,
    )

    # Step 4 — delete now-merged events --------------------------------
    await _delete_persisted_events(session_id)

    # Step 5 — flip run_status to "interrupted" ------------------------
    await _mark_interrupted(email, session_id)

    logger.info(
        "Recovery: session=%s recovered (%d events → %d messages)",
        session_id, len(events), len(msg_dicts),
    )


# ---------------------------------------------------------------------------
# Core write: $push run dict directly onto agno_sessions
# ---------------------------------------------------------------------------

async def _push_recovered_run(
    session_id: str,
    agent_name: str,
    msg_dicts: List[Dict[str, Any]],
) -> None:
    """Build Agno Message objects from aggregated event dicts and persist them
    as a recovered run via the shared ``persist_partial_run`` helper.

    The helper builds the ``RunOutput`` (injecting ``agent_id:null`` for Bug B)
    and ``$push``-es it onto ``agno_sessions.runs[]`` via Motor. Shared with
    ``api/agent_os_adapter.py`` so both paths produce identical run dicts.
    """
    from agno.models.message import Message
    from core.partial_run_persist import persist_partial_run

    messages: List[Message] = []
    for m in msg_dicts:
        kwargs: Dict[str, Any] = {"role": m["role"]}
        if m.get("content") is not None:
            kwargs["content"] = m["content"]
        if m.get("tool_call_id"):
            kwargs["tool_call_id"] = m["tool_call_id"]
        if m.get("tool_calls"):
            kwargs["tool_calls"] = m["tool_calls"]
        if m.get("name"):
            kwargs["name"] = m["name"]
        try:
            messages.append(Message(**kwargs))
        except Exception:
            logger.debug("Recovery: skipping malformed message %r", m, exc_info=True)

    if not messages:
        logger.info("Recovery: no Agno Message objects for session=%s", session_id)
        return

    await persist_partial_run(session_id, messages, agent_name=agent_name or None)


async def _push_notice_run(session_id: str, agent_name: str, text: str) -> None:
    """Push a single assistant notice message as a run into agno_sessions."""
    from agno.models.message import Message
    from core.partial_run_persist import persist_partial_run

    try:
        msg = Message(role="assistant", content=text)
        await persist_partial_run(session_id, [msg], agent_name=agent_name or None)
    except Exception:
        logger.warning(
            "Recovery: _push_notice_run failed (session=%s)", session_id, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _read_persisted_events(session_id: str) -> List[dict]:
    """Return all events for a session, ordered by seq_id ascending."""
    try:
        from db.models import SessionEvent

        docs = await SessionEvent.find(
            SessionEvent.session_id == session_id,
        ).sort(+SessionEvent.seq_id).to_list()
        return [doc.data for doc in docs]
    except Exception:
        logger.warning(
            "Recovery: read persisted events failed (session=%s)",
            session_id, exc_info=True,
        )
        return []


async def _delete_persisted_events(session_id: str) -> None:
    try:
        from db.models import SessionEvent

        await SessionEvent.find(
            SessionEvent.session_id == session_id,
        ).delete()
    except Exception:
        logger.warning(
            "Recovery: delete persisted events failed (session=%s)",
            session_id, exc_info=True,
        )


async def _mark_interrupted(email: str, session_id: str) -> None:
    try:
        from db.user_sessions import update_run_status

        await update_run_status(email, session_id, "interrupted")
    except Exception:
        logger.warning(
            "Recovery: mark_interrupted failed (email=%s, session=%s)",
            email, session_id, exc_info=True,
        )

