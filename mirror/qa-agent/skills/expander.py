"""
QA Agent System — Skill Expander

Expands a SKILL.md prompt template by:
1. Executing embedded shell commands  (!`cmd` syntax)
2. Replacing variables ($ARGUMENTS, $named_arg, ${CLAUDE_SKILL_DIR})
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Regex for embedded shell commands:  !`command`
_SHELL_CMD_RE = re.compile(r"!`([^`]+)`")

# Regex for ${VAR_NAME} style variables
_BRACE_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# Regex for $plain_name positional/named args (lowercase, snake_case)
_PLAIN_VAR_RE = re.compile(r"\$([a-z_][a-z0-9_]*)")

# Max time for inline shell command execution
SHELL_CMD_TIMEOUT = 5  # seconds


def expand_skill_prompt(
    template: str,
    base_dir: Path,
    args: List[str],
    arguments: str = "",
    named_values: Optional[Dict[str, str]] = None,
) -> str:
    """Expand a SKILL.md prompt template into its final form.

    Expansion order:
    1. Execute embedded shell commands  (!`cmd`)
    2. Replace ${CLAUDE_SKILL_DIR}
    3. Replace $ARGUMENTS  (the full argument string)
    4. Replace named args  ($scenario, $platform, etc.)

    Args:
        template: The raw Markdown body from SKILL.md.
        base_dir: Absolute path to the Skill directory (for shell command CWD).
        args: List of arg names from Frontmatter (e.g. ["scenario", "platform"]).
        arguments: The full argument string provided by the caller.
        named_values: Optional dict of pre-parsed named values. If None,
                      the system splits `arguments` by space to fill `args`.

    Returns:
        The expanded prompt string ready for injection into the Agent.
    """
    result = template

    # Step 1: Execute embedded shell commands
    result = _expand_shell_commands(result, base_dir)

    # Step 2: ${CLAUDE_SKILL_DIR}
    skill_dir_str = str(base_dir).replace("\\", "/")
    result = _BRACE_VAR_RE.sub(
        lambda m: skill_dir_str if m.group(1) == "CLAUDE_SKILL_DIR"
        else m.group(0),
        result,
    )

    # Step 3: $ARGUMENTS
    result = result.replace("$ARGUMENTS", arguments)

    # Step 4: named args  ($scenario, $platform, ...)
    if named_values is None:
        named_values = _parse_named_values(args, arguments)

    def _replace_named(m: re.Match) -> str:
        var_name = m.group(1)
        return named_values.get(var_name, m.group(0))  # keep original if not found

    result = _PLAIN_VAR_RE.sub(_replace_named, result)

    return result


# ---------------------------------------------------------------------------
# Shell command execution
# ---------------------------------------------------------------------------

def _expand_shell_commands(template: str, base_dir: Path) -> str:
    """Find and execute all !`cmd` patterns in the template."""

    def _execute(m: re.Match) -> str:
        cmd = m.group(1).strip()
        return _run_shell_command(cmd, base_dir)

    return _SHELL_CMD_RE.sub(_execute, template)


def _run_shell_command(command: str, cwd: Path) -> str:
    """Execute a single embedded shell command with timeout.

    Returns stdout on success, or an error placeholder on failure.
    """
    try:
        if sys.platform == "win32":
            shell_args = ["cmd", "/c", command]
        else:
            shell_args = ["bash", "-c", command]

        result = subprocess.run(
            shell_args,
            capture_output=True,
            text=True,
            timeout=SHELL_CMD_TIMEOUT,
            cwd=str(cwd),
        )

        if result.returncode != 0:
            error_msg = (result.stderr or result.stdout or "unknown error").strip()
            logger.warning("Embedded command failed (exit %d): %s", result.returncode, command)
            return f"[命令执行失败 (exit {result.returncode}): {error_msg}]"

        return result.stdout.rstrip()

    except subprocess.TimeoutExpired:
        logger.warning("Embedded command timed out (%ds): %s", SHELL_CMD_TIMEOUT, command)
        return f"[命令执行超时 ({SHELL_CMD_TIMEOUT}s): {command}]"

    except Exception as e:
        logger.exception("Embedded command error: %s", command)
        return f"[命令执行错误: {e}]"


# ---------------------------------------------------------------------------
# Named argument parsing
# ---------------------------------------------------------------------------

def _parse_named_values(arg_names: List[str], arguments: str) -> Dict[str, str]:
    """Map positional words in `arguments` to arg_names.

    Example:
        arg_names = ["scenario", "platform"]
        arguments = "登录流程 iOS"
        → {"scenario": "登录流程", "platform": "iOS"}

    If there are more words than arg_names, the last arg_name gets the rest.
    """
    if not arg_names or not arguments.strip():
        return {}

    parts = arguments.split()
    result: Dict[str, str] = {}

    for i, name in enumerate(arg_names):
        if i < len(parts):
            if i == len(arg_names) - 1:
                # Last arg gets all remaining words
                result[name] = " ".join(parts[i:])
            else:
                result[name] = parts[i]

    return result
