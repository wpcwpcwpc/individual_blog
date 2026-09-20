"""
QA Agent System — Permission Hook (L2 pre_hook)

This pre_hook is attached to L2 tools. It checks the session's permission_mode
and either auto-proceeds (bypass) or pauses execution for human confirmation.

Usage with Agno @tool::

    from hooks.permission_hook import l2_pre_hook

    @tool(name="bash", pre_hook=l2_pre_hook)
    def bash(command: str) -> str: ...
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ToolCancelledError(Exception):
    """Raised when a human cancels a pending L2 tool execution."""
    pass


# ---------------------------------------------------------------------------
# abort_pre_hook — Agent-level abort interrupt (task 7.1)
# ---------------------------------------------------------------------------

async def abort_pre_hook(agent: Any, **kwargs: Any) -> None:
    """Agent-level pre-hook that checks for abort signal at the start of each turn.

    Must be registered as the FIRST pre_hook (before reminder_pre_hook) so that
    aborted sessions are intercepted before any further processing.

    Uses Agno agent-level pre_hooks protocol: receives ``agent`` and any
    additional keyword arguments provided by the framework.

    Args:
        agent: The Agno Agent instance.
        **kwargs: Additional arguments from the Agno pre_hooks framework
                  (e.g. run_input, session, run_context).

    Raises:
        ToolCancelledError: If the session abort signal is set.
    """
    try:
        from api.session_manager import session_manager

        session_id: str = ""
        state = getattr(agent, "session_state", None)
        if state is not None:
            session_id = getattr(state, "session_id", "") or ""

        if session_id and session_manager.is_aborted(session_id):
            logger.info(
                "abort_pre_hook: session %s is aborted, raising ToolCancelledError",
                session_id,
            )
            raise ToolCancelledError(
                f"Session {session_id} was aborted by user."
            )
    except ToolCancelledError:
        raise
    except Exception:
        # Never block normal execution due to a bug in the abort check
        logger.warning("abort_pre_hook: unexpected error, skipping", exc_info=True)


async def _get_session_info(agent: Any) -> tuple[str, str]:
    """Extract session_id and permission_mode from agent state."""
    session_id = ""
    permission_mode = "default"

    if hasattr(agent, "session_state") and agent.session_state is not None:
        state = agent.session_state
        session_id = getattr(state, "session_id", "")
        permission_mode = getattr(state, "permission_mode", "default")
    elif hasattr(agent, "session_id"):
        session_id = agent.session_id or ""

    return session_id, permission_mode


async def l2_pre_hook(
    tool_name: str,
    tool_args: Dict[str, Any],
    agent: Any,
) -> Optional[Dict[str, Any]]:
    """Pre-hook for L2 tools: pause for human confirmation unless bypassed.

    Args:
        tool_name: The name of the tool being invoked.
        tool_args: The arguments passed to the tool.
        agent: The Agno Agent instance running the tool.

    Returns:
        None to proceed normally, or modified tool_args dict to override args.

    Raises:
        ToolCancelledError: If the human cancels the operation.
    """
    from hooks.interrupt_manager import InterruptType, interrupt_manager

    session_id, permission_mode = await _get_session_info(agent)

    # --- bypass mode: skip confirmation ---
    if permission_mode == "bypass":
        logger.info("[Hook] L2 tool '%s' bypassed (session=%s)", tool_name, session_id)
        return None

    if not session_id:
        logger.warning(
            "[Hook] L2 tool '%s' has no session_id — auto-proceeding (no interrupt possible)",
            tool_name,
        )
        return None

    # --- Build preview for human ---
    preview = _build_preview(tool_name, tool_args)

    payload = {
        "tool_name": tool_name,
        "tool_args": tool_args,
        "preview": preview,
    }

    # plan mode: show plan, still require confirm
    if permission_mode == "plan":
        payload["mode"] = "plan"
        payload["note"] = "系统处于 plan 模式，请确认是否执行以下操作"

    logger.info(
        "[Hook] L2 tool '%s' requesting human confirmation (session=%s, mode=%s)",
        tool_name, session_id, permission_mode,
    )

    # --- Block until human reviews ---
    record = await interrupt_manager.request_interrupt(
        session_id=session_id,
        interrupt_type=InterruptType.PREVIEW_CONFIRM,
        payload=payload,
    )

    action = record.action or "cancel"

    if action in ("cancel", "timeout"):
        reason = "cancelled by user" if action == "cancel" else "timed out waiting for review"
        logger.info("[Hook] Tool '%s' cancelled: %s (session=%s)", tool_name, reason, session_id)
        raise ToolCancelledError(f"Tool '{tool_name}' {reason}.")

    if action == "modify" and record.modified_content:
        # Return modified args — for simple tools, we replace the first string arg
        logger.info("[Hook] Tool '%s' modified by human (session=%s)", tool_name, session_id)
        modified_args = dict(tool_args)
        # Heuristic: replace the primary content field
        primary_keys = ["command", "script", "content", "code", "query"]
        for key in primary_keys:
            if key in modified_args:
                modified_args[key] = record.modified_content
                break
        return modified_args

    # action == "confirm"
    logger.info("[Hook] Tool '%s' confirmed by human (session=%s)", tool_name, session_id)
    return None  # proceed with original args


def _build_preview(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """Build a human-readable preview of what the tool will do."""
    lines = [f"工具: {tool_name}", "参数:"]
    for k, v in tool_args.items():
        value_str = str(v)
        # Truncate long values
        if len(value_str) > 500:
            value_str = value_str[:500] + "...[截断]"
        lines.append(f"  {k}: {value_str}")
    return "\n".join(lines)
