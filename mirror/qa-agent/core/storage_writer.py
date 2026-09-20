"""
QA Agent System — Storage Writer

Write operations on Agno Storage (MongoDB/SQLite/PostgreSQL).
Complements ``core/storage_reader.py`` which handles reads.

Currently provides:
  - ``truncate_session_messages``: Remove messages from a session by
    modifying the AgentSession.runs structure and writing back via
    ``storage.upsert_session()``.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TruncateOutcome:
    """Storage-level truncation result.

    ``total_runs`` / ``total_messages`` describe the post-truncation storage
    state and feed the D14 ``history_version`` tuple.
    """

    deleted_count: int
    total_runs: int
    total_messages: int


async def truncate_session_messages(session_id: str, keep_count: int) -> TruncateOutcome:
    """Truncate a session's messages, keeping only the first *keep_count*.

    Reads the full ``AgentSession`` from Agno Storage, walks through
    ``runs[*].messages``, removes messages beyond the keep boundary,
    and writes the modified session back via ``upsert_session()``.

    Run boundaries are preserved: if a run falls entirely after the
    keep boundary its messages are cleared (or the run is dropped).
    If the boundary falls within a run, only that run is truncated.

    Args:
        session_id: The session UUID.
        keep_count: Number of messages to keep (0-based from the start).
                    If 0, all messages are removed.

    Returns:
        TruncateOutcome with the deleted count and the post-truncation
        run/message totals (history_version inputs).

    Raises:
        ValueError: If the session is not found in storage.
        RuntimeError: If upsert_session fails.
    """
    from core.storage import get_storage
    from agno.db.base import SessionType

    storage = get_storage()

    # --- Read ---
    get_fn = getattr(storage, "get_session", None)
    if get_fn is None:
        raise RuntimeError("Agno storage backend does not support get_session()")

    if inspect.iscoroutinefunction(get_fn):
        agent_session = await get_fn(session_id, session_type=SessionType.AGENT)
    else:
        agent_session = get_fn(session_id, session_type=SessionType.AGENT)

    if agent_session is None:
        raise ValueError(f"Session {session_id} not found in storage")

    # --- Count total messages across all runs ---
    runs = getattr(agent_session, "runs", None) or []
    total_count = 0
    for run in runs:
        msgs = getattr(run, "messages", None) or []
        total_count += len(msgs)

    if keep_count >= total_count:
        # Nothing to truncate
        return TruncateOutcome(deleted_count=0, total_runs=len(runs), total_messages=total_count)

    deleted_count = total_count - keep_count

    # --- Truncate runs structure ---
    remaining = keep_count
    runs_to_keep = []

    for run in runs:
        msgs = getattr(run, "messages", None) or []
        if remaining <= 0:
            # This run is entirely beyond the keep boundary — drop it
            # Clear messages to empty list (in case run object is reused)
            if hasattr(run, "messages"):
                run.messages = []
            continue

        if remaining >= len(msgs):
            # This run is fully within the keep boundary — keep as-is
            remaining -= len(msgs)
            runs_to_keep.append(run)
        else:
            # Boundary falls within this run — truncate
            run.messages = msgs[:remaining]
            remaining = 0
            runs_to_keep.append(run)

    # Update the session's runs list
    agent_session.runs = runs_to_keep

    # --- Write back ---
    upsert_fn = getattr(storage, "upsert_session", None)
    if upsert_fn is None:
        raise RuntimeError("Agno storage backend does not support upsert_session()")

    try:
        if inspect.iscoroutinefunction(upsert_fn):
            await upsert_fn(session=agent_session)
        else:
            upsert_fn(session=agent_session)
    except Exception as exc:
        logger.error(
            "Failed to upsert truncated session %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to persist truncated session: {exc}") from exc

    logger.info(
        "Truncated session %s: kept %d messages, deleted %d",
        session_id,
        keep_count,
        deleted_count,
    )
    return TruncateOutcome(
        deleted_count=deleted_count,
        total_runs=len(runs_to_keep),
        total_messages=keep_count,
    )


async def drop_session_run(session_id: str, run_id: str) -> int:
    """Remove a single run (by run_id) from a session's runs list.

    去重收尾：completed 边界 run 续跑时 agno auto-fork 会把
    克隆 run（含 [前缀…U…新回复]）追加为兄弟 run，原边界 run（截断后仍含 U）若
    保留，读路径 flatten 会出现重复 U —— 续跑成功后 MUST 删除原边界 run。

    Args:
        session_id: The session UUID.
        run_id: The run to remove (message content goes with it).

    Returns:
        Number of messages removed with the run (0 if run not found).

    Raises:
        ValueError: If the session is not found in storage.
        RuntimeError: If upsert_session fails.
    """
    from core.storage import get_storage
    from agno.db.base import SessionType

    storage = get_storage()

    get_fn = getattr(storage, "get_session", None)
    if get_fn is None:
        raise RuntimeError("Agno storage backend does not support get_session()")

    if inspect.iscoroutinefunction(get_fn):
        agent_session = await get_fn(session_id, session_type=SessionType.AGENT)
    else:
        agent_session = get_fn(session_id, session_type=SessionType.AGENT)

    if agent_session is None:
        raise ValueError(f"Session {session_id} not found in storage")

    runs = getattr(agent_session, "runs", None) or []
    target = next((r for r in runs if getattr(r, "run_id", None) == run_id), None)
    if target is None:
        logger.warning(
            "drop_session_run: run %s not found in session %s (already dropped?)",
            run_id, session_id,
        )
        return 0

    removed = len(getattr(target, "messages", None) or [])
    agent_session.runs = [r for r in runs if getattr(r, "run_id", None) != run_id]

    upsert_fn = getattr(storage, "upsert_session", None)
    if upsert_fn is None:
        raise RuntimeError("Agno storage backend does not support upsert_session()")

    try:
        if inspect.iscoroutinefunction(upsert_fn):
            await upsert_fn(session=agent_session)
        else:
            upsert_fn(session=agent_session)
    except Exception as exc:
        logger.error(
            "Failed to upsert session %s after dropping run %s: %s",
            session_id, run_id, exc,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to persist session after run drop: {exc}") from exc

    logger.info(
        "Dropped run %s from session %s (%d messages removed, regenerate dedup)",
        run_id, session_id, removed,
    )
    return removed