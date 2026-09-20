"""
QA Agent System — Workspace API Router

Provides REST endpoints for workspace management:
  - PUT    /sessions/{sid}/workspace       — Set/update workspace
  - GET    /sessions/{sid}/workspace       — Get workspace info + root tree
  - GET    /sessions/{sid}/workspace/tree  — Lazy-load subdirectory
  - DELETE /sessions/{sid}/workspace       — Remove workspace binding
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from api.schemas import (
    SetWorkspaceRequest,
    TreeEntry,
    TreeResponse,
    WorkspaceResponse,
)
from api.session_manager import session_manager
from db.workspace import delete_workspace as dao_delete, get_workspace as dao_get, set_workspace as dao_set

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspace"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_directory(
    abs_path: str,
    rel_prefix: str = "",
    *,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[TreeEntry]:
    """Read one level of a directory and return TreeEntry list.

    Args:
        abs_path: Absolute path to the directory to read.
        rel_prefix: Prefix for building relative paths (relative to workspace root).
        limit: Max entries to return. None = no limit (extensibility placeholder).
        offset: Number of entries to skip (extensibility placeholder).

    Returns:
        List of TreeEntry sorted: directories first, then files, both alphabetical.
    """
    p = Path(abs_path)
    if not p.is_dir():
        raise FileNotFoundError(f"Not a directory: {abs_path}")

    entries: List[TreeEntry] = []
    try:
        children = sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
    except PermissionError:
        logger.warning("Permission denied reading directory: %s", abs_path)
        return entries

    # Apply offset/limit (placeholder for future pagination)
    if offset > 0:
        children = children[offset:]
    if limit is not None:
        children = children[:limit]

    for child in children:
        rel_path = f"{rel_prefix}/{child.name}".lstrip("/") if rel_prefix else child.name
        if child.is_dir():
            # Check if directory has children
            try:
                has_children = any(True for _ in child.iterdir())
            except PermissionError:
                has_children = False
            entries.append(TreeEntry(
                name=child.name,
                type="dir",
                path=rel_path,
                has_children=has_children,
            ))
        elif child.is_file():
            try:
                size = child.stat().st_size
            except OSError:
                size = None
            entries.append(TreeEntry(
                name=child.name,
                type="file",
                path=rel_path,
                size=size,
            ))

    return entries


def _get_session_workspace_root(session_id: str) -> Optional[str]:
    """Get workspace_root from in-memory session state, if available."""
    record = session_manager.get(session_id)
    if record and record.agent:
        state = getattr(record.agent, "session_state", None)
        if state:
            return getattr(state, "workspace_root", None)
    return None


def _set_session_workspace_root(session_id: str, root_path: Optional[str]) -> None:
    """Update workspace_root in in-memory session state."""
    record = session_manager.get(session_id)
    if record and record.agent:
        state = getattr(record.agent, "session_state", None)
        if state:
            state.workspace_root = root_path


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.put("/sessions/{session_id}/workspace", response_model=WorkspaceResponse)
async def set_workspace(session_id: str, req: SetWorkspaceRequest):
    """Set or update the workspace directory for a session."""
    # Validate session exists
    record = session_manager.get(session_id)
    if record is None:
        # Check persistent storage as well
        exists = await session_manager.exists_in_storage(session_id)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    # Validate path
    root_path = req.root_path.strip()
    if not os.path.isabs(root_path):
        raise HTTPException(status_code=400, detail=f"root_path must be an absolute path: {root_path}")
    if not os.path.isdir(root_path):
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {root_path}")

    # Persist to DB
    await dao_set(session_id, root_path)

    # Update in-memory state
    _set_session_workspace_root(session_id, root_path)

    # Read root directory first level
    tree = _read_directory(root_path)

    return WorkspaceResponse(root_path=root_path, tree=tree)


@router.get("/sessions/{session_id}/workspace", response_model=WorkspaceResponse)
async def get_workspace(session_id: str):
    """Get workspace info and root directory tree for a session.

    Returns 200 with root_path=null and tree=[] if no workspace has been set,
    allowing the frontend to call this unconditionally without getting a 404.
    """
    root_path = await dao_get(session_id)
    if root_path is None:
        # No workspace set — return empty response instead of 404
        return WorkspaceResponse(root_path=None, tree=[])

    # Read root directory first level (from FS, not cached)
    try:
        tree = _read_directory(root_path)
    except FileNotFoundError:
        # Directory no longer exists on disk
        tree = []

    return WorkspaceResponse(root_path=root_path, tree=tree)


@router.get("/sessions/{session_id}/workspace/tree", response_model=TreeResponse)
async def get_workspace_tree(
    session_id: str,
    path: str = Query(default="", description="Relative path to expand (empty = root)"),
):
    """Lazy-load a subdirectory of the workspace."""
    root_path = await dao_get(session_id)
    if root_path is None:
        raise HTTPException(status_code=404, detail="Workspace not set for this session")

    # Build absolute path
    rel_path = path.strip().strip("/\\")
    if rel_path:
        abs_path = os.path.join(root_path, rel_path)
    else:
        abs_path = root_path

    # Security: ensure resolved path is under workspace root
    resolved = os.path.realpath(abs_path)
    root_resolved = os.path.realpath(root_path)
    if not resolved.startswith(root_resolved):
        raise HTTPException(status_code=400, detail="Path escapes workspace root")

    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

    entries = _read_directory(abs_path, rel_prefix=rel_path)
    return TreeResponse(entries=entries)


@router.delete("/sessions/{session_id}/workspace", status_code=204, response_model=None)
async def remove_workspace(session_id: str):
    """Remove the workspace binding for a session."""
    await dao_delete(session_id)
    _set_session_workspace_root(session_id, None)
    return None
