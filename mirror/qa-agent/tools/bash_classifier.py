"""
Bash Command Classifier + Pipeline Analyzer

Classifies shell commands into semantic categories for:
- Concurrency safety decisions
- Permission level inference
- Timeout tier selection
- Read-only detection

References:
  - Claude Code BashTool command classification
  - Pipeline analysis (splitCommandWithOperators)
"""

from __future__ import annotations

import logging
import re
import shlex
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Command Categories
# ═══════════════════════════════════════════════════════════════════════

class CommandCategory(Enum):
    """Semantic category of a shell command."""
    SEARCH = "search"     # find, grep, rg, locate...
    READ = "read"         # cat, head, tail, wc, stat...
    LIST = "list"         # ls, tree, du, dir
    NEUTRAL = "neutral"   # echo, printf, true — don't change pipe semantics
    SILENT = "silent"     # mv, cp, rm, mkdir — success produces no output
    WRITE = "write"       # Default: anything else (python, npm, pip...)


# ── Command Set Constants ───────────────────────────────────────────────

SEARCH_COMMANDS = frozenset({
    "find", "grep", "rg", "ag", "ack", "locate", "which", "whereis",
    "fd",   # fd-find
})

READ_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more",
    "wc", "stat", "file", "strings",
    "jq", "yq", "awk", "cut", "sort", "uniq", "tr",
    "tee",  # reads stdin, writes to file AND stdout (borderline)
    "xxd", "od", "hexdump",
    "diff", "comm", "paste",
    "type",  # Windows: similar to cat/which
})

LIST_COMMANDS = frozenset({
    "ls", "tree", "du", "dir",
    "exa", "eza",  # modern ls replacements
})

NEUTRAL_COMMANDS = frozenset({
    "echo", "printf", "true", "false", ":",
    "test", "[",       # test conditions (exit code only)
    "set", "shopt",    # shell options (don't produce meaningful output)
})

SILENT_COMMANDS = frozenset({
    "mv", "cp", "rm", "mkdir", "rmdir",
    "chmod", "chown", "chgrp",
    "touch", "ln",
    "cd", "pushd", "popd",
    "export", "unset",
    "wait",
    "kill", "pkill",
})

# All read-only categories
_READ_ONLY_CATEGORIES = frozenset({
    CommandCategory.SEARCH,
    CommandCategory.READ,
    CommandCategory.LIST,
    CommandCategory.NEUTRAL,
})

# Pipeline operators (order matters: check longer first)
_PIPE_OPERATORS = re.compile(r'\|\||&&|[|;]')

# Redirect operators that indicate writing
_REDIRECT_WRITE = re.compile(r'(?<![12])>{1,2}|[12]>{1,2}')


# ═══════════════════════════════════════════════════════════════════════
# Pipeline Splitter
# ═══════════════════════════════════════════════════════════════════════

def split_pipeline(command: str) -> List[str]:
    """Split a compound command into individual segments.

    Correctly handles:
    - Pipe operators: |
    - Logical operators: && ||
    - Sequential operators: ;
    - Quoted strings (does not split inside quotes)

    Args:
        command: The full shell command string.

    Returns:
        List of individual command segments (stripped of operators).

    Example:
        >>> split_pipeline("grep foo | sort | head -5")
        ['grep foo', 'sort', 'head -5']
        >>> split_pipeline('echo "a | b" && grep c')
        ['echo "a | b"', 'grep c']
    """
    segments: List[str] = []
    current: List[str] = []
    in_single_quote = False
    in_double_quote = False
    i = 0
    chars = command

    while i < len(chars):
        c = chars[i]

        # Handle escape sequences
        if c == '\\' and i + 1 < len(chars) and not in_single_quote:
            current.append(c)
            current.append(chars[i + 1])
            i += 2
            continue

        # Track quoting state
        if c == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(c)
            i += 1
            continue
        if c == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(c)
            i += 1
            continue

        # Only split on operators outside of quotes
        if not in_single_quote and not in_double_quote:
            # Check for two-char operators first: || &&
            if i + 1 < len(chars):
                two_char = chars[i:i+2]
                if two_char in ("||", "&&"):
                    seg = "".join(current).strip()
                    if seg:
                        segments.append(seg)
                    current = []
                    i += 2
                    continue

            # Single-char operators: | ;
            if c in ("|", ";"):
                seg = "".join(current).strip()
                if seg:
                    segments.append(seg)
                current = []
                i += 1
                continue

        current.append(c)
        i += 1

    # Flush remaining
    seg = "".join(current).strip()
    if seg:
        segments.append(seg)

    return segments


def _extract_base_command(segment: str) -> str:
    """Extract the base command name from a segment.

    Handles:
    - env VAR=val cmd → cmd
    - sudo cmd → cmd
    - Leading variable assignments
    """
    # Strip leading variable assignments (VAR=val)
    parts = segment.strip().split()
    idx = 0
    for idx, part in enumerate(parts):
        if "=" in part and not part.startswith("-"):
            continue
        break

    remaining = parts[idx:]
    if not remaining:
        return ""

    # Skip common wrappers
    cmd = remaining[0]
    wrappers = {"env", "sudo", "nohup", "nice", "time", "command", "builtin", "exec"}
    while cmd in wrappers and len(remaining) > 1:
        remaining = remaining[1:]
        # Skip env's VAR=val args
        while remaining and "=" in remaining[0] and not remaining[0].startswith("-"):
            remaining = remaining[1:]
        cmd = remaining[0] if remaining else ""

    # Handle path-qualified commands: /usr/bin/grep → grep
    if "/" in cmd:
        cmd = cmd.rsplit("/", 1)[-1]
    if "\\" in cmd:
        cmd = cmd.rsplit("\\", 1)[-1]

    # Strip .exe suffix on Windows
    if cmd.endswith(".exe"):
        cmd = cmd[:-4]

    return cmd


