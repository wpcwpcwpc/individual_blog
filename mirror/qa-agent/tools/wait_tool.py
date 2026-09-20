"""
QA Agent System — Wait Tool

Unified waiting primitive (single mode). Replaces ad-hoc `sleep` /
`Start-Sleep` inside bash. The tool always anchors on `duration` (the
total wait upper bound). When `check_cmd` is provided, it polls the
check command every `check_interval` seconds and returns early once
`check_exit_code` or `check_contains` matches; otherwise it sleeps the
full `duration`.

Why a dedicated tool?
  bash_tool kills long commands at its timeout (default 180s). A killed
  `Start-Sleep -Seconds 300` does NOT mean 300s elapsed — the LLM had been
  mis-reading the timeout error as success and accumulating fictitious
  elapsed time. The wait tool guarantees the agent observes ACTUAL time
  spent, never inflated.

Design highlights:
  - Hard upper bound on total wait time (WAIT_HARD_MAX_S = 3600s).
  - `check_cmd` is optional; when None the tool degrades to a blind sleep.
  - Condition mode reuses bash safety: blacklist + sed-i interception.
  - Condition mode does NOT return intermediate poll output (token cost);
    only the final check's stdout/stderr is returned.
  - Every return string includes the ACTUAL elapsed time so the LLM cannot
    inflate the timeline.
  - AI SHALL derive `duration` from context (e.g. a per-task timeout present
    in the session state); fall back to default 60 when no context exists.
"""

from __future__ import annotations

import asyncio
import locale
import logging
import os
import sys
import time

from agno.tools import tool

from .bash_classifier import detect_sed_inplace
from .bash_tool import (  # noqa: F401 (helpers reused)
    _is_command_blocked,
    _shlex_direct_exec_argv,
    rewrite_inline_to_subprocess,
)

logger = logging.getLogger(__name__)


# ── Limits ──────────────────────────────────────────────────────────────
WAIT_HARD_MAX_S = 3600          # absolute upper bound on any wait (1 hour)
WAIT_MIN_POLL_S = 1             # do not allow tight-spin polling
WAIT_MAX_OUTPUT_CHARS = 8_000   # truncate final check output


# ── Helpers ─────────────────────────────────────────────────────────────
def _clamp_total_timeout(value: int) -> int:
    if value < 1:
        return 1
    if value > WAIT_HARD_MAX_S:
        return WAIT_HARD_MAX_S
    return value


def _clamp_check_interval(value: int, total: int) -> int:
    if value < WAIT_MIN_POLL_S:
        return WAIT_MIN_POLL_S
    if value > total:
        return total
    return value


async def _run_check_command(
    command: str, cwd: str | None, check_timeout: int,
) -> tuple[int, str, str]:
    """Run a single poll check command. Returns (exit_code, stdout, stderr).

    On Windows, `python -c "..."` / `powershell -Command "..."` check commands
    are rewritten to execute a temp script file via direct subprocess argv —
    cmd.exe cannot be trusted with nested quotes (cwd trailing backslash +
    opening quote gets eaten, producing bogus argv paths).
    """
    tmp_path: str | None = None
    try:
        rewrite = rewrite_inline_to_subprocess(command, cwd)
        if rewrite is not None:
            shell_args, tmp_path = rewrite
        else:
            # Shlex direct-exec — bypass cmd.exe for single commands with
            # quoted args (e.g. `"app.exe" --run-script "script.py"
            # input.txt --grep "MARKER" --count`). cmd /c strips the
            # leading and trailing quotes when there are >2 quotes,
            # producing bogus argv like `G:\dir\"G:\path\script.py"`.
            # Mirrors bash_tool Step 3c.
            shlex_argv = _shlex_direct_exec_argv(command)
            if shlex_argv is not None:
                logger.info(
                    "[wait] shlex direct-exec (no shell meta): %s",
                    command[:120],
                )
                shell_args = shlex_argv
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
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=check_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=3)
            except Exception:  # noqa: BLE001
                out, err = b"", b""
            enc = locale.getpreferredencoding(do_setlocale=False)
            return (
                -9,
                out.decode(enc, errors="replace") if out else "",
                (err.decode(enc, errors="replace") if err else "")
                + f"\n[check command killed: exceeded per-poll {check_timeout}s]",
            )

        enc = locale.getpreferredencoding(do_setlocale=False)
        stdout = out.decode(enc, errors="replace") if out else ""
        stderr = err.decode(enc, errors="replace") if err else ""
        return proc.returncode or 0, stdout, stderr

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:  # noqa: BLE001
                pass


def _truncate(text: str, limit: int = WAIT_MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated at {limit} chars]"


# ── Main Tool ───────────────────────────────────────────────────────────

