"""
QA Agent System — Bash Tool (v3: full classifier + auto-bg + guided errors)

Intelligent bash/shell execution with:
- 6-category command classification (via bash_classifier)
- Pipeline-aware read-only detection
- Tiered timeouts: SILENT→2s, SEARCH/READ/LIST→15s, WRITE→30s
- Auto-backgrounding after BLOCKING_BUDGET_MS (15s)
- sed -i interception → guides user to file_edit
- sleep / Start-Sleep / timeout interception → guides user to wait_tool
- Guided error messages (errors as retry prompts for LLM)
- Known exit code semantics (grep:1=no match, diff:1=has diff)

Compatible with Agno 2.5.14+

References:
  - Claude Code BashTool
  - StreamingToolExecutor auto-background
"""

from __future__ import annotations

import asyncio
import locale
import logging
import os
import re
import shlex
import sys
import tempfile
import uuid
from typing import List, Optional, Tuple

from agno.tools import tool
from core.config import settings

from .bash_classifier import (
    CommandCategory,
    classify_command,
    is_read_only_command,
    detect_sed_inplace,
    SILENT_COMMANDS,
)
from .background_tasks import BackgroundTaskManager
from .sleep_detector import detect_sleep_command, build_sleep_interception_message

logger = logging.getLogger(__name__)


# ── Tiered Timeouts (seconds) ──────────────────────────────────────────
TIMEOUT_SILENT = 2         # SILENT commands: mkdir, cp, mv
TIMEOUT_READ = 15          # SEARCH/READ/LIST: grep, cat, ls
TIMEOUT_WRITE = settings.max_blocking_seconds   # WRITE / long-running commands
TIMEOUT_BACKGROUND = settings.max_blocking_seconds  # Background tasks max lifetime

# Auto-background threshold (short-timeout fallback, not default path)
BLOCKING_BUDGET_S = 15     # Commands exceeding this get auto-backgrounded

_CATEGORY_TIMEOUT = {
    CommandCategory.SEARCH:  TIMEOUT_READ,
    CommandCategory.READ:    TIMEOUT_READ,
    CommandCategory.LIST:    TIMEOUT_READ,
    CommandCategory.NEUTRAL: TIMEOUT_READ,
    CommandCategory.SILENT:  TIMEOUT_SILENT,
    CommandCategory.WRITE:   TIMEOUT_WRITE,
}


# ── Safety Blacklist ────────────────────────────────────────────────────
COMMAND_BLACKLIST = [
    r"\brm\s+(-rf?|--recursive)\s+/\s*$",   # rm -rf /
    r"\brmdir\s+/\s*$",
    r"\bmkfs\b",
    r"\bdd\s+.*of=/dev/",
    r"\bformat\s+[a-z]:",                     # Windows format
    r":\(\)\{\s*:\|:&\s*\};:",                # Fork bomb
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\binit\s+0\b",
]
_BLACKLIST_COMPILED = [re.compile(p, re.IGNORECASE) for p in COMMAND_BLACKLIST]


# ── Known Exit Code Semantics ──────────────────────────────────────────
# {base_command: {exit_code: (meaning, is_real_error)}}
KNOWN_EXIT_CODES = {
    "grep":   {1: ("No matches found", False), 2: ("Syntax error in pattern", True)},
    "findstr": {1: ("No matches found", False), 2: ("Error", True)},
    "rg":     {1: ("No matches found", False), 2: ("Error occurred", True)},
    "ag":     {1: ("No matches found", False)},
    "diff":   {1: ("Files differ", False), 2: ("Error", True)},
    "test":   {1: ("Condition is false", False)},
    "[":      {1: ("Condition is false", False)},
    "cmp":    {1: ("Files differ", False), 2: ("Error", True)},
}


# Singleton background task manager
_bg_manager = BackgroundTaskManager(default_timeout=TIMEOUT_BACKGROUND)


# ── Internal helpers ────────────────────────────────────────────────────

def _is_command_blocked(command: str) -> Optional[str]:
    for pattern in _BLACKLIST_COMPILED:
        if pattern.search(command):
            return pattern.pattern
    return None


def _effective_timeout(category: CommandCategory, user_timeout: Optional[int]) -> int:
    if user_timeout is not None:
        return user_timeout
    return _CATEGORY_TIMEOUT.get(category, TIMEOUT_WRITE)


