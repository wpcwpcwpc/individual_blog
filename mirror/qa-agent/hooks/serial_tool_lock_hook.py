"""
QA Agent System — Serial Tool Lock Hook (Agno middleware)

Forces serial execution of tool calls when the upstream model emits multiple
tool_calls in one assistant turn. agno dispatches all function calls via
``asyncio.gather`` (``agno/models/base.py:2646``), so even if the model emits
2+ tool_calls they run concurrently. This hook wraps every tool execution in
a single global ``asyncio.Lock`` — concurrent tool_calls block on the lock and
execute strictly one at a time.

Why this exists:
  - Earlier attempt injected ``parallel_tool_calls=False`` into request params,
    but some upstream OpenAI-compatible providers ignore that param, so the model
    still emits multiple tool_calls per turn → agno runs them concurrently.
  - Concurrent tool calls caused the streaming delta-merge bug ("Unable to
    decode function arguments") and unwanted parallel bash execution.

Non-invasive: uses agno's public ``tool_hooks`` middleware contract. No agno
internals patched, no model subclassing, streaming preserved.

Hook order: registered LAST (after execution_log_hook + mcp_safety_hook) so
the lock only serializes the actual entrypoint execution, not the logging /
safety guards which can run concurrently (they are side-effect-free w.r.t.
the tool entrypoint).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Process-global single-writer lock. One tool execution at a time across all
# agents / sessions in this process. Intentionally global: the bug is about
# the model emitting parallel tool_calls within a single turn, and a per-agent
# lock would still serialize correctly for that case; global is simpler and
# also prevents cross-agent contention over MCP tools that touch shared state.
_SERIAL_TOOL_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    """Lazily create the global lock on first use (event-loop safe)."""
    global _SERIAL_TOOL_LOCK
    if _SERIAL_TOOL_LOCK is None:
        _SERIAL_TOOL_LOCK = asyncio.Lock()
    return _SERIAL_TOOL_LOCK


async def serial_tool_lock_hook(
    function_name: str,
    func: Callable[..., Any],
    arguments: Dict[str, Any],
    *,
    agent: Optional[Any] = None,
    run_context: Optional[Any] = None,
) -> Any:
    """Middleware hook: serialize tool execution via a global asyncio.Lock.

    Acquires the lock, runs the original tool entrypoint, then releases.
    Concurrent tool_calls (dispatched by agno's ``asyncio.gather``) block on
    the lock and execute strictly sequentially.
    """
    lock = _get_lock()
    async with lock:
        result = func(**arguments)
        if asyncio.iscoroutine(result):
            result = await result
        return result