@tool(
    name="wait",
    description=(
        "Wait for `duration` seconds. If `check_cmd` is provided, run it "
        "every `check_interval` seconds and return early when `check_exit_code` "
        "or `check_contains` matches. USE THIS instead of bash sleep/Start-Sleep/timeout "
        "(those are intercepted). AI SHALL derive `duration` from context "
        "(e.g. a per-task timeout present in the session state); fall back "
        "to default 60 when no context. Total wait capped at 3600s."
    ),
)
async def wait(
    duration: int = 60,
    check_cmd: str | None = None,
    check_interval: int = 5,
    check_timeout: int = 30,
    check_exit_code: int | None = 0,
    check_contains: str | None = None,
    cwd: str | None = None,
    description: str = "",
) -> str:
    """Wait for `duration` seconds, optionally polling `check_cmd` for early exit.

    Args:
        duration: Total wait seconds (the upper bound). Clamped to [1, 3600].
            AI SHALL derive this from context (e.g. case.timeout_s); default 60.
        check_cmd: Optional shell command to poll. When None, the tool sleeps
            the full `duration` (blind wait). When provided, the tool polls
            every `check_interval` seconds and returns early on match.
        check_interval: Seconds between polls (min 1). Default 5.
        check_timeout: Per-poll check command timeout in seconds. Default 30.
        check_exit_code: Exit code that means "condition met". Default 0.
            Set to None to disable exit-code matching (use check_contains).
        check_contains: If set, condition is met when this substring appears
            in the check command's stdout. Combined with check_exit_code via
            logical OR (either match satisfies).
        cwd: Working directory for the check command.
        description: Human-readable intent (logged, surfaced in HIL).

    Returns:
        Human-readable status string. ALWAYS contains the actual elapsed
        time so the agent cannot inflate the timeline.
    """
    desc_display = description or check_cmd or f"wait {duration}s"

    # ── Clamp duration ────────────────────────────────────────────────
    actual_duration = _clamp_total_timeout(int(duration))
    clamp_note = ""
    if actual_duration != int(duration):
        clamp_note = f" (clamped from {duration}s — hard max {WAIT_HARD_MAX_S}s)"

    # ── Blind wait (check_cmd is None) ────────────────────────────────
    if check_cmd is None:
        logger.info("[wait] blind sleep=%ds | %s", actual_duration, desc_display)
        start = time.monotonic()
        try:
            await asyncio.sleep(actual_duration)
        except asyncio.CancelledError:
            elapsed = time.monotonic() - start
            logger.info("[wait] blind cancelled after %.1fs", elapsed)
            raise
        elapsed = time.monotonic() - start
        return (
            f"Wait completed (blind).\n"
            f"Requested: {actual_duration}s{clamp_note}\n"
            f"Actually waited: {elapsed:.1f}s"
        )

    # ── Condition wait (check_cmd provided) ───────────────────────────
    if not check_cmd.strip():
        return "Error: `check_cmd` is empty. Provide a shell command to poll."

    # Safety: reuse bash blacklist + sed -i interception on the check command.
    blocked = _is_command_blocked(check_cmd)
    if blocked:
        return (
            f"Error: check command blocked by safety policy.\n"
            f"Matched pattern: {blocked}\n"
            f"Choose a non-destructive check command."
        )
    sed_hit = detect_sed_inplace(check_cmd)
    if sed_hit is not None:
        return (
            "Error: `sed -i` is not allowed in wait's check command. "
            "The check should be READ-ONLY (e.g. status query, log grep, "
            "process check). Use file_edit for in-place edits."
        )

    if check_exit_code is None and check_contains is None:
        return (
            "Error: condition wait needs at least one match criterion: "
            "set `check_exit_code` (default 0) or `check_contains`."
        )

    interval = _clamp_check_interval(int(check_interval), actual_duration)

    logger.info(
        "[wait] condition check_cmd=%r exit=%s contains=%r interval=%ds total=%ds | %s",
        check_cmd[:80], check_exit_code, check_contains, interval, actual_duration, desc_display,
    )

    start = time.monotonic()
    poll_count = 0
    last_exit = None
    last_stdout = ""
    last_stderr = ""

    while True:
        poll_count += 1
        last_exit, last_stdout, last_stderr = await _run_check_command(
            check_cmd, cwd, check_timeout,
        )

        # Evaluate match
        exit_match = (
            check_exit_code is not None and last_exit == check_exit_code
        )
        contains_match = (
            check_contains is not None and check_contains in last_stdout
        )
        if exit_match or contains_match:
            elapsed = time.monotonic() - start
            reason = "exit code" if exit_match else f"stdout contains '{check_contains}'"
            return (
                f"Wait completed (condition met).\n"
                f"Matched: {reason}\n"
                f"Polls: {poll_count}\n"
                f"Actually waited: {elapsed:.1f}s{clamp_note}\n"
                f"Check command: {check_cmd}\n"
                f"Final exit code: {last_exit}\n"
                f"Final stdout:\n{_truncate(last_stdout) or '(empty)'}"
                + (f"\n[stderr]\n{_truncate(last_stderr)}" if last_stderr else "")
            )

        elapsed = time.monotonic() - start
        remaining = actual_duration - elapsed
        if remaining <= 0:
            return (
                f"Wait TIMEOUT — condition NOT met.\n"
                f"Polls attempted: {poll_count}\n"
                f"Actually waited: {elapsed:.1f}s (limit {actual_duration}s){clamp_note}\n"
                f"Check command: {check_cmd}\n"
                f"Last exit code: {last_exit}\n"
                f"Last stdout:\n{_truncate(last_stdout) or '(empty)'}"
                + (f"\n[stderr]\n{_truncate(last_stderr)}" if last_stderr else "")
                + "\n\nHint: extend `duration`, or the underlying task is stuck "
                  "— investigate before waiting again."
            )

        # Sleep until next poll, but never overshoot the total budget.
        await asyncio.sleep(min(interval, remaining))
