"""
QA Agent System — Search Tools

Provides grep (L1) for regex-based file content search with path filtering.

Compatible with Agno 2.5.14+
"""

from __future__ import annotations

import logging
import os
import re
import time as time_mod
from pathlib import Path
from typing import Optional

from agno.tools import tool

from .workspace_utils import resolve_workspace_path as _resolve_workspace_path

logger = logging.getLogger(__name__)

# ── Hard limits ────────────────────────────────────────────────────────
MAX_MATCHES = 50
MAX_FILES_SCANNED = 2000      # terminate scan after this many files
MAX_DEPTH_DEFAULT = 8          # max directory depth from base (0 = base itself)
SEARCH_TIMEOUT_S = 30          # wall-clock timeout for the whole scan

# ── Directory blacklist ─────────────────────────────────────────────────
SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".svn", ".hg",
    "Library",              # Unity
    "Intermediate",         # Unreal Engine
    "DerivedDataCache",     # Unreal Engine
    "Build", "build",
    ".vs", ".idea",
})


@tool(
    name="grep",
    description=(
        "Regex search across files. Returns matches with file path and line number. "
        "Always narrow path and set file_pattern (e.g. '*.cs') to avoid full-repo scan."
    ),
)
def grep(
    agent,
    pattern: str,
    path: str = ".",
    file_pattern: Optional[str] = None,
    case_sensitive: bool = False,
    max_depth: int = MAX_DEPTH_DEFAULT,
) -> str:
    """Search for a regex pattern in files.

    Args:
        pattern: Regular expression pattern to search for.
        path: Directory to search in (recursively). Default is current directory.
              **Always narrow this** — use a specific subdirectory, never '.' in a large repo.
        file_pattern: Optional glob pattern to filter files (e.g. "*.py", "*.ts").
        case_sensitive: Whether the search is case-sensitive. Default False.
        max_depth: Max directory levels to descend. Default 8. Increase if needed.

    Returns:
        Matching lines with file:line format, capped at 50 matches.
    """
    deadline = time_mod.monotonic() + SEARCH_TIMEOUT_S

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"

    resolved_path = _resolve_workspace_path(agent, path)
    base = Path(resolved_path).resolve()
    if not base.exists():
        return f"Error: Directory not found: {path}"
    if not base.is_dir():
        return f"Error: Not a directory: {path}"

    # Skip binary / very large files
    skip_extensions = {
        ".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
        ".mp3", ".mp4", ".avi", ".mkv", ".wav",
        ".db", ".sqlite", ".sqlite3",
        ".woff", ".woff2", ".ttf", ".eot",
    }
    max_file_size = 1_000_000  # 1 MB

    matches = []
    files_scanned = 0
    files_skipped_depth = 0
    files_skipped_dir = 0

    # ── Walk the tree with depth + dir-blacklist filtering ────────────
    def _iter_files():
        """Generator: yield (Path, relative_depth) for each candidate file."""
        base_depth = len(base.parts)
        for dirpath_str, dirnames, filenames in os.walk(str(base)):
            # Wall-clock check
            if time_mod.monotonic() > deadline:
                return

            dirpath = Path(dirpath_str)
            rel_depth = len(dirpath.parts) - base_depth

            # Depth guard — skip deeper directories entirely
            if rel_depth >= max_depth:
                dirnames.clear()  # prevent os.walk from descending further
                continue

            # Directory blacklist — prune before descending
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS
            ]

            # file_pattern filtering
            for fname in sorted(filenames):
                full = dirpath / fname
                if file_pattern and not full.match(file_pattern):
                    continue
                if full.suffix.lower() in skip_extensions:
                    continue
                yield full

    for fpath in _iter_files():
        # Depth check (already filtered in _iter_files, double-check for safety)
        rel_depth = len(fpath.parts) - len(base.parts)
        if rel_depth > max_depth:
            files_skipped_depth += 1
            continue

        # Timeout check
        if time_mod.monotonic() > deadline:
            break

        # Scan ceiling
        if files_scanned >= MAX_FILES_SCANNED:
            break

        try:
            size = fpath.stat().st_size
        except OSError:
            continue
        if size > max_file_size or size == 0:
            continue

        files_scanned += 1

        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                rel = fpath.relative_to(base)
                display_line = line.strip()
                if len(display_line) > 200:
                    display_line = display_line[:200] + "..."
                matches.append(f"{rel}:{line_no}: {display_line}")

                if len(matches) >= MAX_MATCHES:
                    tip = _build_grep_tip(files_scanned, MAX_FILES_SCANNED, path, base, time_mod.monotonic() > deadline)
                    matches.append(tip)
                    return "\n".join(matches)

    # ── Build result footer ───────────────────────────────────────────
    footer_parts = []
    hit_timeout = time_mod.monotonic() > deadline
    hit_scan_limit = files_scanned >= MAX_FILES_SCANNED

    if hit_timeout:
        footer_parts.append(f"⚠ Timed out after {SEARCH_TIMEOUT_S}s.")
    if hit_scan_limit:
        footer_parts.append(f"⚠ Scanned {files_scanned} files (limit {MAX_FILES_SCANNED}).")
    if files_skipped_depth:
        footer_parts.append(f"{files_skipped_depth} files skipped (depth > {max_depth}).")
    if not (hit_timeout or hit_scan_limit):
        footer_parts.append(f"Scanned {files_scanned} files.")

    if not matches and (hit_timeout or hit_scan_limit):
        footer_parts.append(_build_grep_tip(files_scanned, MAX_FILES_SCANNED, path, base, hit_timeout))

    footer = "  ".join(footer_parts)

    if not matches:
        return f"No matches found for pattern '{pattern}'.\n\n[{footer}]"

    result = "\n".join(matches)
    result += f"\n\n[Found {len(matches)} matches. {footer}]"
    return result


def _build_grep_tip(
    files_scanned: int,
    max_files: int,
    path_arg: str,
    base: Path,
    timed_out: bool,
) -> str:
    """Build a guidance message to help the LLM narrow its next search."""
    # Suggest common subdirectories near base (up to 2 levels)
    subdir_hint = ""
    if base.is_dir():
        try:
            top_items = sorted(
                p.name for p in base.iterdir()
                if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")
            )[:8]
            if top_items:
                subdir_hint = (
                    "\n  Top-level subdirectories at '{base}': {dirs}".format(
                        base=str(base), dirs=", ".join(top_items),
                    )
                )
        except Exception:
            pass

    reason = "timed out" if timed_out else f"reached {max_files} files scanned"
    guidance = (
        f"\n\n[Search {reason}. To get useful results, narrow the scope:"
        f"\n  - Use a more specific `path` (current: '{path_arg}')"
        f"\n  - Use `file_pattern` to filter by file type (e.g. '*.cs', '*.lua')"
        f"\n  - Use a more precise `pattern`"
        f"{subdir_hint}"
        f"]"
    )
    return guidance
