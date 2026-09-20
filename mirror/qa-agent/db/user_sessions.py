"""
QA Agent System — UserSession DAO (Beanie ODM)

CRUD operations for qa_agent_db.user_sessions collection.
Uses Beanie Document API instead of raw motor calls.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Legacy-data-tolerant session loading ────────────────────────────
# Old user_sessions documents may contain entries stored as raw JSON
# strings inside the sessions[] array (corrupted writes from earlier
# code paths). Beanie's model validation rejects these and crashes
# every reader. These helpers read via the raw Motor collection and
# coerce/ skip bad entries so the API degrades gracefully instead of
# 500-ing on a single bad document.

_ALLOWED_SESSION_KEYS = {
    "session_id", "agent_name", "mode", "game_version", "module",
    "title", "created_at", "last_active_at", "run_status",
    "worker_agent_names", "plan_context", "hidden",
}


def _coerce_session_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """Coerce a raw sessions[] element into a clean dict, or skip it.

    Accepts dict (pass-through after key filter) or JSON string.
    Returns None for unparseable / non-dict input so callers can skip.
    """
    if isinstance(raw, dict):
        return {k: raw.get(k) for k in _ALLOWED_SESSION_KEYS if k in raw} or None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("skip corrupt session entry (non-JSON str): %r", raw[:120])
            return None
        if isinstance(parsed, dict):
            return {k: parsed.get(k) for k in _ALLOWED_SESSION_KEYS if k in parsed} or None
        logger.warning("skip corrupt session entry (JSON non-dict): %r", raw[:120])
        return None
    logger.warning("skip corrupt session entry (type=%s)", type(raw).__name__)
    return None


async def _load_user_session_raw(email: str) -> Optional[Dict[str, Any]]:
    """Read a UserSession document via raw Motor, returning clean dicts.

    Returns None if no document matches. The ``sessions`` list contains
    only successfully coerced dicts; corrupt entries are skipped with a
    warning. This bypasses Beanie model validation entirely.
    """
    from db.models import UserSession

    coll = UserSession.get_motor_collection()
    doc = await coll.find_one({"email": email})
    if doc is None:
        return None

    sessions: List[Dict[str, Any]] = []
    for raw in doc.get("sessions", []):
        coerced = _coerce_session_entry(raw)
        if coerced is not None:
            sessions.append(coerced)
    doc["sessions"] = sessions
    return doc


async def upsert_session(email: str, session_meta: Dict[str, Any]) -> bool:
    """Add a session entry for a user (upsert by email, push to sessions array).

    Uses raw Motor ``$push`` so a pre-existing corrupt sibling entry in
    sessions[] does not crash the write (Beanie save() re-validates the
    whole array). New entries are always clean dicts matching
    SessionEntry fields.

    Args:
        email: User email address (document primary key).
        session_meta: Dict matching SessionEntry fields.

    Returns:
        True if written successfully, False if Beanie is unavailable.
    """
    try:
        from db.models import SessionEntry, UserSession

        entry = SessionEntry(**session_meta)
        entry_dict = entry.model_dump()
        now = time.time()

        coll = UserSession.get_motor_collection()
        result = await coll.update_one(
            {"email": email},
            {"$push": {"sessions": entry_dict}, "$set": {"updated_at": now}},
        )
        if result.matched_count == 0:
            await coll.insert_one({
                "email": email,
                "sessions": [entry_dict],
                "updated_at": now,
            })

        logger.debug("upsert_session: wrote session %s for %s", session_meta.get("session_id"), email)
        return True

    except Exception:
        logger.warning("upsert_session failed for email=%s", email, exc_info=True)
        return False


async def get_sessions(email: str) -> List[Dict[str, Any]]:
    """Return session list for a user, sorted by last_active_at descending.

    Returns:
        List of session metadata dicts. Empty list if user not found.
    """
    try:
        doc = await _load_user_session_raw(email)
        if doc is None:
            return []

        sessions = list(doc["sessions"])
        sessions.sort(key=lambda s: s.get("last_active_at", 0), reverse=True)
        return sessions

    except Exception:
        logger.warning("get_sessions failed for email=%s", email, exc_info=True)
        return []


async def update_session_meta(
    email: str,
    session_id: str,
    *,
    title: Optional[str] = None,
    last_active_at: Optional[float] = None,
    hidden: Optional[bool] = None,
    force: bool = False,
) -> bool:
    """Partially update a session entry (title, last_active_at, and/or hidden).

    Only updates fields that are explicitly passed (not None).
    By default title is only set once (first message); pass ``force=True``
    to overwrite an existing title (e.g. user editing).
    Uses raw Motor positional ``$`` update so a single corrupt sibling
    entry in sessions[] does not crash the write (Beanie save() would
    re-validate the whole array).
    Returns True on success, False if Beanie is unavailable or entry not found.
    """
    try:
        from db.models import UserSession

        set_fields: Dict[str, Any] = {"updated_at": time.time()}
        if title is not None:
            doc = await _load_user_session_raw(email)
            if doc is None:
                return False
            current = next((s for s in doc["sessions"] if s.get("session_id") == session_id), None)
            if current is None:
                return False
            if force or not current.get("title"):
                set_fields["sessions.$.title"] = title
        if last_active_at is not None:
            set_fields["sessions.$.last_active_at"] = last_active_at
        if hidden is not None:
            set_fields["sessions.$.hidden"] = hidden

        if len(set_fields) == 1:  # only updated_at, nothing to do
            return True

        coll = UserSession.get_motor_collection()
        result = await coll.update_one(
            {"email": email, "sessions.session_id": session_id},
            {"$set": set_fields},
        )
        if result.matched_count == 0:
            logger.debug(
                "update_session_meta: no match (email=%s, session_id=%s)", email, session_id,
            )
            return False
        logger.debug(
            "update_session_meta: updated session %s for %s (title=%r, last_active_at=%s)",
            session_id, email, title, last_active_at,
        )
        return True

    except Exception:
        logger.warning(
            "update_session_meta failed for email=%s session_id=%s",
            email, session_id, exc_info=True,
        )
        return False


async def get_session_meta(email: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Return metadata for a specific session belonging to a user.

    Returns:
        Session metadata dict, or None if not found.
    """
    try:
        doc = await _load_user_session_raw(email)
        if doc is None:
            return None

        for entry in doc["sessions"]:
            if entry.get("session_id") == session_id:
                return entry
        return None

    except Exception:
        logger.warning(
            "get_session_meta failed for email=%s session_id=%s",
            email, session_id, exc_info=True,
        )
        return None


