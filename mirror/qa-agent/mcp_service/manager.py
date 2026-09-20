"""
QA Agent System — MCP Service Manager

Global singleton that manages all MCP server connections, lifecycle,
and tool registration. Provides the runtime layer for:
- Persistent MCP connections (stdio / SSE)
- Dynamic enable / disable / reconnect
- mcp.json configuration read/write
- Tool injection into agents via get_enabled_tools()
- Background health monitoring: periodic probe + auto-reconnect
  so "connected" status always reflects real usability.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Health-monitoring constants
# ---------------------------------------------------------------------------

# How often the background health loop probes each connected HTTP/SSE server.
_HEALTH_CHECK_INTERVAL = 30.0   # seconds between probes

# How long a single ping is allowed to take before it's considered a failure.
_HEALTH_PING_TIMEOUT = 5.0      # seconds

# How many consecutive probe failures before an auto-reconnect is attempted.
_HEALTH_FAIL_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Cancellation isolation — MCP transport 噪声收容
# ---------------------------------------------------------------------------

#: mcp/agno 的 streamable-http transport 用跨 task 的 anyio cancel scope 管理
#: 连接生命周期。连接失败/会话失效时，库会把调用方 task 直接 cancel()，抛出裸
#: asyncio.CancelledError（或 BaseExceptionGroup(GeneratorExit)）——这不是真实
#: 的任务取消，却穿透所有 ``except Exception`` 边界，最终在 ASGI 层表现为
#: 500 "No response returned"（qa-agent/scripts/spike_probe_dead_port.py 已复现）。
#: 只有 KeyboardInterrupt / SystemExit 是无条件 fatal。
_MCP_NOISE_FLAVORS: tuple = (asyncio.CancelledError, GeneratorExit)


def _is_mcp_cancellation_noise(exc: BaseException) -> bool:
    """判定异常是否为 MCP transport 内部 cancel-scope 的伪取消（非真任务取消）。"""
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return False
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return all(
            isinstance(sub, _MCP_NOISE_FLAVORS) or _is_mcp_cancellation_noise(sub)
            for sub in exc.exceptions
        )
    return False


async def run_mcp_call_isolated(coro: Any) -> Any:
    """在独立子 task 中执行 MCP 库协程，隔离 transport 的跨 task 伪取消。

    mcp/agno 的 cancel scope 会 cancel 调用方 task（伪取消）；放进子 task 后
    scope 打在子 task 上，本 task 的 ``cancelling()`` 保持 0，可可靠区分：

    - 本 task 未被取消、子 task 死于取消类异常 → transport 噪声 → 转成
      ``MCPConnectionError``（普通 Exception，调用方按连接失败处理）。
    - 本 task 被真实取消（uvicorn shutdown / 客户端断开）→ 取消子 task 并
      放行 CancelledError。

    Args:
        coro: MCP 库协程（connect / 工具调用），由本函数负责调度执行。

    Returns:
        协程返回值。

    Raises:
        MCPConnectionError: transport 伪取消（连接失败/会话失效的伪装形态）。
        asyncio.CancelledError: 本 task 被真实取消。
    """
    from tools.lazy_mcp_tool import MCPConnectionError

    child = asyncio.create_task(coro)
    try:
        return await child
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling() > 0:
            child.cancel()
            raise
        raise MCPConnectionError(
            "MCP call was cancelled by the transport's internal cancel scope "
            "(connection failure or stale session), not a real task cancellation"
        ) from None
    except BaseExceptionGroup as exc:
        if _is_mcp_cancellation_noise(exc) and asyncio.current_task().cancelling() == 0:
            raise MCPConnectionError(
                f"MCP transport raised cancellation noise: {exc}"
            ) from None
        raise


# ---------------------------------------------------------------------------
# MCPServerState — per-server runtime state
# ---------------------------------------------------------------------------

@dataclass
class MCPServerState:
    """Runtime state for a single MCP server."""

    name: str
    config: dict                                    # transport, command, args, env, url
    enabled: bool = False
    status: str = "disconnected"                    # connected | disconnected | error
    tools: List[Any] = field(default_factory=list)
    tool_names: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    connected_at: Optional[float] = None
    _mcp_instance: Optional[Any] = None             # MCPTools instance (persistent)
    _health_failures: int = 0                       # consecutive probe failures


# ---------------------------------------------------------------------------
# MCPManager — global lifecycle manager
# ---------------------------------------------------------------------------

class MCPManager:
    """Manages all MCP server connections and tool registration.

    Usage::

        manager = MCPManager()
        await manager.startup(settings.mcp_config_path)  # "" → MCP disabled
        tools = manager.get_enabled_tools()
        await manager.shutdown()
    """

    def __init__(self) -> None:
        self._servers: Dict[str, MCPServerState] = {}
        self._config_path: str = ""
        self._lock = asyncio.Lock()
        self._health_task: Optional[asyncio.Task] = None
        self._shutdown_event: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------------

    def _load_config(self) -> Dict[str, Any]:
        """Read mcp.json and return the servers dict.

        Supports two formats:
        - Our native format: ``{"servers": {...}}``
        - Claude Code format: ``{"mcpServers": {...}}`` with ``type`` and ``disabled`` fields

        Claude Code configs are normalized on read:
        - ``mcpServers`` → ``servers``
        - ``type: "streamableHttp"`` → ``transport: "streamableHttp"``
        - ``disabled: true`` → ``enabled: false``
        """
        if not (self._config_path or "").strip():
            return {}
        path = Path(self._config_path)
        if not path.exists():
            logger.debug("MCP config not found at %s", self._config_path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            # Support Claude Code format (mcpServers key)
            servers = data.get("servers") or data.get("mcpServers") or {}

            # Normalize each server config
            normalized: Dict[str, Any] = {}
            for name, cfg in servers.items():
                cfg = dict(cfg)  # shallow copy

                # Claude Code uses "type" instead of "transport"
                if "type" in cfg and "transport" not in cfg:
                    cfg["transport"] = cfg.pop("type")

                # Claude Code uses "disabled" (inverted) instead of "enabled"
                if "disabled" in cfg and "enabled" not in cfg:
                    cfg["enabled"] = not cfg.pop("disabled")

                normalized[name] = cfg

            return normalized
        except Exception as e:
            logger.error("Failed to read MCP config: %s", e)
            return {}

    async def _save_config(self) -> None:
        """Write current server configs back to mcp.json (lock must be held).

        With no config path configured the servers stay in-memory only — a
        restart forgets them. Logged (not raised) so runtime add/remove still
        works in a deployment that ships no MCP config file.
        """
        if not (self._config_path or "").strip():
            logger.warning(
                "MCP_CONFIG_PATH unset — %d server(s) kept in memory only (not persisted)",
                len(self._servers),
            )
            return
        path = Path(self._config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        servers_data: Dict[str, Any] = {}
        for name, state in self._servers.items():
            cfg = dict(state.config)
            cfg["enabled"] = state.enabled
            servers_data[name] = cfg

        data = {"servers": servers_data}
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug("Saved MCP config to %s", self._config_path)
        except Exception as e:
            logger.error("Failed to write MCP config: %s", e)

    # ------------------------------------------------------------------
    # Connection internals
    # ------------------------------------------------------------------

    async def _connect_server(self, name: str, config: dict) -> MCPServerState:
        """Create a persistent MCP connection and register tools.

        Uses MCPTools.connect() for persistent connections (not async-with).
        After connect(), tools are available via mcp_instance.functions.

        For HTTP/SSE transports a lightweight ping is sent after connect()
        so that "status=connected" guarantees the server is actually reachable.

        Args:
            name: Server name.
            config: Server config dict (transport, command/url, args, env).

        Returns:
            Updated MCPServerState.
        """
        state = self._servers.get(name) or MCPServerState(name=name, config=config)
        state.config = config
        state.enabled = True
        state._health_failures = 0

        transport = config.get("transport", "stdio")

        try:
            mcp_instance = self._create_mcp_instance(config, transport)
            logger.warning(
                "MCP DEBUG pre-connect: name=%s transport=%s header_provider=%s hp_returns=%s",
                name,
                getattr(mcp_instance, "transport", None),
                "set" if getattr(mcp_instance, "header_provider", None) else "None",
                mcp_instance.header_provider() if getattr(mcp_instance, "header_provider", None) else None,
            )

            # Use connect() for persistent connection (not __aenter__)
            # connect() internally calls _connect() → initialize() → build_tools()
            # 隔离 transport 伪取消：连接失败时 mcp 库会裸抛 CancelledError，
            # 未隔离会穿透下方 except Exception 并炸掉调用方请求（500）。
            await run_mcp_call_isolated(mcp_instance.connect())

            # Check that initialization actually succeeded
            # connect() swallows errors internally, so check _initialized flag
            if not mcp_instance._initialized:
                raise RuntimeError(
                    "MCPTools.connect() completed but _initialized=False "
                    "(server may be unreachable or returned an error)"
                )

            # For HTTP/SSE transports: do a real ping to confirm reachability.
            # This turns "connected" from "initialized at one point in time" into
            # "confirmed reachable right now".  stdio servers skip this because
            # their subprocess stdout/stdin is already validated by connect().
            if transport != "stdio" and mcp_instance.session is not None:
                try:
                    await asyncio.wait_for(
                        mcp_instance.session.send_ping(),
                        timeout=_HEALTH_PING_TIMEOUT,
                    )
                    logger.debug("MCP server '%s' ping OK", name)
                except Exception as ping_err:
                    raise RuntimeError(
                        f"MCP server connected but ping failed: {ping_err}"
                    ) from ping_err

            # Extract tools from MCPTools.functions (dict of Function objects)
            raw_tools = list(mcp_instance.functions.values()) if mcp_instance.functions else []

            # Tag and register tools in tool_registry
            from mcp_service.loader import _tag_mcp_tools
            _tag_mcp_tools(raw_tools, name)

            state._mcp_instance = mcp_instance
            state.tools = raw_tools
            state.tool_names = [
                getattr(t, "name", "") for t in raw_tools
            ]
            state.status = "connected"
            state.error_message = None
            state.connected_at = time.time()

            logger.info(
                "MCP server '%s' connected: %d tools loaded (%s)",
                name, len(raw_tools), transport,
            )

        except Exception as e:
            state.status = "error"
            state.error_message = str(e)
            state.tools = []
            state.tool_names = []
            state._mcp_instance = None
            state.connected_at = None
            # DEBUG: per-attempt failure is noisy during background health-loop
            # auto-reconnect cycles. The terminal failure is still surfaced by
            # _auto_reconnect() / _connect_server_bg() at WARNING level.
            logger.debug(
                "MCP server '%s' connection failed: %s", name, e
            )

        self._servers[name] = state
        return state

    async def _disconnect_server(self, name: str) -> None:
        """Close an MCP connection and unregister its tools."""
        state = self._servers.get(name)
        if not state:
            return

        # Unregister tools from tool_registry
        from tools.registry import tool_registry
        category = f"mcp_{name}"
        removed = tool_registry.unregister_by_category(category)
        if removed:
            logger.info("Unregistered %d tools for MCP server '%s'", len(removed), name)

        # Close the MCP connection using close() (safe cleanup)
        if state._mcp_instance is not None:
            try:
                await state._mcp_instance.close()
            except Exception as e:
                logger.warning("Error closing MCP server '%s': %s", name, e)
            state._mcp_instance = None

        state.tools = []
        state.tool_names = []
        state.status = "disconnected"
        state.connected_at = None
        state.error_message = None
        state._health_failures = 0

    @staticmethod
    def _create_mcp_instance(config: dict, transport: str) -> Any:
        """Create an MCPTools instance (not yet connected).

        Args:
            config: Server config dict.
            transport: Transport type (stdio, sse, streamableHttp, streamable-http).

        Returns:
            MCPTools instance ready for connect().
        """
        from agno.tools.mcp import MCPTools

        # Default to 120s timeout — MCP tools can take longer than the 10s
        # Agno default, especially for heavy remote operations.
        timeout = config.get("timeout_seconds", 120)

        if transport == "stdio":
            command = config.get("command", "python")
            env = config.get("env", None)
            args = config.get("args", [])
            # MCPTools accepts command string directly
            full_command = f"{command} {' '.join(args)}" if args else command
            return MCPTools(command=full_command, env=env, timeout_seconds=timeout)

        elif transport in ("sse", "streamableHttp", "streamable-http"):
            url = config.get("url", "")
            if not url:
                raise ValueError(f"Missing 'url' for {transport} transport")
            # MCPTools auto-detects transport from url parameter
            # For SSE, explicitly set transport
            if transport == "sse":
                return MCPTools(url=url, transport="sse", timeout_seconds=timeout)
            else:
                # Use a header_provider so agno uses per-run sessions instead of
                # a shared persistent session.
                #
                # Root cause of "Session is not initialized":
                #   agno's get_session_for_run() without header_provider falls back to
                #   self.session (the persistent session built during connect()). That
                #   session is tied to the anyio cancel-scope of the background connect
                #   task; once the task finishes the underlying httpx stream can become
                #   invalid, making self.session None or stale.
                #
                # Fix: header_provider != None forces the per-run session code path —
                #   each agent run gets a fresh HTTP session to the MCP server URL,
                #   completely bypassing the shared self.session.  This makes every
                #   tool call resilient to MCP server restarts, game reloads, and
                #   anyio scope lifetime issues.
                #
                # When config includes "headers" (e.g. Bearer auth for codemap),
                # those headers are passed through.  Without "headers" the dict is
                # empty — backward compatible with existing bridge configs.
                _headers = config.get("headers", {})
                logger.warning(
                    "MCP DEBUG create_instance: url=%s transport=%s headers_keys=%s",
                    url, transport, list(_headers.keys()) if _headers else None,
                )
                return MCPTools(
                    url=url,
                    timeout_seconds=timeout,
                    header_provider=lambda: dict(_headers),
                )

        else:
            raise ValueError(f"Unknown transport: {transport}")

    # ------------------------------------------------------------------
    # Lifecycle — startup / shutdown
    # ------------------------------------------------------------------

    async def startup(self, config_path: Optional[str] = None) -> None:
        """Load mcp.json and connect all enabled servers as background tasks.

        All connections are fire-and-forget asyncio tasks — this method returns
        immediately after scheduling them, so callers (lifespan) are not blocked.
        Tools from each server become available once that server's task completes.
        Also starts the background health-monitoring loop.
        Called during app lifespan startup (itself already a background task).
        """
        if config_path:
            self._config_path = config_path

        servers_config = self._load_config()
        if not servers_config:
            logger.info("MCP: no servers configured")
            return

        total = len(servers_config)

        # First pass: register all servers in state dict
        for name, config in servers_config.items():
            enabled = config.pop("enabled", True)  # default True for backward compat
            state = MCPServerState(
                name=name,
                config=config,
                enabled=enabled,
            )
            self._servers[name] = state

        # Second pass: connect enabled servers — fully non-blocking background tasks.
        # Each task is independent; failure of one doesn't affect others.
        enabled_servers = [
            (name, s.config)
            for name, s in self._servers.items()
            if s.enabled
        ]

        if not enabled_servers:
            logger.info("MCP: no servers enabled (%d configured)", total)
            return

        logger.info("MCP: scheduling %d/%d enabled servers for background connection...",
                    len(enabled_servers), total)

        for name, config in enabled_servers:
            asyncio.create_task(
                self._connect_server_bg(name, config),
                name=f"mcp-connect-{name}",
            )

        # Start background health monitoring
        self._shutdown_event = asyncio.Event()
        self._health_task = asyncio.create_task(
            self._health_loop(),
            name="mcp-health-monitor",
        )

    async def _connect_server_bg(self, name: str, config: dict) -> None:
        """Background task wrapper for _connect_server.

        Isolates anyio cancel scope issues from the main lifespan.
        """
        try:
            await self._connect_server(name, config)
        except Exception as e:
            logger.warning("MCP server '%s' background connect failed: %s", name, e)
            if name in self._servers:
                self._servers[name].status = "error"
                self._servers[name].error_message = str(e)

    async def shutdown(self) -> None:
        """Disconnect all connected servers. Called during app shutdown."""
        # Stop health monitoring first
        if self._shutdown_event:
            self._shutdown_event.set()
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass

        tasks = [
            asyncio.create_task(
                self._disconnect_server(name),
                name=f"mcp-disconnect-{name}",
            )
            for name, state in self._servers.items()
            if state.status == "connected"
        ]
        if tasks:
            await asyncio.wait(tasks, timeout=10.0)

        logger.info("MCP: all servers disconnected")

    # ------------------------------------------------------------------
    # Background health monitoring
    # ------------------------------------------------------------------

    async def _health_loop(self) -> None:
        """Background task: periodically probe connected HTTP/SSE servers.

        Logic per server:
          - Skip stdio (subprocess; connect() already validates the pipe).
          - For HTTP/SSE: attempt session.send_ping() with a short timeout.
          - On success: reset failure counter, ensure status=connected.
          - On failure: increment failure counter.
          - When failures >= _HEALTH_FAIL_THRESHOLD: auto-reconnect.
            If reconnect fails: mark status=error so the UI reflects reality.

        This ensures "status=connected" means the server is actually reachable,
        not just that it was reachable at startup time.
        """
        assert self._shutdown_event is not None
        logger.info("MCP health monitor started (interval=%ds)", int(_HEALTH_CHECK_INTERVAL))

        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=_HEALTH_CHECK_INTERVAL,
                )
                # shutdown_event fired — exit loop
                break
            except asyncio.TimeoutError:
                pass  # normal: interval elapsed, do a probe round

            await self._probe_all_servers()

        logger.info("MCP health monitor stopped")

    async def _probe_all_servers(self) -> None:
        """Probe all enabled HTTP/SSE servers once and update their status."""
        for name, state in list(self._servers.items()):
            if not state.enabled:
                continue

            transport = state.config.get("transport", "stdio")
            if transport == "stdio":
                continue  # stdio health is implicit (subprocess presence)

            if state.status not in ("connected", "error"):
                continue  # disconnected = intentionally offline, skip

            alive = await self._probe_server(name, state)

            if alive:
                if state.status != "connected":
                    # Recovered from a previous error — mark connected
                    logger.info("MCP server '%s' recovered (probe OK)", name)
                    state.status = "connected"
                    state.error_message = None
                state._health_failures = 0
            else:
                state._health_failures += 1
                # DEBUG: per-probe failure progress is noisy (repeats every
                # interval). Terminal threshold breach is logged at WARNING below.
                logger.debug(
                    "MCP server '%s' probe failed (%d/%d)",
                    name, state._health_failures, _HEALTH_FAIL_THRESHOLD,
                )

                if state._health_failures >= _HEALTH_FAIL_THRESHOLD:
                    logger.warning(
                        "MCP server '%s' reached failure threshold — auto-reconnecting",
                        name,
                    )
                    state._health_failures = 0
                    # Fire-and-forget reconnect so we don't block the probe loop
                    asyncio.create_task(
                        self._auto_reconnect(name),
                        name=f"mcp-auto-reconnect-{name}",
                    )

    async def _probe_server(self, name: str, state: MCPServerState) -> bool:
        """Send a ping to an HTTP/SSE MCP server.  Returns True if alive.

        Uses the per-run session approach (matching how tools are called) so
        the probe exercises exactly the same connection path that tool calls use.
        For servers using per-run sessions (header_provider set), we attempt a
        fresh HTTP connection; for others we use the persistent session.
        """
        try:
            mcp_instance = state._mcp_instance
            if mcp_instance is None:
                return False

            session = getattr(mcp_instance, "session", None)
            if session is None:
                return False

            await asyncio.wait_for(session.send_ping(), timeout=_HEALTH_PING_TIMEOUT)
            return True
        except Exception as e:
            logger.debug("MCP server '%s' ping error: %s", name, e)
            return False

    async def _auto_reconnect(self, name: str) -> None:
        """Attempt to reconnect a server that failed health checks.

        Skips if already connected (another task beat us to it).
        Acquires the manager lock so it doesn't race with manual reconnect.
        """
        async with self._lock:
            state = self._servers.get(name)
            if not state or not state.enabled:
                return

            # DEBUG: disconnect/reconnect lifecycle chatter is noisy under
            # repeated failures. Only the terminal success/failure is logged
            # at INFO/WARNING below.
            logger.debug("MCP server '%s' auto-reconnect: disconnecting...", name)
            await self._disconnect_server(name)
            logger.debug("MCP server '%s' auto-reconnect: reconnecting...", name)
            state = await self._connect_server(name, state.config)
            state.enabled = True

            if state.status == "connected":
                logger.info("MCP server '%s' auto-reconnect: success", name)
            else:
                logger.warning(
                    "MCP server '%s' auto-reconnect: failed — %s",
                    name, state.error_message,
                )

    # ------------------------------------------------------------------
    # Public API — enable / disable / reconnect
    # ------------------------------------------------------------------

    async def enable_server(self, name: str) -> MCPServerState:
        """Enable and connect an MCP server.

        Idempotent: if already connected, returns current state.
        """
        async with self._lock:
            state = self._servers.get(name)
            if not state:
                raise KeyError(f"MCP server '{name}' not found")

            if state.status == "connected":
                return state

            state = await self._connect_server(name, state.config)
            state.enabled = True
            await self._save_config()
            return state

    async def disable_server(self, name: str) -> MCPServerState:
        """Disable and disconnect an MCP server."""
        async with self._lock:
            state = self._servers.get(name)
            if not state:
                raise KeyError(f"MCP server '{name}' not found")

            await self._disconnect_server(name)
            state = self._servers[name]
            state.enabled = False
            await self._save_config()
            return state

    async def reconnect_server(self, name: str) -> MCPServerState:
        """Reconnect an MCP server (disconnect + connect)."""
        async with self._lock:
            state = self._servers.get(name)
            if not state:
                raise KeyError(f"MCP server '{name}' not found")

            await self._disconnect_server(name)
            state = await self._connect_server(name, state.config)
            state.enabled = True
            await self._save_config()
            return state

    # ------------------------------------------------------------------
    # Public API — CRUD
    # ------------------------------------------------------------------

    async def add_server(
        self,
        name: str,
        config: dict,
        enabled: bool = True,
    ) -> MCPServerState:
        """Add a new MCP server configuration.

        Args:
            name: Server name (must be unique).
            config: Transport config dict.
            enabled: Whether to auto-connect after adding.

        Raises:
            ValueError: If server name already exists.
        """
        async with self._lock:
            if name in self._servers:
                raise ValueError(f"MCP server '{name}' already exists")

            state = MCPServerState(name=name, config=config, enabled=False)
            self._servers[name] = state

            if enabled:
                state = await self._connect_server(name, config)
                state.enabled = True
            else:
                state.enabled = False

            await self._save_config()
            return state

    async def update_server(self, name: str, config_updates: dict) -> MCPServerState:
        """Update an existing MCP server's configuration.

        Only updates provided fields; does NOT reconnect automatically.
        """
        async with self._lock:
            state = self._servers.get(name)
            if not state:
                raise KeyError(f"MCP server '{name}' not found")

            state.config.update(config_updates)
            await self._save_config()
            return state

    async def remove_server(self, name: str) -> None:
        """Remove an MCP server (disconnect if needed + remove from config)."""
        async with self._lock:
            state = self._servers.get(name)
            if not state:
                raise KeyError(f"MCP server '{name}' not found")

            if state.status == "connected":
                await self._disconnect_server(name)

            del self._servers[name]
            await self._save_config()

    # ------------------------------------------------------------------
    # Public API — tool access
    # ------------------------------------------------------------------

    def get_enabled_tools(self) -> List[Any]:
        """Return all tools from enabled & connected MCP servers."""
        result: List[Any] = []
        for state in self._servers.values():
            if state.enabled and state.status == "connected":
                result.extend(state.tools)
        return result

    # ------------------------------------------------------------------
    # Public API — status queries
    # ------------------------------------------------------------------

    def list_servers(self) -> List[dict]:
        """Return status snapshots for all configured servers."""
        return [self._server_info(s) for s in self._servers.values()]

    def get_server(self, name: str) -> Optional[dict]:
        """Return status snapshot for a single server, or None."""
        state = self._servers.get(name)
        if not state:
            return None
        return self._server_info(state)

    def has_server(self, name: str) -> bool:
        """Check if a server exists."""
        return name in self._servers

    @staticmethod
    def _server_info(state: MCPServerState) -> dict:
        """Convert MCPServerState to an API-friendly dict."""
        return {
            "name": state.name,
            "transport": state.config.get("transport", "stdio"),
            "enabled": state.enabled,
            "status": state.status,
            "tool_count": len(state.tools),
            "tools": [
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "permission_level": _get_tool_permission(getattr(t, "name", "")),
                }
                for t in state.tools
            ],
            "error": state.error_message,
            "connected_at": state.connected_at,
            "config": {
                k: v for k, v in state.config.items()
                if k != "enabled"
            },
        }

    # ------------------------------------------------------------------
    # Public API — config management
    # ------------------------------------------------------------------

    def get_config(self) -> dict:
        """Return the full mcp.json content."""
        return {"servers": self._load_config()}

    async def apply_config(self, new_config: dict) -> None:
        """Apply a full config replacement, reconciling differences.

        Disables removed servers, enables newly added servers with enabled=true.
        """
        async with self._lock:
            new_servers = new_config.get("servers", {})
            old_names = set(self._servers.keys())
            new_names = set(new_servers.keys())

            # Disable and remove servers that were removed
            for removed_name in old_names - new_names:
                if self._servers[removed_name].status == "connected":
                    await self._disconnect_server(removed_name)
                del self._servers[removed_name]

            # Update existing and add new servers
            for name, config in new_servers.items():
                enabled = config.pop("enabled", True)

                if name in self._servers:
                    # Update config for existing server
                    self._servers[name].config = config
                    self._servers[name].enabled = enabled
                else:
                    # Add new server
                    state = MCPServerState(name=name, config=config, enabled=enabled)
                    self._servers[name] = state
                    if enabled:
                        await self._connect_server(name, config)

            await self._save_config()


def _get_tool_permission(tool_name: str) -> str:
    """Get the permission level string for a tool from the registry."""
    try:
        from tools.registry import tool_registry
        meta = tool_registry.get_meta(tool_name)
        if meta:
            return meta.permission_level.value if hasattr(meta.permission_level, "value") else str(meta.permission_level)
    except Exception:
        pass
    return "L1"


# Global singleton — imported by api.server and agent code
mcp_manager = MCPManager()
