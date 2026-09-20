"""
QA Agent System — Workspace DAO

CRUD operations for WorkspaceRecord (MongoDB workspaces collection).
Each session has at most one workspace binding.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


async def get_workspace(session_id: str) -> Optional[str]:
    """Return the workspace root_path for a session, or None if not set."""
    try:
        from db.models import WorkspaceRecord

        record = await WorkspaceRecord.find_one(
            WorkspaceRecord.session_id == session_id
        )
        return record.root_path if record else None
    except Exception:
        logger.warning("get_workspace failed for session %s", session_id, exc_info=True)
        return None


async def set_workspace(session_id: str, root_path: str) -> None:
    """Create or update the workspace binding for a session."""
    from db.models import WorkspaceRecord

    record = await WorkspaceRecord.find_one(
        WorkspaceRecord.session_id == session_id
    )
    now = time.time()
    if record:
        record.root_path = root_path
        record.updated_at = now
        await record.save()
    else:
        record = WorkspaceRecord(
            session_id=session_id,
            root_path=root_path,
            created_at=now,
            updated_at=now,
        )
        await record.insert()

    logger.info("Workspace set for session %s: %s", session_id, root_path)


async def delete_workspace(session_id: str) -> bool:
    """Delete the workspace binding for a session. Returns True if deleted."""
    try:
        from db.models import WorkspaceRecord

        record = await WorkspaceRecord.find_one(
            WorkspaceRecord.session_id == session_id
        )
        if record:
            await record.delete()
            logger.info("Workspace deleted for session %s", session_id)
            return True
        return False
    except Exception:
        logger.warning("delete_workspace failed for session %s", session_id, exc_info=True)
        return False