async def update_run_status(email: str, session_id: str, status: str) -> bool:
    """Update the run_status field of a specific SessionEntry.

    Uses MongoDB positional ``$`` operator for an atomic, low-overhead update
    of a single nested array element. This is called frequently (run start /
    end / abort) and must be cheap.

    Args:
        email: User email (document key).
        session_id: Target session within UserSession.sessions[].
        status: One of "idle" | "running" | "interrupted".

    Returns:
        True if a document was matched and the update issued, False if Beanie
        is unavailable or the document was not found.
    """
    try:
        from db.models import UserSession

        coll = UserSession.get_motor_collection()
        result = await coll.update_one(
            {"email": email, "sessions.session_id": session_id},
            {
                "$set": {
                    "sessions.$.run_status": status,
                    "updated_at": time.time(),
                }
            },
        )
        if result.matched_count == 0:
            logger.debug(
                "update_run_status: no matching session (email=%s, session_id=%s)",
                email, session_id,
            )
            return False
        logger.debug(
            "update_run_status: %s → %s (email=%s)", session_id, status, email,
        )
        return True

    except Exception:
        logger.warning(
            "update_run_status failed (email=%s, session_id=%s, status=%s)",
            email, session_id, status, exc_info=True,
        )
        return False


async def find_orphan_sessions() -> List[Dict[str, Any]]:
    """Return all SessionEntry records whose run_status == "running".

    These are sessions whose previous run did not finish cleanly — the process
    was killed before the lifecycle code could update run_status to "idle".
    The startup recovery hook iterates this list to merge buffered events
    from session_events back into agno_sessions.

    Returns:
        List of dicts with keys: email, session_id, agent_name, mode,
        game_version, module, title, created_at, last_active_at.
        Empty list if Beanie is unavailable or no orphans exist.
    """
    try:
        from db.models import UserSession

        orphans: List[Dict[str, Any]] = []
        coll = UserSession.get_motor_collection()
        async for doc in coll.find({"sessions.run_status": "running"}):
            email = doc.get("email")
            for raw in doc.get("sessions", []):
                entry = _coerce_session_entry(raw)
                if entry is None:
                    continue
                if entry.get("run_status") == "running":
                    orphans.append({"email": email, **entry})
        return orphans

    except Exception:
        logger.warning("find_orphan_sessions failed", exc_info=True)
        return []