def _extract_base_cmd(command: str) -> str:
    """Extract base command name for exit code lookup."""
    parts = command.strip().split()
    if not parts:
        return ""
    cmd = parts[0]
    # Strip path
    if "/" in cmd:
        cmd = cmd.rsplit("/", 1)[-1]
    if "\\" in cmd:
        cmd = cmd.rsplit("\\", 1)[-1]
    if cmd.endswith(".exe"):
        cmd = cmd[:-4]
    return cmd.lower()


def _format_exit_code_info(command: str, exit_code: int) -> str:
    """Return helpful info about known exit codes, or empty string."""
    base = _extract_base_cmd(command)
    known = KNOWN_EXIT_CODES.get(base, {}).get(exit_code)
    if known:
        meaning, is_error = known
        if not is_error:
            return f" (Note: Exit code {exit_code} for '{base}' means: {meaning} — this is not an error)"
    return ""


def _format_timeout_error(
    command: str,
    category: CommandCategory,
    timeout_s: int,
    readonly: bool,
) -> str:
    """Build a guided timeout error message."""
    parts = [f"Error: Command timed out after {timeout_s} seconds."]
    parts.append(f"Command category: {category.name}")

    if readonly:
        parts.append(
            "Hint: This is a read-only command. The search may be too broad — "
            "try narrowing the pattern, path, or adding filters."
        )
    elif _bg_manager.can_auto_background(command):
        parts.append(
            "Hint: Consider re-running with run_in_background=True for long-running commands."
        )

    return "\n".join(parts)


def _format_not_found_error(command: str) -> str:
    base = _extract_base_cmd(command)
    # Check if it looks like PowerShell syntax
    ps_hint = ""
    ps_indicators = ["get-", "set-", "select-", "out-", "invoke-", "write-", "foreach-", "where-"]
    if any(command.lower().startswith(ind) for ind in ps_indicators):
        ps_hint = (
            "Note: This looks like PowerShell syntax. All commands run via cmd.exe — "
            "use CMD commands instead. For example: 'dir' not 'Get-ChildItem', "
            "'findstr' not 'Select-String', 'type' not 'Get-Content'."
        )
    return (
        f"Error: Command '{base}' not found. "
        f"Check that it is installed and available in PATH. "
        f"Use 'where {base}' to locate it on Windows. "
        f"{ps_hint}"
    )


# ── Inline Code Extraction (Windows quote-escaping fix) ────────────────
# On Windows, cmd.exe cannot reliably pass complex inline code to python
# or powershell -Command due to quote mangling. This extractor detects such
# patterns, writes a temp script, and rewrites the command to execute the file.

# Pattern: python[3]? -c "..." or python[3]? -c '...'
# We need to handle the case where -c is followed by inline code
_PYTHON_INLINE_RE = re.compile(
    r'^(python[23]?(?:\.exe)?)\s+-c\s+(.+)$',
    re.IGNORECASE | re.DOTALL,
)

# Pattern: powershell -Command "..." or pwsh -Command "..."
_POWERSHELL_INLINE_RE = re.compile(
    r'^(powershell|pwsh)(?:\.exe)?\s+(?:-Command|-c)\s+(.+)$',
    re.IGNORECASE | re.DOTALL,
)


def _extract_inline_code(code_arg: str) -> str:
    """Strip outer quotes from inline code argument.

    Handles: "code", 'code', or unquoted code.
    Also handles triple-quoted strings.
    """
    code = code_arg.strip()
    # Triple quotes
    for q in ('"""', "'''"):
        if code.startswith(q) and code.endswith(q):
            return code[3:-3]
    # Single/double quotes
    if (code.startswith('"') and code.endswith('"')) or \
       (code.startswith("'") and code.endswith("'")):
        return code[1:-1]
    return code


def _build_inline_temp_path(suffix: str, cwd: Optional[str]) -> str:
    """Build a unique temp file path. cwd wins over gettempdir.

    Uses a uuid suffix to avoid collisions between concurrent tool calls
    (wait_tool polls + bash_tool inline share this helper).
    """
    tmp_dir = cwd or tempfile.gettempdir()
    return os.path.join(tmp_dir, f"_inline_{uuid.uuid4().hex[:8]}.{suffix}")


