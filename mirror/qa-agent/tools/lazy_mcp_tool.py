"""
QA Agent System — LazyMCPTool

Transparent reconnection wrapper around a single MCP-exposed Function.

Problem:
    ``mcp_service.manager`` keeps each MCP server's tools as ``Function``
    objects bound to a specific ``MCPTools`` instance. When the manager
    reconnects a server (e.g. after a transport drop or game-client
    crash), the old ``MCPTools`` instance is discarded and a fresh one
    is created. Any Agent that captured the previous ``Function`` is now
    pointing at a closed session.

Solution (Design Decision D3):
    ``LazyMCPTool`` snapshots the tool's static metadata (``name``,
    ``description``, ``parameters``) at construction time, then resolves
    the current ``Function`` from
    ``mcp_manager._servers[server_name]._mcp_instance.functions[tool_name]``
    on every dispatch. If the server is not connected, it calls
    ``reconnect_server`` once before dispatching. Reconnection failures
    surface as ``MCPConnectionError`` for the caller (typically
    ``ensure_client_alive``) to handle.

This module is used by server-side code that needs to keep a tool dispatchable
across MCP server reconnects (see ``mcp_service/manager.py``).
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from agno.tools.function import Function

logger = logging.getLogger(__name__)


class MCPConnectionError(RuntimeError):
    """Raised when a LazyMCPTool cannot reach a connected MCP server."""


class LazyMCPTool:
    """Transparent late-binding wrapper around a single MCP ``Function``.

    Construction snapshots ``name / description / parameters`` so the
    Agno Agent's tool registration remains stable across MCP reconnects.
    Dispatch resolves the current ``Function`` instance just in time and
    proxies the call.

    Use :func:`lazy_mcp_tools_for` to wrap every enabled tool on a given
    server in one call.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        *,
        description: Optional[str] = None,
        parameters: Optional[dict] = None,
    ) -> None:
        self.server_name = server_name
        self.tool_name = tool_name

        # Snapshot metadata at construction time. Caller may pass overrides
        # for the rare case where the live MCP server is unavailable at
        # construction (in which case ``description`` and ``parameters``
        # must be supplied explicitly).
        if description is None or parameters is None:
            snap = self._snapshot_from_manager()
            if description is None:
                description = snap.get("description")
            if parameters is None:
                parameters = snap.get("parameters")

        self.description: Optional[str] = description
        self.parameters: dict = parameters or {
            "type": "object",
            "properties": {},
            "required": [],
        }

    # ------------------------------------------------------------------
    # Snapshot / resolution helpers
    # ------------------------------------------------------------------

    def _snapshot_from_manager(self) -> dict:
        """Capture ``description`` and ``parameters`` from the live MCP server."""
        fn = self._resolve_current_function(allow_reconnect=False)
        if fn is None:
            raise KeyError(
                f"LazyMCPTool: cannot snapshot metadata — tool "
                f"'{self.tool_name}' not found on server '{self.server_name}'"
            )
        return {
            "name": fn.name,
            "description": fn.description,
            "parameters": fn.parameters,
        }

    def _resolve_current_function(
        self,
        *,
        allow_reconnect: bool = True,
    ) -> Optional[Function]:
        """Resolve the current ``Function`` from ``mcp_manager``.

        Returns ``None`` if the server is not registered or the tool is
        not present. Raises :class:`MCPConnectionError` if reconnection
        is attempted and fails.
        """
        # Import lazily to avoid a circular import at module load time.
        from mcp_service.manager import mcp_manager

        state = mcp_manager._servers.get(self.server_name)
        if state is None:
            return None

        mcp_instance = state._mcp_instance
        if state.status != "connected" or mcp_instance is None:
            if not allow_reconnect:
                return None
            try:
                import asyncio
                # Reconnect is async; LazyMCPTool dispatch is itself async
                # so we must be called from an event loop already.
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Caller (entrypoint) handles its own awaiting.
                    raise RuntimeError("reconnect must be awaited by caller")
            except RuntimeError:
                pass
            # Real reconnect happens in ``entrypoint`` (async path).
            return None

        functions = getattr(mcp_instance, "functions", None) or {}
        return functions.get(self.tool_name)

    async def _aresolve_current_function(self) -> Function:
        """Async resolution with at-most-one reconnect attempt on failure."""
        from mcp_service.manager import mcp_manager

        state = mcp_manager._servers.get(self.server_name)
        if state is None:
            raise MCPConnectionError(
                f"LazyMCPTool: MCP server '{self.server_name}' is not registered"
            )

        if state.status != "connected" or state._mcp_instance is None:
            logger.warning(
                "LazyMCPTool: server '%s' status=%s — attempting reconnect",
                self.server_name, state.status,
            )
            try:
                from mcp_service.manager import run_mcp_call_isolated

                state = await run_mcp_call_isolated(
                    mcp_manager.reconnect_server(self.server_name)
                )
            except Exception as exc:  # noqa: BLE001
                raise MCPConnectionError(
                    f"LazyMCPTool: reconnect of '{self.server_name}' failed: {exc}"
                ) from exc
            if state.status != "connected" or state._mcp_instance is None:
                raise MCPConnectionError(
                    f"LazyMCPTool: server '{self.server_name}' still not connected "
                    f"after reconnect (status={state.status})"
                )

        functions = getattr(state._mcp_instance, "functions", None) or {}
        fn = functions.get(self.tool_name)
        if fn is None:
            raise MCPConnectionError(
                f"LazyMCPTool: tool '{self.tool_name}' not found on server "
                f"'{self.server_name}' after resolution"
            )
        return fn

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _entrypoint(self, **kwargs: Any) -> Any:
        """Async entrypoint forwarded into the agno ``Function``.

        Resolves the current MCP ``Function`` at call time so that any
        intervening reconnect transparently rebinds the underlying
        callable. ``**kwargs`` are passed through verbatim.
        """
        fn = await self._aresolve_current_function()
        target = fn.entrypoint
        if target is None:
            raise MCPConnectionError(
                f"LazyMCPTool: resolved Function '{self.tool_name}' has no entrypoint"
            )

        # MCPTools-emitted Function entrypoints are async coroutines.
        # 隔离 transport 伪取消（同 _aresolve_current_function reconnect 路径）：
        # 会话失效/连接中断时工具调用也会被库的跨 task cancel scope 打成裸
        # CancelledError / BaseExceptionGroup(GeneratorExit)，统一收容成普通异常。
        import inspect

        from mcp_service.manager import run_mcp_call_isolated

        if inspect.iscoroutinefunction(target):
            return await run_mcp_call_isolated(target(**kwargs))
        result = target(**kwargs)
        if inspect.isawaitable(result):
            return await run_mcp_call_isolated(result)
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def to_function(self) -> Function:
        """Materialize this wrapper as an agno ``Function`` ready for Agent tools."""
        # ``skip_entrypoint_processing=True`` prevents agno from inspecting
        # the entrypoint's signature against ``parameters`` — our entrypoint
        # is a generic ``**kwargs`` shim, which would otherwise be flagged.
        return Function(
            name=self.tool_name,
            description=self.description,
            parameters=self.parameters,
            entrypoint=self._entrypoint,
            skip_entrypoint_processing=True,
            # Static MCP-tool safety: never prompt for confirmation.
            requires_confirmation=False,
        )


