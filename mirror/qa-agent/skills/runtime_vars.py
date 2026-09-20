"""
QA Agent System — Skill Runtime Variable Substitution

Provides `substitute_runtime_placeholders` used by both skill loading entry points
(skills/skill_tool.py for default-mode skills and tools/skill_discovery_tool.py for
on-demand load_skill calls) to replace ${SESSION_ID} and ${USER_ID} placeholders
in SKILL.md templates with values from the agno RunContext.

Substitution happens BEFORE expand_skill_prompt to prevent $ARGUMENTS injection.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def substitute_runtime_placeholders(text: str, run_context: Optional[Any] = None) -> str:
    """Replace runtime placeholders in a SKILL.md template string.

    Supported placeholders:
        ${SESSION_ID} → run_context.session_id
        ${USER_ID}    → run_context.user_id

    If run_context is None or fields are empty, placeholders are replaced with
    empty strings (downstream argparse will surface the missing value as an error).

    Args:
        text: The raw SKILL.md template content.
        run_context: The agno RunContext instance (or None).

    Returns:
        Template string with placeholders substituted.
    """
    if run_context is None:
        sid = ""
        uid = ""
    else:
        sid = getattr(run_context, "session_id", "") or ""
        uid = getattr(run_context, "user_id", "") or ""

    # Warn if placeholder present but value empty (helps operators debug)
    if "${SESSION_ID}" in text and not sid:
        logger.warning(
            "SKILL template contains ${SESSION_ID} but run_context.session_id is empty"
        )
    if "${USER_ID}" in text and not uid:
        logger.warning(
            "SKILL template contains ${USER_ID} but run_context.user_id is empty"
        )

    return text.replace("${SESSION_ID}", sid).replace("${USER_ID}", uid)
