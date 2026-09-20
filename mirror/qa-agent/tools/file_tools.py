"""
QA Agent System — File Tools (v2: search/replace + validateInput)

Provides file_read (L1), file_edit (L2), and glob_search (L1) tools.

file_edit uses search/replace mode (not full overwrite) with a
multi-step validation chain inspired by Claude Code's FileEditTool.

Compatible with Agno 2.5.14+

References:
  - Claude Code FileEditTool validateInput chain (10 steps)
"""

from __future__ import annotations

import glob as glob_mod
import hashlib
import logging
import os
import time as time_mod
from pathlib import Path
from typing import Optional

from agno.tools import tool

from .file_state_cache import get_file_state_cache
from .workspace_utils import resolve_workspace_path as _resolve_workspace_path

logger = logging.getLogger(__name__)

# Maximum bytes to return from a single file read
MAX_RESULT_SIZE = 100 * 1024  # 100 KB

# ── glob_search limits ─────────────────────────────────────────────────
GLOB_MAX_RESULTS = 200
GLOB_MAX_FILES_SCANNED = 2000
GLOB_TIMEOUT_S = 30
GLOB_MAX_DEPTH_DEFAULT = 8
GLOB_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".svn", ".hg",
    "Library", "Intermediate", "DerivedDataCache",
    "Build", "build", ".vs", ".idea",
})

# ── file_read multi-format dispatch ──────────
# Format dispatch by file extension. Maps extension → format kind, which
# routes to the corresponding _read_* function. Extensions not in this map
# fall through to utf-8 text (the original file_read behavior).
FORMAT_DISPATCH: dict[str, str] = {
    ".txt": "text",
    ".md": "text",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".docx": "docx",
    ".json": "text",   # plain text fallback (no special parsing)
}

# Image extensions route to _read_image (placeholder per spec — returns
# a placeholder string + warning; multimodal injection is a later change).
IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
})


# ═══════════════════════════════════════════════════════════════════════
# file_read  (L1, read-only)
# ═══════════════════════════════════════════════════════════════════════

def detect_format(path: str) -> str:
    """Return the format kind for a path based on its extension.

    Returns one of: "text" / "csv" / "xlsx" / "docx" / "image" / "fallback".
    - "text"      → _read_text (utf-8, with line numbers, like original file_read)
    - "csv"       → _read_csv  (markdown table)
    - "xlsx"      → _read_xlsx (openpyxl → markdown table)
    - "docx"      → _read_docx (zipfile+xml → paragraphs + markdown tables)
    - "image"     → _read_image (placeholder; multimodal injection is later)
    - "fallback"  → _read_text (utf-8 fallback for unknown extensions)

    xmind is intentionally NOT routed here — uploads are rejected at the
    POST /sessions/{sid}/uploads gate (session_uploads.py FORBIDDEN_EXTENSIONS),
    so file_read never sees a .xmind path in the supported flow.
    """
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return FORMAT_DISPATCH.get(ext, "fallback")