def _classify_segment(segment: str) -> CommandCategory:
    """Classify a single command segment."""
    base = _extract_base_command(segment)
    if not base:
        return CommandCategory.NEUTRAL

    base_lower = base.lower()

    if base_lower in SEARCH_COMMANDS:
        return CommandCategory.SEARCH
    if base_lower in READ_COMMANDS:
        return CommandCategory.READ
    if base_lower in LIST_COMMANDS:
        return CommandCategory.LIST
    if base_lower in NEUTRAL_COMMANDS:
        return CommandCategory.NEUTRAL
    if base_lower in SILENT_COMMANDS:
        return CommandCategory.SILENT

    return CommandCategory.WRITE


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def classify_command(command: str) -> CommandCategory:
    """Classify a (possibly compound) command.

    For pipelines, the overall category is determined by the last
    non-NEUTRAL segment. This follows the intuition that
    `grep foo | sort | head -5` is a READ (head) operation.

    Args:
        command: Full shell command string.

    Returns:
        CommandCategory for the overall command.
    """
    segments = split_pipeline(command)
    if not segments:
        return CommandCategory.NEUTRAL

    # Find the last non-neutral segment's category
    last_meaningful = CommandCategory.NEUTRAL
    for seg in segments:
        cat = _classify_segment(seg)
        if cat != CommandCategory.NEUTRAL:
            last_meaningful = cat

    return last_meaningful


def is_read_only_command(command: str) -> bool:
    """Determine if a command is read-only (pipeline-aware).

    A command is read-only if:
    1. ALL segments are in read-only categories (SEARCH/READ/LIST/NEUTRAL)
    2. There are NO write redirections (> >>)

    Args:
        command: Full shell command string.

    Returns:
        True if the command is read-only.
    """
    # Check for write redirections (outside of quotes)
    # Simple heuristic: if > or >> appears outside quotes, it's a write
    stripped = _strip_quoted_content(command)
    if _REDIRECT_WRITE.search(stripped):
        return False

    segments = split_pipeline(command)
    if not segments:
        return True

    for seg in segments:
        cat = _classify_segment(seg)
        if cat not in _READ_ONLY_CATEGORIES:
            return False

    return True


def _strip_quoted_content(command: str) -> str:
    """Replace quoted content with placeholder (for redirect detection)."""
    result = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == '\\' and i + 1 < len(command) and not in_single:
            result.append("__")
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            result.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            result.append(c)
            i += 1
            continue
        if in_single or in_double:
            result.append("_")  # placeholder
        else:
            result.append(c)
        i += 1
    return "".join(result)


# ═══════════════════════════════════════════════════════════════════════
# sed -i Detector
# ═══════════════════════════════════════════════════════════════════════

# Matches: sed -i 's/old/new/g' file.txt
# Also:    sed --in-place 's/old/new/' file.txt
# Also:    sed -i'' 's/old/new/' file.txt  (macOS variant)
_SED_INPLACE_PATTERN = re.compile(
    r"""
    \bsed\s+
    (?:.*\s)?                     # optional flags before -i
    (?:-i(?:\s*'[^']*'|\s*"[^"]*"|\S*)?\s+|--in-place(?:=\S+)?\s+)
    (?:'([^']*)'|"([^"]*)")       # the s/old/new/ pattern in quotes
    \s+(\S+)                      # the file path
    """,
    re.VERBOSE,
)

# Simpler pattern for basic sed -i 's/old/new/g' file
_SED_SUBSTITUTION = re.compile(
    r"s([/|#@])(.+?)\1(.+?)\1([gi]*)"
)


def detect_sed_inplace(command: str) -> Optional[Tuple[str, str, str]]:
    """Detect `sed -i` / `sed --in-place` commands.

    Args:
        command: Shell command string.

    Returns:
        Tuple of (file_path, old_pattern, new_pattern) if sed -i detected,
        None otherwise.
    """
    # Quick check
    if "sed" not in command:
        return None

    # Check for -i or --in-place flag
    has_inplace = bool(
        re.search(r'\bsed\s+.*-i\b', command) or
        re.search(r'\bsed\s+.*--in-place\b', command)
    )
    if not has_inplace:
        return None

    # Try to extract the substitution pattern and file
    match = _SED_INPLACE_PATTERN.search(command)
    if match:
        sed_expr = match.group(1) or match.group(2) or ""
        file_path = match.group(3)

        sub_match = _SED_SUBSTITUTION.search(sed_expr)
        if sub_match:
            old_pattern = sub_match.group(2)
            new_pattern = sub_match.group(3)
            return (file_path, old_pattern, new_pattern)

    # Fallback: just detect that it's sed -i without extracting details
    # This still allows us to block the command and suggest file_edit
    logger.debug("[bash_classifier] Detected sed -i but could not parse substitution")
    return ("unknown", "unknown", "unknown")
