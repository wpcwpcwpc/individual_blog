"""
QA Agent System — System Utility API Router

Provides system-level endpoints that bridge the web UI to the host OS:
  - POST /system/browse-directory — Open native folder picker dialog
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


class BrowseDirectoryResponse(BaseModel):
    selected_path: str | None = None
    error: str | None = None


def _tkinter_browse() -> str | None:
    """Open a native folder picker using tkinter. Returns selected path or None."""
    import tkinter.filedialog
    import tkinter

    root = tkinter.Tk()
    root.withdraw()  # hide the root window
    root.attributes("-topmost", True)

    try:
        path = tkinter.filedialog.askdirectory(
            parent=root,
            title="选择工作区目录",
            initialdir=str(Path.home()),
        )
        if path and isinstance(path, str) and path.strip():
            return str(Path(path).resolve())
        return None
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def _powershell_browse() -> str | None:
    """Fallback: open folder picker via PowerShell (Windows)."""
    ps_script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择工作区目录'
$dialog.ShowNewFolderButton = $false
$result = $dialog.ShowDialog()
if ($result -eq 'OK') { $dialog.SelectedPath } else { '' }
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        path = proc.stdout.strip()
        if path:
            return str(Path(path).resolve())
        return None
    except Exception as exc:
        logger.warning("PowerShell folder picker failed: %s", exc)
        return None


@router.post("/system/browse-directory", response_model=BrowseDirectoryResponse)
async def browse_directory():
    """Open a native OS folder picker dialog and return the selected directory path.

    The dialog runs in the qa-agent Python process. This is a UI convenience
    endpoint — the selected path is still submitted through the normal
    PUT /sessions/{sid}/workspace endpoint.

    On Windows, tkinter is tried first; PowerShell fallback if tkinter fails.
    """
    # Try tkinter first (bundled with most Python distributions)
    try:
        path = _tkinter_browse()
        return BrowseDirectoryResponse(selected_path=path)
    except Exception as exc:
        logger.info("tkinter folder picker failed (%s), trying PowerShell fallback...", exc)

    # PowerShell fallback (Windows only)
    if sys.platform == "win32":
        try:
            path = _powershell_browse()
            return BrowseDirectoryResponse(selected_path=path)
        except Exception as exc:
            logger.warning("PowerShell folder picker also failed: %s", exc)
            return BrowseDirectoryResponse(
                error=f"无法打开文件夹选择器。请手动输入路径。({exc})"
            )

    return BrowseDirectoryResponse(
        error="当前平台无法打开文件夹选择器，请手动输入路径。"
    )
