"""
QA Agent System — MCP Safety Tool Hook (Agno middleware hook)

Zero-token defense layer for MCP tool calls. Intercepts and corrects MCP tool
calls at the code level — no system prompt or tool description changes needed.

Agno contract
-------------
Same middleware signature as ``execution_log_hook``. Agno injects kwargs
by parameter name (see ``agno/tools/function.py::_build_hook_args``).

Registered AFTER ``execution_log_tool_hook`` in ``agents/base.py`` so that
the log hook captures the original (uncorrected) arguments first.

Guards provided:
  - per-agent tool scope (see ``_AGENT_TOOL_ALLOWLIST``)
  - bash timeout above the blocking budget → suggest run_in_background
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent tool scope enforcement (hard guard)
# ---------------------------------------------------------------------------
# Maps agent name → set of tool names that agent is ALLOWED to call.
# If an agent is listed here and calls a tool NOT in its allowlist, the call
# is blocked and a guidance message is returned instead.
# Agents NOT listed here are unrestricted (e.g. GeneralAgent, which acquires
# capabilities dynamically through skill discovery instead of a fixed list).
# Empty by default: every registered agent declares its tool set up front in
# its AgentDefinition, so the guard is a no-op until an entry is added.
_AGENT_TOOL_ALLOWLIST: Dict[str, frozenset] = {}


def _check_scope(agent_name: str, function_name: str) -> Optional[str]:
    """Return a block message if the tool call is out of scope, else None."""
    allowlist = _AGENT_TOOL_ALLOWLIST.get(agent_name)
    if allowlist is None:
        return None
    if function_name not in allowlist:
        return (
            f"⛔ 越权拦截: {agent_name} 不允许调用 {function_name}。"
            f"允许的工具: {', '.join(sorted(allowlist))}。"
            "请仅执行你职责范围内的操作。"
        )
    return None


# ---------------------------------------------------------------------------
# Blocking-time budget (bash timeout guard)
# ---------------------------------------------------------------------------
# A bash timeout above this budget occupies the agent turn for that whole
# duration, even though the async subprocess itself never blocks the event
# loop. Value sourced from core.config.settings.max_blocking_seconds for
# consistency with the bash tool's own write / background timeouts.
MAX_TOOL_TIMEOUT_S = settings.max_blocking_seconds


# ---------------------------------------------------------------------------
# Main hook (Agno middleware signature)
# ---------------------------------------------------------------------------
async def mcp_safety_tool_hook(
    function_name: str,
    func: Callable[..., Any],
    arguments: Dict[str, Any],
    *,
    agent: Optional[Any] = None,
    run_context: Optional[Any] = None,
) -> Any:
    """Middleware hook that guards MCP and builtin tool calls.

    Parameter names MUST stay as ``function_name`` / ``func`` / ``arguments``
    / ``agent`` / ``run_context`` — Agno uses the names to decide what to inject.

    ``run_context`` is part of that injectable contract and is kept even when
    the current guards do not read it.

    Pre-execution guards:
      - scope guard: per-agent tool allowlist (see _AGENT_TOOL_ALLOWLIST)
      - bash: warn when timeout exceeds MAX_TOOL_TIMEOUT_S
    """
    tool_args: Dict[str, Any] = dict(arguments) if arguments else {}
    agent_name = getattr(agent, "name", None) or ""

    # ── SCOPE GUARD: block out-of-scope tool calls for allowlisted agents ──
    scope_block = _check_scope(agent_name, function_name)
    if scope_block is not None:
        logger.warning(
            "[MCP-Safety] scope guard blocked %s → %s: %s",
            agent_name, function_name, scope_block,
        )
        return scope_block

    # ── PRE: argument guards ───────────────────────────────────────────
    # bash command with timeout > MAX_TOOL_TIMEOUT_S → warn, suggest
    # run_in_background=True.  Without this the agent waits up to the full
    # timeout for the bash result (though async subprocess won't block the
    # event loop, the agent turn itself is occupied for the entire duration).
    if function_name == "bash":
        user_timeout = tool_args.get("timeout")
        run_in_bg = tool_args.get("run_in_background", False)
        if (
            not run_in_bg
            and isinstance(user_timeout, int)
            and user_timeout > MAX_TOOL_TIMEOUT_S
        ):
            logger.info(
                "[MCP-Safety] bash timeout=%s > %ds — warning",
                user_timeout, MAX_TOOL_TIMEOUT_S,
            )
            return (
                "⚠ bash 命令超时设置过大。\n"
                f"当前 timeout={user_timeout}s，超过安全阈值 {MAX_TOOL_TIMEOUT_S}s，"
                "Agent 会等待整个超时期间无法处理其他任务。\n\n"
                "建议:\n"
                "  方案 1: 使用 run_in_background=True 后台执行\n"
                "     bash(command='...', run_in_background=True)\n"
                f"  方案 2: 拆分命令，每步控制在 {MAX_TOOL_TIMEOUT_S}s 以内\n"
                f"  方案 3: 使用 timeout=N 显式设定合理上限"
            )

    # ── EXECUTE: call downstream ───────────────────────────────────────
    try:
        result = func(**tool_args)
        if asyncio.iscoroutine(result):
            result = await result
    except BaseException:
        raise  # let execution_log_hook (upstream) handle error logging

    return result