def rewrite_inline_to_subprocess(
    command: str, cwd: Optional[str] = None,
) -> Optional[Tuple[List[str], str]]:
    """Detect inline `python -c "..."` / `powershell -Command "..."` and rewrite
    to execute a temp script file via a direct subprocess argv list.

    Why a list, not a `cmd /c <string>`?
      cmd.exe mangles nested quotes (cwd trailing backslash + opening quote
      gets eaten), producing bogus argv like `E:\\cwd\\"C:\\path"`. Passing
      `[python_exe, tmp_path]` to create_subprocess_exec bypasses cmd.exe
      entirely — Python receives argv cleanly.

    Args:
        command: Original shell command string.
        cwd: Working dir; temp file lands here when provided.

    Returns:
        (subprocess_argv_list, temp_file_path) if rewritten, else None.
        Caller MUST delete temp_file_path after the subprocess completes.
    """
    if sys.platform != "win32":
        return None

    command_stripped = command.strip()

    # Python -c pattern
    m = _PYTHON_INLINE_RE.match(command_stripped)
    if m:
        python_exe = m.group(1)
        code = _extract_inline_code(m.group(2))
        # Unescape common CMD escape sequences that may have leaked in
        code = code.replace('\\"', '"')

        tmp_path = _build_inline_temp_path("py", cwd)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(code)

        return ([python_exe, tmp_path], tmp_path)

    # PowerShell -Command pattern
    m = _POWERSHELL_INLINE_RE.match(command_stripped)
    if m:
        ps_exe = m.group(1)
        code = _extract_inline_code(m.group(2))
        code = code.replace('\\"', '"')

        tmp_path = _build_inline_temp_path("ps1", cwd)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(code)

        return (
            [ps_exe, "-ExecutionPolicy", "Bypass", "-File", tmp_path],
            tmp_path,
        )

    return None


# Backward-compat alias for any external callers expecting the old name.
_rewrite_inline_to_tempfile = rewrite_inline_to_subprocess


# ── Shlex Direct-Exec (Windows quote-escaping fix, single commands) ────
# On Windows, cmd.exe mangles nested quotes for any command with quoted
# args (not just python -c). For single commands without shell
# metacharacters, bypass cmd.exe entirely via shlex.split + direct
# create_subprocess_exec.

_SHELL_META_TOKENS = frozenset({"&&", "||", "|", ">", "<", ">>", "<<", "&"})
_ENV_VAR_RE = re.compile(r'%[A-Za-z_][A-Za-z0-9_]*%')
_REDIRECT_NUM_RE = re.compile(r'^\d+[<>]')


def _has_shell_metacharacters(tokens: List[str]) -> bool:
    """Detect shell metacharacters in shlex-parsed tokens.

    Detection runs AFTER shlex.split, so metacharacters inside quotes
    (e.g. `pip install "requests>=2.28"`) are already stripped to literal
    tokens and do NOT trigger fallback. Only bare tokens like `&&`, `|`,
    `>`, `%VAR%`, `2>nul` force cmd /c fallback.

    Also detects explicit `cmd /c` prefix — agent wrapped the command in
    cmd /c on purpose (e.g. for compound commands); respect that intent
    and let cmd /c handle it.
    """
    if len(tokens) >= 2 and tokens[0].lower() == "cmd":
        # Explicit cmd invocation — agent wants cmd.exe interpretation.
        # Common forms: `cmd /c "..."`, `cmd /k ...`, `cmd /c ... && ...`
        return True
    for tok in tokens:
        if tok in _SHELL_META_TOKENS:
            return True
        if _ENV_VAR_RE.search(tok):
            return True
        if _REDIRECT_NUM_RE.match(tok):
            return True
    return False


def _shlex_direct_exec_argv(command: str) -> Optional[List[str]]:
    """Try to parse command into argv list suitable for direct
    create_subprocess_exec, bypassing cmd.exe.

    Returns:
        argv list if command is a single command without shell
        metacharacters (safe for direct exec).
        None if command should fall back to cmd /c (compound commands,
        pipes, redirects, env vars, or shlex parse failure).
    """
    if sys.platform != "win32":
        return None  # Non-Windows: original subprocess path handles it

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        # Unclosed quotes etc → let cmd /c try
        return None

    if not tokens:
        return None

    if _has_shell_metacharacters(tokens):
        return None  # Compound/pipe/redirect → cmd /c fallback

    return tokens


# ── Main Tool ───────────────────────────────────────────────────────────