def lazy_mcp_tool_by_name(
    server_name: str,
    tool_name: str,
    description: str,
    parameters: Optional[dict] = None,
) -> Function:
    """Create a single ``LazyMCPTool`` with explicit static metadata.

    Use this when the MCP server may not be connected at build time but
    the tool's schema is known ahead of time (e.g. MCP tools that are needed
    before their backing service has been started).

    The returned ``Function`` will late-bind to the live server at
    dispatch time — identical reconnect semantics as
    ``lazy_mcp_tools_for``.
    """
    wrapper = LazyMCPTool(
        server_name=server_name,
        tool_name=tool_name,
        description=description,
        parameters=parameters,
    )
    return wrapper.to_function()


def lazy_mcp_tools_for(server_name: str) -> List[Function]:
    """Wrap every currently-enabled tool on ``server_name`` in a ``LazyMCPTool``.

    Returns a list of agno ``Function`` instances ready to pass to an
    Agent's tools= argument. The list is computed at call time from
    ``mcp_manager``'s current ``MCPServerState.tools``. Subsequent
    reconnects do not invalidate the returned objects.

    Raises ``KeyError`` if the server is not registered with
    ``mcp_manager``.
    """
    from mcp_service.manager import mcp_manager

    state = mcp_manager._servers.get(server_name)
    if state is None:
        raise KeyError(f"LazyMCPTool: MCP server '{server_name}' is not registered")

    tools: List[Function] = []
    for raw in state.tools:
        tool_name = getattr(raw, "name", None)
        if not tool_name:
            continue
        wrapper = LazyMCPTool(
            server_name=server_name,
            tool_name=tool_name,
            description=getattr(raw, "description", None),
            parameters=getattr(raw, "parameters", None),
        )
        tools.append(wrapper.to_function())

    logger.info(
        "lazy_mcp_tools_for: wrapped %d tools from server '%s'",
        len(tools), server_name,
    )
    return tools