def _read_text(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read a text file with line numbers (original file_read behavior).

    Args:
        path: Absolute path to the file (already resolved by caller).
        offset: 0-based line offset.
        limit: Max lines to return.

    Returns:
        Numbered file content, truncated to MAX_RESULT_SIZE if oversized,
        with a footer note when truncated or when more lines are available.
    """
    p = Path(path).resolve()
    raw = p.read_bytes()
    text_full = raw.decode("utf-8", errors="replace")
    lines_all = text_full.splitlines()
    total_lines = len(lines_all)

    # Record read in state cache (so file_edit can enforce read-before-write).
    cache = get_file_state_cache()
    cache.record_read(
        str(p), raw,
        offset=offset, limit=limit, total_lines=total_lines,
    )

    if len(raw) > MAX_RESULT_SIZE:
        text = raw[:MAX_RESULT_SIZE].decode("utf-8", errors="replace")
        truncated = True
    else:
        text = text_full
        truncated = False

    lines = text.splitlines()
    selected = lines[offset: offset + limit]

    numbered = []
    for i, line in enumerate(selected, start=offset + 1):
        numbered.append(f"{i:>6}\t{line}")

    result = "\n".join(numbered)
    if truncated:
        result += f"\n\n[File truncated at {MAX_RESULT_SIZE // 1024}KB. Total size: {len(raw)} bytes]"
    if offset + limit < total_lines:
        result += f"\n\n[Showing lines {offset + 1}-{offset + len(selected)} of {total_lines}. Use offset/limit to read more.]"
    return result


def _read_csv(path: str) -> str:
    """Read a CSV file and return it as a markdown table.

    Uses the stdlib ``csv`` module (handles quoted fields with embedded
    commas and newlines). Multi-line cell values are joined with ``<br>``
    so they render as a single markdown table row. Output is bounded by
    MAX_RESULT_SIZE (truncated with a footer note if exceeded).
    """
    import csv as csv_mod

    p = Path(path).resolve()
    raw = p.read_bytes()
    if not raw.strip():
        return f"CSV 文件为空: {path}"

    text = raw.decode("utf-8-sig", errors="replace")
    # NOTE: pass the raw text (NOT splitlines) so csv.reader can correctly
    # parse quoted fields with embedded newlines (a quoted newline is part
    # of the cell, not a row terminator).
    reader = csv_mod.reader(text.splitlines(keepends=True))
    rows = list(reader)
    # Drop fully-empty trailing rows but keep structural rows.
    rows = [r for r in rows if any(c.strip() != "" for c in r) or len(r) > 1]
    if not rows:
        return f"CSV 文件为空: {path}"

    header = rows[0]
    body = rows[1:]

    # Normalize each cell: replace newlines with <br> so the row stays on one line.
    # Order matters: handle CRLF first, then bare LF/CR, else CRLF would
    # partially survive as "<br>\n" or be eaten as a space.
    def _norm(cell: str) -> str:
        return (
            cell.replace("\r\n", "<br>")
                .replace("\r", "<br>")
                .replace("\n", "<br>")
                .replace("|", "\\|")
        )

    lines = [
        "| " + " | ".join(_norm(c) for c in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        # Pad/truncate to header width so the markdown table is well-formed.
        padded = list(row) + [""] * (len(header) - len(row))
        padded = padded[:len(header)]
        lines.append("| " + " | ".join(_norm(c) for c in padded) + " |")

    result = "\n".join(lines)
    if len(result.encode("utf-8")) > MAX_RESULT_SIZE:
        result = result[:MAX_RESULT_SIZE]
        result += f"\n\n[文件过大，已截断，完整内容请用 offset/limit 参数分页]"
    return result


def _read_xlsx(path: str) -> str:
    """Read an xlsx file's first sheet and return it as a markdown table.

    Uses ``openpyxl.load_workbook(read_only=True, data_only=True)`` so that
    cells with cached formula results are read as their computed values.
    Falls back to a clear error string if openpyxl is missing or the file
    is corrupt. Output is bounded by MAX_RESULT_SIZE.
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        logger.warning("xlsx 解析依赖 openpyxl 未安装，无法读取: %s", path)
        return f"xlsx 解析依赖 openpyxl 未安装，无法读取: {path}"

    p = Path(path).resolve()
    try:
        wb = load_workbook(filename=str(p), read_only=True, data_only=True)
        sheet = wb.active if wb.active is not None else wb.worksheets[0]
        rows = list(sheet.iter_rows(values_only=True))
        wb.close()
    except Exception as e:
        logger.warning("xlsx 解析失败: %s (path=%s)", e, path, exc_info=True)
        return f"xlsx 解析失败: {e}"

    if not rows:
        return f"xlsx 文件为空: {path}"

    def _cell_str(v: object) -> str:
        if v is None:
            return ""
        s = str(v)
        return (
            s.replace("\r\n", "<br>")
             .replace("\r", "<br>")
             .replace("\n", "<br>")
             .replace("|", "\\|")
        )

    header = rows[0]
    body = rows[1:]
    lines = [
        "| " + " | ".join(_cell_str(c) for c in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        padded = list(row) + [None] * (len(header) - len(row))
        padded = padded[:len(header)]
        lines.append("| " + " | ".join(_cell_str(c) for c in padded) + " |")

    result = "\n".join(lines)
    if len(result.encode("utf-8")) > MAX_RESULT_SIZE:
        result = result[:MAX_RESULT_SIZE]
        result += f"\n\n[文件过大，已截断，完整内容请用 offset/limit 参数分页]"
    return result


def _read_docx(path: str) -> str:
    """Read a .docx file and return its text + tables as markdown.

    Uses only the Python stdlib (``zipfile`` + ``xml.etree.ElementTree``) so
    no extra dependency is required. A .docx is a zip archive whose main
    content lives at ``word/document.xml``; elements we care about:

      - ``<w:p>``  paragraph; concatenate its ``<w:t>`` runs to get the text
      - ``<w:tbl>`` table; iterate ``<w:tr>`` (rows) → ``<w:tc>`` (cells) and
        emit a markdown table

    Paragraphs and tables appear in document order, so the output preserves
    the original flow (a paragraph before a table precedes it, etc.). Cells
    with newlines are joined with ``<br>`` to keep each markdown row on one
    line (same convention as ``_read_csv`` / ``_read_xlsx``).

    Degradation: a missing or corrupt ``word/document.xml`` returns a clear
    error string (the agent can still react); we MUST NOT raise — see
    ``_read_image`` for the same pattern. Output bounded by MAX_RESULT_SIZE.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    # Local names with namespace prefix for find/findall (ElementTree wants
    # the ``{ns}localname`` form, not the w:localname form from the raw XML).
    def _q(tag: str) -> str:
        return f"{{{W_NS}}}{tag}"

    P_TAG = _q("p")
    T_TAG = _q("t")
    TBL_TAG = _q("tbl")
    TR_TAG = _q("tr")
    TC_TAG = _q("tc")

    p = Path(path).resolve()
    try:
        with zipfile.ZipFile(str(p)) as zf:
            try:
                with zf.open("word/document.xml") as doc_xml:
                    root = ET.parse(doc_xml).getroot()
            except KeyError:
                logger.warning("docx 缺少 word/document.xml: %s", path)
                return f"docx 文件缺少正文 (word/document.xml): {path}"
    except zipfile.BadZipFile:
        logger.warning("docx 非 zip 格式（损坏或非 docx）: %s", path)
        return f"docx 解析失败: 文件损坏或不是 docx 格式: {path}"
    except Exception as e:
        logger.warning("docx 解析失败: %s (path=%s)", e, path, exc_info=True)
        return f"docx 解析失败: {e}"

    def _cell_str(v: object) -> str:
        if v is None:
            return ""
        s = str(v)
        return (
            s.replace("\r\n", "<br>")
             .replace("\r", "<br>")
             .replace("\n", "<br>")
             .replace("|", "\\|")
        )

    def _para_text(p_elem: ET.Element) -> str:
        """Concatenate all <w:t> runs in a paragraph."""
        return "".join(t.text or "" for t in p_elem.iter(T_TAG))

    def _render_table(tbl: ET.Element) -> list[str]:
        """Render a <w:tbl> as markdown table rows."""
        lines: list[str] = []
        rows = tbl.findall(TR_TAG)
        if not rows:
            return lines
        # First row = header; build column count from header.
        header_cells = [_para_text(tc) or "" for tc in rows[0].findall(TC_TAG)]
        n_cols = len(header_cells)
        if n_cols == 0:
            return lines
        lines.append("| " + " | ".join(_cell_str(c) for c in header_cells) + " |")
        lines.append("| " + " | ".join("---" for _ in header_cells) + " |")
        for tr in rows[1:]:
            cells = [_para_text(tc) or "" for tc in tr.findall(TC_TAG)]
            # Pad/truncate to header width for well-formed markdown.
            padded = list(cells) + [""] * (n_cols - len(cells))
            padded = padded[:n_cols]
            lines.append("| " + " | ".join(_cell_str(c) for c in padded) + " |")
        return lines

    # Walk the body in document order, emitting paragraphs as text and
    # tables as markdown tables. The body is the top-level <w:body> (or the
    # root for some docx variants); we iterate its direct children so the
    # order is preserved (paragraphs interleave with tables).
    body = root.find(_q("body")) or root
    out_lines: list[str] = []
    for child in body:
        if child.tag == P_TAG:
            text = _para_text(child)
            if text:
                out_lines.append(text)
        elif child.tag == TBL_TAG:
            tbl_lines = _render_table(child)
            if tbl_lines:
                # Blank line before/after so markdown table renders separately.
                if out_lines and out_lines[-1] != "":
                    out_lines.append("")
                out_lines.extend(tbl_lines)
                out_lines.append("")

    if not out_lines:
        return f"docx 文件为空: {path}"

    result = "\n".join(out_lines)
    if len(result.encode("utf-8")) > MAX_RESULT_SIZE:
        result = result[:MAX_RESULT_SIZE]
        result += f"\n\n[文件过大，已截断，完整内容请用 offset/limit 参数分页]"
    return result


def _read_image(path: str) -> str:
    """Fallback placeholder for image reading.

    Normal flow: uploaded images are converted to text and registered as
    .txt in session_state.uploads.
    AI reads the .txt via _read_text, so _read_image is NOT invoked in the
    normal upload+file_read flow.

    This function only triggers in abnormal paths — e.g. an agent manually
    calls file_read on an image file path that did not go through the upload
    conversion pipeline. In that case, we return a placeholder and log a
    warning rather than raising, so the agent flow can continue.

    MUST NOT raise: callers (file_read) wrap this in a try/except, but
    raising here would abort the agent's tool call; returning a clear
    placeholder is the safer degradation.
    """
    logger.warning("图片读取能力搁置，未返回内容: %s", path)
    return f"[图片读取搁置: {path}，多模态注入能力后续实现]"


@tool(
    name="file_read",
    description=(
        "Read a file's content with line numbers. "
        "PREFER this instead of bash `type` or PowerShell `Get-Content`. "
        "Use offset and limit to read specific sections of large files. "
        "ALWAYS read a file before editing it. "
        "Supports multi-format dispatch: txt/md (text), csv (markdown table), "
        "xlsx (markdown table via openpyxl), docx (paragraphs + markdown tables "
        "via stdlib zipfile+xml), images (placeholder). "
        "Other extensions fall back to utf-8 text."
    ),
)
def file_read(agent, path: str, offset: int = 0, limit: int = 500) -> str:
    """Read a file and return its content with line numbers.

    Args:
        path: Relative or absolute path to the file.
        offset: Line number to start reading from (0-based). Default 0.
            Only effective for text/csv/xlsx formats.
        limit: Maximum number of lines to return. Default 500.
            Only effective for text/csv/xlsx formats.

    Returns:
        File content with line numbers, or an error message.
    """
    try:
        resolved_path = _resolve_workspace_path(agent, path)
        p = Path(resolved_path).resolve()
        if not p.exists():
            return f"Error: File not found: {path}"
        if not p.is_file():
            return f"Error: Not a file: {path}"

        fmt = detect_format(resolved_path)
        if fmt == "image":
            return _read_image(str(p))
        if fmt == "csv":
            return _read_csv(str(p))
        if fmt == "xlsx":
            return _read_xlsx(str(p))
        if fmt == "docx":
            return _read_docx(str(p))
        # "text" or "fallback" → original utf-8 path
        return _read_text(str(p), offset=offset, limit=limit)

    except Exception as e:
        logger.exception("file_read failed for %s", path)
        return f"Error reading file: {e}"


# ═══════════════════════════════════════════════════════════════════════
# file_edit  (L2, search/replace mode + validateInput)
# ═══════════════════════════════════════════════════════════════════════

def _validate_file_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> Optional[str]:
    """Multi-step validation chain for file_edit.

    Returns None if valid, or a guidance error string if invalid.
    The error string is designed as a "retry prompt" for the LLM.
    """
    p = Path(file_path).resolve()
    abs_path = str(p)

    # Step 1: No-op detection
    if old_string == new_string:
        return (
            "No changes to make: old_string and new_string are identical. "
            "If you intended a different edit, double-check both values."
        )

    # Step 2: Create-new-file mode (old_string == "")
    if old_string == "":
        # Empty old_string → create or append
        if p.exists():
            return (
                f"File already exists: {file_path}. "
                "To edit an existing file, provide the exact old_string to replace. "
                "Use file_read to see the current content first."
            )
        return None  # OK to create

    # Step 3: File existence check
    if not p.exists():
        return (
            f"File not found: {file_path}. "
            "To create a new file, use old_string='' (empty string). "
            "If editing an existing file, check the file path with glob_search."
        )

    if not p.is_file():
        return f"Not a regular file: {file_path}"

    # Step 4: Read-before-write check
    cache = get_file_state_cache()
    record = cache.get_record(abs_path)
    if record is None:
        return (
            f"You must read the file before editing it. "
            f"Use file_read(path='{file_path}') first, then retry the edit. "
            f"This ensures you have the latest content and avoids stale edits."
        )

    # Step 5: External modification detection
    if cache.is_stale(abs_path):
        return (
            f"File has been modified since you last read it. "
            f"Read it again with file_read(path='{file_path}') before editing. "
            f"This prevents overwriting changes made by other processes."
        )

    # Step 6: Partial-read warning
    if record.is_partial:
        # Warning but don't block — the LLM might be editing within the read range
        logger.warning(
            "[file_edit] Editing %s after partial read (offset=%d, limit=%d). "
            "old_string may not be within the read range.",
            file_path, record.offset, record.limit,
        )

    # Step 7: Read file content for match checking
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file for validation: {e}"

    # Step 8: old_string existence check
    if old_string not in content:
        # Try with normalized whitespace (common LLM mistake)
        normalized_old = " ".join(old_string.split())
        normalized_content = " ".join(content.split())
        if normalized_old in normalized_content:
            return (
                f"The old_string was not found with exact whitespace matching, "
                f"but a similar string exists in the file. "
                f"Make sure old_string matches exactly, including indentation and line breaks. "
                f"Re-read the file with file_read to get the exact content."
            )
        return (
            f"The old_string was not found in {file_path}. "
            f"Make sure it matches the file content exactly, "
            f"including whitespace and indentation. "
            f"Re-read the file with file_read(path='{file_path}') to verify."
        )

    # Step 9: Multiple match check
    match_count = content.count(old_string)
    if match_count > 1 and not replace_all:
        return (
            f"Found {match_count} matches for old_string in {file_path}. "
            f"Include more surrounding context lines to make it unique, "
            f"or set replace_all=True to replace all occurrences."
        )

    return None  # All checks passed


@tool(
    name="file_edit",
    description=(
        "Edit a file using search/replace. Provide old_string (exact text to find) "
        "and new_string (replacement text). To create a new file, use old_string='' "
        "with new_string containing the file content. "
        "You MUST read the file with file_read before editing it."
    ),
)
def file_edit(
    agent,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Edit a file by replacing old_string with new_string.

    Args:
        file_path: Path to the file to edit.
        old_string: Exact text to find and replace. Empty string = create new file.
        new_string: Text to replace old_string with.
        replace_all: If True, replace all occurrences. Default False (single match required).

    Returns:
        Success message with line change count, or validation error guidance.
    """
    # ── Resolve path via workspace ──────────────────────────────────────
    file_path = _resolve_workspace_path(agent, file_path)

    # ── Validation chain ────────────────────────────────────────────────
    error = _validate_file_edit(file_path, old_string, new_string, replace_all)
    if error:
        return f"Validation error: {error}"

    # ── Execute edit ────────────────────────────────────────────────────
    try:
        p = Path(file_path).resolve()

        # Create new file mode
        if old_string == "":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new_string, encoding="utf-8")

            # Update state cache for the new file
            cache = get_file_state_cache()
            cache.record_read(str(p), new_string.encode("utf-8"))

            lines = new_string.count("\n") + (1 if new_string and not new_string.endswith("\n") else 0)
            return f"Created new file {file_path} ({lines} lines)"

        # Search/replace mode
        content = p.read_text(encoding="utf-8")

        if replace_all:
            count = content.count(old_string)
            updated = content.replace(old_string, new_string)
        else:
            count = 1
            updated = content.replace(old_string, new_string, 1)

        # Atomic-ish write
        p.write_text(updated, encoding="utf-8")

        # Update state cache with new content
        cache = get_file_state_cache()
        cache.record_read(str(p), updated.encode("utf-8"))

        # Count changed lines for feedback
        old_lines = old_string.count("\n") + 1
        new_lines = new_string.count("\n") + 1
        delta = new_lines - old_lines

        if count == 1:
            return (
                f"Successfully edited {file_path}: "
                f"replaced {old_lines} line(s) with {new_lines} line(s) "
                f"(net {delta:+d})"
            )
        else:
            return (
                f"Successfully edited {file_path}: "
                f"replaced {count} occurrences "
                f"({old_lines}→{new_lines} lines each, net {delta * count:+d})"
            )

    except Exception as e:
        logger.exception("file_edit failed for %s", file_path)
        return f"Error editing file: {e}"


# ═══════════════════════════════════════════════════════════════════════
# glob_search  (L1, read-only)
# ═══════════════════════════════════════════════════════════════════════

@tool(
    name="glob_search",
    description=(
        "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.cs'). "
        "PREFER this instead of bash `dir /s` or PowerShell `Get-ChildItem -Recurse`. "
        "Dedicated tool with depth limiting, directory blacklist, and timeout protection. "
        "Returns a list of matching file paths (max 200 results). "
        "IMPORTANT: Narrow `base_dir` (e.g. 'Assets/Scripts') to avoid scanning "
        "deep directory trees. Use `max_depth` to limit recursion."
    ),
)
def glob_search(
    agent,
    pattern: str,
    base_dir: str = ".",
    max_depth: int = GLOB_MAX_DEPTH_DEFAULT,
) -> str:
    """Search for files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g. "**/*.py", "src/**/*.ts").
        base_dir: Base directory for the search. Default is current directory.
                  **Always narrow this** — use a specific subdirectory.
        max_depth: Max directory levels to descend. Default 8.

    Returns:
        Newline-separated list of matching file paths (max 200 results).
    """
    deadline = time_mod.monotonic() + GLOB_TIMEOUT_S

    try:
        resolved_base = _resolve_workspace_path(agent, base_dir)
        base = Path(resolved_base).resolve()
    except Exception as e:
        logger.exception("glob_search: failed to resolve base_dir %s", base_dir)
        return f"Error resolving directory: {e}"

    if not base.exists():
        return f"Error: Directory not found: {base_dir}"
    if not base.is_dir():
        return f"Error: Not a directory: {base_dir}"

    base_depth = len(base.parts)
    paths: list[str] = []
    files_scanned = 0

    # ── Walk manually for dir-blacklist + depth + timeout + scan limit ──
    for dirpath_str, dirnames, filenames in os.walk(str(base)):
        if time_mod.monotonic() > deadline:
            break

        dirpath = Path(dirpath_str)
        rel_depth = len(dirpath.parts) - base_depth

        # Depth guard
        if rel_depth >= max_depth:
            dirnames.clear()
            continue

        # Directory blacklist
        dirnames[:] = [d for d in dirnames if d not in GLOB_SKIP_DIRS]
        dirnames.sort()

        for fname in sorted(filenames):
            files_scanned += 1
            if files_scanned > GLOB_MAX_FILES_SCANNED:
                break

            full = dirpath / fname
            if full.match(pattern):
                rel = str(full.relative_to(base))
                paths.append(rel)

                if len(paths) >= GLOB_MAX_RESULTS:
                    break

        if files_scanned > GLOB_MAX_FILES_SCANNED or len(paths) >= GLOB_MAX_RESULTS:
            break

    # ── Build result ──────────────────────────────────────────────────
    hit_timeout = time_mod.monotonic() > deadline
    hit_scan_limit = files_scanned >= GLOB_MAX_FILES_SCANNED

    if not paths:
        if hit_timeout:
            subdir_hint = _glob_subdir_hint(base)
            return (
                f"Search timed out after {GLOB_TIMEOUT_S}s "
                f"(scanned {files_scanned} files). "
                f"Try a narrower `base_dir` (current: '{base_dir}') "
                f"or a more specific `pattern`.{subdir_hint}"
            )
        if hit_scan_limit:
            subdir_hint = _glob_subdir_hint(base)
            return (
                f"Scanned {GLOB_MAX_FILES_SCANNED} files without finding a match. "
                f"The search pattern may be too broad or the directory tree too large. "
                f"Try narrowing `base_dir` (current: '{base_dir}').{subdir_hint}"
            )
        return f"No files found matching pattern: {pattern} in {files_scanned} files."

    lines = list(paths)
    footer_parts = []
    if len(paths) >= GLOB_MAX_RESULTS:
        footer_parts.append(f"... and more files (capped at {GLOB_MAX_RESULTS} results)")
    if hit_timeout:
        footer_parts.append(f"⚠ Timed out after {GLOB_TIMEOUT_S}s (scanned {files_scanned} files)")
    if hit_scan_limit and not hit_timeout:
        footer_parts.append(f"⚠ Scanned {GLOB_MAX_FILES_SCANNED} files (limit) — results may be incomplete")
    if footer_parts:
        lines.append(f"\n{'  '.join(footer_parts)}")
    if not lines:
        return ""

    return "\n".join(lines)


def _glob_subdir_hint(base: Path) -> str:
    """Suggest top-level subdirectories for narrowing glob_search."""
    try:
        top = sorted(
            p.name for p in base.iterdir()
            if p.is_dir() and p.name not in GLOB_SKIP_DIRS and not p.name.startswith(".")
        )[:8]
        if top:
            return f"\n  Top-level subdirectories at '{base}': {', '.join(top)}"
    except Exception:
        pass
    return ""