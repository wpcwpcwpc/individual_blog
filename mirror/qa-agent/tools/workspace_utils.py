"""
QA Agent System — Workspace Path Utilities

Shared helper for resolving file paths against the workspace root
stored in an agent's session_state. Used by file_tools and search_tools.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_workspace_path(agent, path: str) -> str:
    """Resolve a path using the workspace root from agent session state.

    Strategy:
      - Absolute path → unchanged
      - Relative path + workspace set → join with workspace_root
      - Relative path + no workspace  → Path(path).resolve() (current behavior)
    """
    if os.path.isabs(path):
        return path

    workspace_root = None
    if agent is not None:
        state = getattr(agent, "session_state", None)
        if state is not None:
            workspace_root = getattr(state, "workspace_root", None)

    if workspace_root:
        return os.path.join(workspace_root, path)

    return str(Path(path).resolve())