@tool(
    name="bash",
    description=(
        "Run a Windows CMD command (use CMD syntax, not PowerShell). "
        "Use ONLY when no dedicated tool fits: python/git/pip/npm/build commands. "
        "PREFER file_read / file_edit / grep / glob_search / wait over bash equivalents. "
        "Set run_in_background=true for long-running tasks."
    ),
)
async def bash(
    command: str,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    description: str = "",
    run_in_background: bool = False,
) -> str:
    """Execute a Windows CMD command (cmd.exe) with intelligent classification and timeouts.

    This tool runs commands via cmd.exe on Windows. Do NOT use PowerShell syntax.

    Args:
        command: The CMD command to execute. Must use cmd.exe syntax (not PowerShell).
        timeout: Override timeout in seconds. If omitted, auto-determined.
        cwd: Working directory. Default is current directory.
        description: Human-readable intent (used for logging and HIL display).
        run_in_background: If True, run in background and return task ID.

    Returns:
        Command output, error guidance, or background task ID.
    """
    if not command or not command.strip():
        return "Error: Empty command. Provide a valid shell command to execute."

    desc_display = description or command[:80]

    # ── Step 1: Blacklist check ─────────────────────────────────────────
    blocked = _is_command_blocked(command)
    if blocked:
        return (
            f"Error: Command blocked by safety policy.\n"
            f"Matched pattern: {blocked}\n"
            f"This command could cause irreversible damage to the system."
        )

    # ── Step 2: sed -i interception ─────────────────────────────────────
    sed_result = detect_sed_inplace(command)
    if sed_result is not None:
        file_path, old_pat, new_pat = sed_result
        return (
            f"Instead of sed -i, use the file_edit tool for safer editing:\n\n"
            f"  file_edit(\n"
            f"    file_path='{file_path}',\n"
            f"    old_string='{old_pat}',\n"
            f"    new_string='{new_pat}',\n"
            f"    replace_all=True\n"
            f"  )\n\n"
            f"file_edit provides validation (read-before-write check, "
            f"exact match verification) and keeps the edit history."
        )

    # ── Step 2b: sleep-style command interception ───────────────────────
    # Reason: bash kills commands exceeding its timeout. A killed sleep is
    # NOT the same as the requested duration having elapsed — the LLM has
    # historically mis-read the timeout error as success and inflated the
    # perceived timeline (e.g. 9 × Start-Sleep 300 → believed 45min,
    # actual ~27min). Always redirect to the dedicated wait tool.
    sleep_det = detect_sleep_command(command)
    if sleep_det is not None:
        logger.info(
            "[bash] sleep intercepted: form=%s seconds=%s | %s",
            sleep_det.form, sleep_det.seconds, command[:120],
        )
        return build_sleep_interception_message(sleep_det)

    # ── Step 3: Classify command ────────────────────────────────────────
    category = classify_command(command)
    readonly = is_read_only_command(command)
    effective_to = _effective_timeout(category, timeout)

    # ── Step 3b: Inline code → temp file rewrite (Windows quote fix) ───
    # Must happen AFTER classification (uses original command) but BEFORE
    # execution (rewrites the actual command to run).
    _inline_tmp_path: Optional[str] = None
    _inline_argv: Optional[List[str]] = None
    rewrite = rewrite_inline_to_subprocess(command, cwd)
    if rewrite is not None:
        _inline_argv, _inline_tmp_path = rewrite
        logger.info(
            "[bash] inline code rewritten to temp file (direct exec): %s",
            _inline_tmp_path,
        )

    # ── Step 3c: Shlex direct-exec for single commands (Windows quote fix) ─
    # Inline rewrite (Step 3b) handles python -c / powershell -Command.
    # For other single commands with quoted args (python "abs_path" --arg,
    # pip install "pkg>=1.0", git commit -m "msg"), bypass cmd.exe via
    # shlex.split + direct exec. Compound commands fall back to cmd /c.
    _shlex_argv: Optional[List[str]] = None
    if _inline_argv is None and not run_in_background:
        _shlex_argv = _shlex_direct_exec_argv(command)
        if _shlex_argv is not None:
            logger.info(
                "[bash] shlex direct-exec (no shell meta): %s",
                command[:120],
            )

    logger.info(
        "[bash] category=%s readonly=%s timeout=%ds bg=%s | %s%s",
        category.name, readonly, effective_to, run_in_background,
        command[:120],
        f" | {description}" if description else "",
    )

    # ── Step 4: Background execution path ───────────────────────────────
    if run_in_background:
        # Background path still uses cmd /c string (threading layer expects
        # a single command string). Inline python -c is rare in background
        # mode; if needed, callers should pre-write the script file.
        task_id = _bg_manager.start_task(
            command=command,
            cwd=cwd,
            timeout=TIMEOUT_BACKGROUND,
            description=desc_display,
        )
        return (
            f"Command started in background.\n"
            f"Task ID: {task_id}\n"
            f"Category: {category.name}\n"
            f"Use bash(command='bg_status {task_id}') to check progress."
        )

    # ── Step 4b: Background task status query ───────────────────────────
    if command.strip().startswith("bg_status "):
        task_id = command.strip().split(maxsplit=1)[1]
        status = _bg_manager.get_status(task_id)
        if status is None:
            return f"Error: No background task found with ID '{task_id}'."
        return (
            f"Task: {status['task_id']}\n"
            f"Status: {status['status']}\n"
            f"Elapsed: {status['elapsed_s']:.1f}s\n"
            f"Exit code: {status['exit_code']}\n"
            f"Output:\n{status.get('output', '(still running)')}"
        )

    # ── Step 5: Async execution with tiered timeout ──────────────────────
    try:
        if _inline_argv is not None:
            # Direct exec — bypass cmd.exe to avoid quote mangling.
            shell_args = _inline_argv
        elif _shlex_argv is not None:
            # Shlex direct-exec — bypass cmd.exe for single commands
            # with quoted args (Windows quote-escaping fix).
            shell_args = _shlex_argv
        elif sys.platform == "win32":
            shell_args = ["cmd", "/c", command]
        else:
            shell_args = ["bash", "-c", command]

        proc = await asyncio.create_subprocess_exec(
            *shell_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=effective_to,
            )
            return_code = proc.returncode or 0
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=5,
                )
            except Exception:
                stdout_bytes, stderr_bytes = b"", b""
            return_code = -9
            timed_out = True

        # Decode
        _encoding = locale.getpreferredencoding(do_setlocale=False)
        stdout = stdout_bytes.decode(_encoding, errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(_encoding, errors="replace") if stderr_bytes else ""

        # ── Step 5a: Timeout path ────────────────────────────────────
        if timed_out:
            # Auto-background on short-timeout expiry (short user-specified
            # timeout that expired; not the default 180s path)
            if (
                not readonly
                and _bg_manager.can_auto_background(command)
                and effective_to <= BLOCKING_BUDGET_S
            ):
                task_id = _bg_manager.start_task(
                    command=command,
                    cwd=cwd,
                    timeout=TIMEOUT_BACKGROUND,
                    description=desc_display,
                )
                return (
                    f"Command auto-backgrounded after {effective_to}s timeout.\n"
                    f"Task ID: {task_id}\n"
                    f"Category: {category.name}\n"
                    f"Use bash(command='bg_status {task_id}') to check progress."
                )
            return _format_timeout_error(command, category, effective_to, readonly)

        # ── Step 5b: Normal output processing ─────────────────────────
        output_parts = []
        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append(f"[stderr]\n{stderr}")

        output = "\n".join(output_parts) if output_parts else ""

        # ── Step 5c: Silent command — no output is OK ────────────────
        if not output:
            if category == CommandCategory.SILENT or return_code == 0:
                return "(command completed successfully, no output)"

        # ── Step 5d: Truncate very long output ───────────────────────
        max_output = 50_000
        if len(output) > max_output:
            output = (
                output[:max_output]
                + f"\n\n[Output truncated at {max_output} chars. "
                f"Use grep or file_read for targeted reading.]"
            )

        # ── Step 5e: Exit code handling ──────────────────────────────
        if return_code != 0:
            exit_info = _format_exit_code_info(command, return_code)
            if exit_info:
                # Known non-error exit code
                output = output + exit_info if output else exit_info
            else:
                output = f"[Exit code: {return_code}]\n{output}"

        return output

    except FileNotFoundError:
        return _format_not_found_error(command)

    except Exception as e:
        logger.exception("bash tool failed for command: %s", command)
        return f"Error executing command: {e}"

    finally:
        # ── Cleanup temp file from inline code rewrite ────────────────
        if _inline_tmp_path and os.path.exists(_inline_tmp_path):
            try:
                os.remove(_inline_tmp_path)
            except OSError:
                pass