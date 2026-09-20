"""
Sleep Command Detector

Detects sleep-style commands in shell input across CMD/PowerShell/POSIX:
- Unix:        sleep N, sleep 1.5
- PowerShell:  Start-Sleep -Seconds N, Start-Sleep -Milliseconds N, Start-Sleep N, sleep N
- CMD:         timeout /t N, timeout N, ping -n N 127.0.0.1 (idle trick), choice /t N
- Python:      python -c "import time; time.sleep(N)"

Why this exists:
  bash_tool kills sleep when it exceeds the WRITE timeout (default 180s).
  The LLM then mis-reads the timeout error as "sleep completed" and
  accumulates fictitious elapsed time. To prevent this class of bug we
  intercept all sleep-style commands and redirect the agent to wait_tool.

Returns parsed (command_form, seconds) when detected, None otherwise.
seconds may be None if duration could not be parsed (still intercept).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class SleepDetection:
    """A detected sleep command."""
    form: str                    # human-readable form, e.g. "Start-Sleep -Seconds"
    seconds: Optional[float]     # parsed duration in seconds (None if unparseable)
    raw_segment: str             # the matched segment text


# ── Patterns ───────────────────────────────────────────────────────────

# PowerShell: Start-Sleep [-Seconds] N | Start-Sleep -Milliseconds N | Start-Sleep -s N
_PS_START_SLEEP = re.compile(
    r"""
    \bStart-Sleep\b
    (?:\s+(?:-Seconds|-s|-Milliseconds|-m))?
    \s+(\d+(?:\.\d+)?)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PS_START_SLEEP_MS_FLAG = re.compile(
    r"\bStart-Sleep\s+(?:-Milliseconds|-m)\b", re.IGNORECASE,
)

# Unix: sleep N | sleep 1.5 | sleep 2m  (we treat trailing units conservatively)
_UNIX_SLEEP = re.compile(
    r"\bsleep\s+(\d+(?:\.\d+)?)(s|m|h|d)?\b",
    re.IGNORECASE,
)

# CMD: timeout /t N | timeout /T N | timeout N
_CMD_TIMEOUT = re.compile(
    r"\btimeout(?:\.exe)?\s+(?:/t\s+|/T\s+)?(\d+)\b",
    re.IGNORECASE,
)

# CMD idle trick: ping -n N 127.0.0.1   (sleeps N-1 seconds)
_CMD_PING_IDLE = re.compile(
    r"\bping(?:\.exe)?\s+-n\s+(\d+)\s+127\.0\.0\.1\b",
    re.IGNORECASE,
)

# CMD: choice /t N /d Y >nul
_CMD_CHOICE = re.compile(
    r"\bchoice(?:\.exe)?\s+.*?/t\s+(\d+)",
    re.IGNORECASE,
)

# Python inline sleep: python -c "... time.sleep(N) ..."
_PY_INLINE_SLEEP = re.compile(
    r"""
    \bpython\b[^\n]*?-c\b[^\n]*?
    (?:time\.)?sleep\s*\(\s*(\d+(?:\.\d+)?)\s*\)
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


# Convert unit suffix to multiplier (POSIX sleep semantics)
_UNIT_MULT = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, None: 1.0, "": 1.0}


def detect_sleep_command(command: str) -> Optional[SleepDetection]:
    """Detect any sleep-style command in the input.

    Args:
        command: full shell command string (may include pipes / && / ;).

    Returns:
        SleepDetection if a sleep-style command is found, else None.

    Notes:
        - Returns the FIRST detection found in scan order. Compound commands
          like ``sleep 10 && echo done`` are intercepted on the sleep half.
        - Duration parsing is best-effort. If we detect a sleep but cannot
          parse a number, ``seconds`` is None — still intercept.
    """
    # PowerShell Start-Sleep
    m = _PS_START_SLEEP.search(command)
    if m:
        n = float(m.group(1))
        is_ms = bool(_PS_START_SLEEP_MS_FLAG.search(m.group(0)))
        seconds = n / 1000.0 if is_ms else n
        return SleepDetection(
            form="Start-Sleep" + (" -Milliseconds" if is_ms else " -Seconds"),
            seconds=seconds,
            raw_segment=m.group(0),
        )

    # CMD timeout
    m = _CMD_TIMEOUT.search(command)
    if m:
        return SleepDetection(
            form="timeout /t",
            seconds=float(m.group(1)),
            raw_segment=m.group(0),
        )

    # CMD ping idle trick (sleeps N-1 seconds, but we report N for safety)
    m = _CMD_PING_IDLE.search(command)
    if m:
        return SleepDetection(
            form="ping -n (idle trick)",
            seconds=float(m.group(1)),
            raw_segment=m.group(0),
        )

    # CMD choice /t
    m = _CMD_CHOICE.search(command)
    if m:
        return SleepDetection(
            form="choice /t",
            seconds=float(m.group(1)),
            raw_segment=m.group(0),
        )

    # Python inline sleep
    m = _PY_INLINE_SLEEP.search(command)
    if m:
        return SleepDetection(
            form="python -c time.sleep()",
            seconds=float(m.group(1)),
            raw_segment=m.group(0),
        )

    # Unix sleep — check LAST to avoid matching the "sleep" inside Start-Sleep
    # (we already matched Start-Sleep above, so if we reach here it's safe).
    m = _UNIX_SLEEP.search(command)
    if m:
        n = float(m.group(1))
        unit = (m.group(2) or "s").lower()
        seconds = n * _UNIT_MULT.get(unit, 1.0)
        return SleepDetection(
            form=f"sleep {unit if unit != 's' else ''}".strip(),
            seconds=seconds,
            raw_segment=m.group(0),
        )

    return None


def build_sleep_interception_message(det: SleepDetection) -> str:
    """Build the guidance message returned to the LLM when sleep is intercepted."""
    secs = f"{det.seconds:g}s" if det.seconds is not None else "unknown duration"
    lines = [
        f"Error: Sleep-style command intercepted ({det.form}, {secs}).",
        "",
        "Why: bash kills commands exceeding its timeout. A killed sleep does NOT",
        "mean the requested duration elapsed — the LLM has historically mis-read",
        "the timeout error as success and accumulated fictitious elapsed time.",
        "",
        "Use the `wait` tool instead. It waits for `duration` seconds, optionally",
        "polling `check_cmd` for early exit.",
        "",
        "  1) Blind wait (fixed duration):",
        "     wait(duration=10, description='wait for server warmup')",
        "",
        "  2) Condition wait (poll until a check command succeeds):",
        "     wait(",
        "       duration=1800,",
        "       check_cmd='python check_dungeon_status.py 510001',",
        "       check_exit_code=0,",
        "       check_interval=10,",
        "       description='wait for dungeon 510001 to finish'",
        "     )",
    ]
    return "\n".join(lines)
