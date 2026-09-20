"""
QA Agent System — CoordinatorWorker DAO

CRUD operations for qa_agent_db.coordinator_workers collection.
Provides persistence for WorkerPool entries so that Coordinator sessions
can be fully restored after process restarts.

Design reference: coordinator-session-restore / coordinator-worker-persistence spec
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_RESULT_MAX_CHARS = 10_000   # truncate Worker result before persistence


def _truncate_result(result: Optional[str]) -> Optional[str]:
    """Truncate Worker result to _RESULT_MAX_CHARS to keep documents lean."""
    if result is not None and len(result) > _RESULT_MAX_CHARS:
        return result[:_RESULT_MAX_CHARS] + "\n…[truncated]"
    return result


async def upsert_worker(
    coordinator_session_id: str,
    entry_dict: Dict[str, Any],
) -> bool:
    """Insert or replace a CoordinatorWorker document.

    Called by WorkerPool.register() after a new worker is created.

    Args:
        coordinator_session_id: Owning Coordinator's Agno session_id.
        entry_dict: WorkerEntry fields as a plain dict.

    Returns:
        True on success, False if Beanie is unavailable or write failed.
    """
    try:
        from db.models import CoordinatorWorker

        result = entry_dict.get("result")
        doc = CoordinatorWorker(
            coordinator_session_id=coordinator_session_id,
            worker_id=entry_dict["worker_id"],
            agent_name=entry_dict["agent_name"],
            description=entry_dict.get("description", ""),
            status=entry_dict.get("status", "creating"),
            session_id=entry_dict.get("session_id", ""),
            created_at=entry_dict.get("created_at", time.time()),
            completed_at=entry_dict.get("completed_at"),
            result=_truncate_result(result),
            error=entry_dict.get("error"),
            turns_used=entry_dict.get("turns_used", 0),
            tools_used=entry_dict.get("tools_used", []),
            context_ids=entry_dict.get("context_ids", []),
            output_artifact_id=entry_dict.get("output_artifact_id"),
        )
        # Use upsert via replace to handle duplicate registrations gracefully
        await CoordinatorWorker.find_one(
            CoordinatorWorker.worker_id == entry_dict["worker_id"]
        ).upsert(
            {"$set": doc.model_dump(exclude={"id"})},
            on_insert=doc,
        )
        logger.debug(
            "coordinator_workers: upserted worker %s for coordinator %s",
            entry_dict["worker_id"], coordinator_session_id,
        )
        return True

    except Exception:
        logger.warning(
            "coordinator_workers: upsert_worker failed for worker_id=%s coordinator=%s",
            entry_dict.get("worker_id"), coordinator_session_id,
            exc_info=True,
        )
        return False


async def update_worker_status(
    worker_id: str,
    status: str,
    *,
    result: Optional[str] = None,
    error: Optional[str] = None,
    turns_used: Optional[int] = None,
    tools_used: Optional[List[str]] = None,
    completed_at: Optional[float] = None,
) -> bool:
    """Update status and outcome fields for a persisted Worker document.

    Called by WorkerPool.update_status() on every status transition.

    Returns True on success, False on failure (non-raising).
    """
    try:
        from db.models import CoordinatorWorker

        update: Dict[str, Any] = {"status": status}
        if result is not None:
            update["result"] = _truncate_result(result)
        if error is not None:
            update["error"] = error
        if turns_used is not None:
            update["turns_used"] = turns_used
        if tools_used is not None:
            update["tools_used"] = tools_used
        if completed_at is not None:
            update["completed_at"] = completed_at

        await CoordinatorWorker.find_one(
            CoordinatorWorker.worker_id == worker_id
        ).update({"$set": update})

        logger.debug(
            "coordinator_workers: updated worker %s → status=%s",
            worker_id, status,
        )
        return True

    except Exception:
        logger.warning(
            "coordinator_workers: update_worker_status failed for worker_id=%s",
            worker_id,
            exc_info=True,
        )
        return False


async def find_by_coordinator(coordinator_session_id: str) -> List[Dict[str, Any]]:
    """Return all CoordinatorWorker documents for a given Coordinator session.

    Args:
        coordinator_session_id: The Coordinator's Agno session_id.

    Returns:
        List of raw field dicts (model_dump), sorted by created_at ascending.
        Empty list if Beanie unavailable or no documents found.
    """
    try:
        from db.models import CoordinatorWorker

        docs = await CoordinatorWorker.find(
            CoordinatorWorker.coordinator_session_id == coordinator_session_id
        ).sort("+created_at").to_list()

        return [doc.model_dump() for doc in docs]

    except Exception:
        logger.warning(
            "coordinator_workers: find_by_coordinator failed for coordinator=%s",
            coordinator_session_id,
            exc_info=True,
        )
        return []


async def downgrade_active_workers(coordinator_session_id: str) -> int:
    """Set status of RUNNING/CREATING workers to FAILED with process_restart error.

    Called during restore to handle workers that were in-flight when the
    process died.

    Returns the number of workers downgraded.
    """
    try:
        from db.models import CoordinatorWorker

        active_statuses = {"running", "creating"}
        docs = await CoordinatorWorker.find(
            CoordinatorWorker.coordinator_session_id == coordinator_session_id,
            CoordinatorWorker.status.is_in(list(active_statuses)),  # type: ignore[attr-defined]
        ).to_list()

        count = 0
        for doc in docs:
            await CoordinatorWorker.find_one(
                CoordinatorWorker.worker_id == doc.worker_id
            ).update({
                "$set": {
                    "status": "failed",
                    "error": "process_restart",
                    "completed_at": time.time(),
                }
            })
            count += 1

        if count:
            logger.info(
                "coordinator_workers: downgraded %d active workers to failed "
                "(coordinator=%s, reason=process_restart)",
                count, coordinator_session_id,
            )
        return count

    except Exception:
        logger.warning(
            "coordinator_workers: downgrade_active_workers failed for coordinator=%s",
            coordinator_session_id,
            exc_info=True,
        )
        return 0