async def update_session_plan_context(
    email: str,
    session_id: str,
    plan_context: str,
) -> bool:
    """Update the plan_context field of a specific coordinator SessionEntry.

    Uses MongoDB positional ``$`` operator for an atomic, low-overhead update.
    Called (fire-and-forget) after Phase 0 PlanAgent completes so the plan
    can be restored if the session is ever revived.

    Args:
        email: User email (document key).
        session_id: Target session within UserSession.sessions[].
        plan_context: Plan text (should be pre-truncated to ≤3000 chars).

    Returns:
        True if matched and updated, False otherwise.
    """
    try:
        from db.models import UserSession

        coll = UserSession.get_motor_collection()
        result = await coll.update_one(
            {"email": email, "sessions.session_id": session_id},
            {
                "$set": {
                    "sessions.$.plan_context": plan_context,
                    "updated_at": time.time(),
                }
            },
        )
        if result.matched_count == 0:
            logger.debug(
                "update_session_plan_context: no match (email=%s, session_id=%s)",
                email, session_id,
            )
            return False
        logger.debug("update_session_plan_context: updated session %s", session_id)
        return True

    except Exception:
        logger.warning(
            "update_session_plan_context failed (email=%s, session_id=%s)",
            email, session_id, exc_info=True,
        )
        return False


async def update_session_plan_context_by_session_id(
    session_id: str,
    plan_context: str,
) -> bool:
    """Update plan_context for a session by session_id alone (no email required).

    Scans all UserSession documents that contain the given session_id.
    Slightly less efficient than update_session_plan_context() but useful
    when the user email is not available at the call site (e.g. from
    coordinator_agent.py background task).

    Returns:
        True if any document was updated, False otherwise.
    """
    try:
        from db.models import UserSession

        coll = UserSession.get_motor_collection()
        result = await coll.update_one(
            {"sessions.session_id": session_id},
            {
                "$set": {
                    "sessions.$.plan_context": plan_context,
                    "updated_at": time.time(),
                }
            },
        )
        if result.matched_count == 0:
            logger.debug(
                "update_session_plan_context_by_session_id: no match for session_id=%s",
                session_id,
            )
            return False
        logger.debug(
            "update_session_plan_context_by_session_id: updated session %s (%d chars)",
            session_id, len(plan_context),
        )
        return True

    except Exception:
        logger.warning(
            "update_session_plan_context_by_session_id failed for session_id=%s",
            session_id, exc_info=True,
        )
        return False


async def remove_session(email: str, session_id: str) -> bool:
    """Remove a specific session entry from the user's sessions array.

    Uses MongoDB $pull to atomically remove the SessionEntry with the given
    session_id from the UserSession.sessions array. Raw Motor so corrupt
    sibling entries do not crash the operation.

    Returns:
        True if the document was found and updated, False otherwise.
    """
    try:
        from db.models import UserSession

        coll = UserSession.get_motor_collection()
        result = await coll.update_one(
            {"email": email},
            {"$pull": {"sessions": {"session_id": session_id}},
             "$set": {"updated_at": time.time()}},
        )
        if result.matched_count == 0:
            logger.debug("remove_session: no doc for email=%s", email)
            return False
        logger.debug("remove_session: pulled session %s for %s", session_id, email)
        return True

    except Exception:
        logger.warning(
            "remove_session failed for email=%s session_id=%s",
            email, session_id, exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Module-level convenience object (keeps backward compat with callers using
# user_sessions_dao.upsert_session() etc.)
# ---------------------------------------------------------------------------

class _UserSessionsDAO:
    """Thin wrapper that delegates to module-level functions."""
    upsert_session = staticmethod(upsert_session)
    get_sessions = staticmethod(get_sessions)
    get_session_meta = staticmethod(get_session_meta)
    update_session_meta = staticmethod(update_session_meta)
    update_run_status = staticmethod(update_run_status)
    update_session_plan_context = staticmethod(update_session_plan_context)
    update_session_plan_context_by_session_id = staticmethod(update_session_plan_context_by_session_id)
    find_orphan_sessions = staticmethod(find_orphan_sessions)
    remove_session = staticmethod(remove_session)


user_sessions_dao = _UserSessionsDAO()