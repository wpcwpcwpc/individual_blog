"""
QA Agent System — Execution Log Tool Hook (Agno middleware hook)

Agno contract (single source of truth for this module)
------------------------------------------------------
Agno's ``Agent.tool_hooks`` is a middleware chain. Agno inspects the hook's
signature and injects kwargs BY PARAMETER NAME from this fixed vocabulary
(see ``agno/tools/function.py::_build_hook_args``):

    agent         → the owning Agent instance
    team          → the owning Team instance
    run_context   → the current RunContext
    name | function_name
                  → the tool's function name (both names accepted)
    function | func | function_call
                  → the next-in-chain callable (IMPORTANT: NOT ``next_func``;
                    using that name silently receives nothing and the hook
                    crashes at call time with a confusing TypeError)
    args | arguments
                  → the dict of tool arguments

Anything else in the hook signature is ignored. We therefore declare our
parameters with Agno-recognised names only:

    async def execution_log_tool_hook(
        function_name, func, arguments, *, agent=None,
    )

No closure capture is needed — Agno injects ``agent`` automatically. The
registration site in ``agents/base.py`` just appends the raw function to
``agent.tool_hooks``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Current hook (Agno middleware signature)
# ---------------------------------------------------------------------------
async def execution_log_tool_hook(
    function_name: str,
    func: Callable[..., Any],
    arguments: Dict[str, Any],
    *,
    agent: Optional[Any] = None,
) -> Any:
    """Middleware hook that records one execution log entry per tool call.

    Parameter names MUST stay as ``function_name`` / ``func`` / ``arguments``
    / ``agent`` — Agno uses the names to decide what to inject (see module
    docstring).

    Behaviour:

    - Calls ``func(**arguments)`` to execute the downstream chain. The
      callable Agno passes in is an async closure in the async-entrypoint
      path and a sync function in the sync path; we handle both via
      :func:`asyncio.iscoroutine`.
    - On success, dispatches a fire-and-forget write to
      ``qa_execution_log`` via :func:`memory.writer.write_execution_log`.
    - On exception, dispatches a fire-and-forget error-log write and then
      re-raises the original exception.
    - Never blocks the Agent loop and never propagates memory-write errors
      to the Agent.
    """
    tool_args: Dict[str, Any] = dict(arguments) if arguments else {}

    # Execute the downstream chain (may be sync or async depending on tool).
    try:
        result = func(**tool_args)
        if asyncio.iscoroutine(result):
            result = await result
    except BaseException as exc:
        err_summary = f"ERROR: {type(exc).__name__}: {exc}"
        if len(err_summary) > 1000:
            err_summary = err_summary[:1000] + "...[truncated]"
        entry = _build_log_entry(
            agent=agent,
            function_name=function_name,
            tool_args=tool_args,
            result_text=err_summary,
        )
        try:
            asyncio.create_task(_write_log_safe(entry))
        except RuntimeError:
            logger.debug(
                "[Hook] No running loop to dispatch error log (tool=%s)",
                function_name,
            )
        raise

    # Success path: build log entry and dispatch.
    result_str = str(result) if result is not None else ""
    if len(result_str) > 1000:
        result_str = result_str[:1000] + "...[truncated]"

    entry = _build_log_entry(
        agent=agent,
        function_name=function_name,
        tool_args=tool_args,
        result_text=result_str,
    )

    try:
        asyncio.create_task(_write_log_safe(entry))
    except RuntimeError:
        logger.debug(
            "[Hook] No running loop to dispatch execution log (tool=%s)",
            function_name,
        )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _build_log_entry(
    *,
    agent: Any,
    function_name: str,
    tool_args: Dict[str, Any],
    result_text: str,
) -> Dict[str, Any]:
    """Extract session context from ``agent.session_state`` and build the dict
    consumed by :func:`memory.writer.write_execution_log`."""
    session_id = ""
    worker_id = None
    game_version = ""
    module = ""
    step_no = 0

    state = getattr(agent, "session_state", None)
    if state is not None:
        session_id = getattr(state, "session_id", "") or ""
        worker_id = getattr(state, "worker_id", None)
        game_version = getattr(state, "game_version", "") or ""
        module = getattr(state, "module", "") or ""
        step_no = getattr(state, "turn_count", 0) or 0

    return {
        "session_id": session_id,
        "worker_id": worker_id or session_id,
        "step_no": step_no,
        "tool_used": function_name,
        "tool_args_summary": _summarize_args(tool_args),
        "result_summary": result_text,
        "game_version": game_version,
        "module": module,
        "timestamp": time.time(),
    }


async def _write_log_safe(log_entry: Dict[str, Any]) -> None:
    """Write execution log to Milvus, swallowing errors gracefully."""
    try:
        from memory.writer import write_execution_log
        await write_execution_log(log_entry)
        logger.debug(
            "[Hook] Execution log written (session=%s, tool=%s, step=%d)",
            log_entry.get("session_id"),
            log_entry.get("tool_used"),
            log_entry.get("step_no", 0),
        )
    except Exception:
        # Never let memory write failures propagate to the Agent loop.
        # Note: the underlying KnowledgeHub.safe_insert now logs at ERROR
        # with traceback, so this WARN is just the hook-level context.
        logger.warning(
            "[Hook] Failed to write execution log (session=%s, tool=%s) — degraded mode",
            log_entry.get("session_id"),
            log_entry.get("tool_used"),
            exc_info=True,
        )


def _summarize_args(tool_args: Dict[str, Any]) -> str:
    """Create a short summary of tool arguments for logging."""
    parts = []
    for k, v in tool_args.items():
        v_str = str(v)
        if len(v_str) > 100:
            v_str = v_str[:100] + "..."
        parts.append(f"{k}={v_str!r}")
    return ", ".join(parts[:5])  # max 5 args in summary


# ---------------------------------------------------------------------------
# Deprecated alias (will be removed one release after this change)
# ---------------------------------------------------------------------------
_DEPRECATED_WARNED: bool = False


async def execution_log_post_hook(
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_result: Any,
    agent: Any,
) -> None:
    """DEPRECATED. Kept only to surface any lingering import sites.

    The old ``(tool_name, tool_args, tool_result, agent)`` signature does NOT
    match Agno's ``tool_hooks`` middleware contract. Use
    :func:`execution_log_tool_hook` registered via closure in
    ``agents/base.py`` instead.
    """
    global _DEPRECATED_WARNED
    if not _DEPRECATED_WARNED:
        _DEPRECATED_WARNED = True
        logger.warning(
            "execution_log_post_hook is DEPRECATED — register "
            "execution_log_tool_hook via agent.tool_hooks closure in "
            "agents/base.py instead. This call is a no-op."
        )
    # No-op: we cannot dispatch a write here because the signature cannot
    # reliably produce the right middleware behaviour. Callers must migrate.
    return None
