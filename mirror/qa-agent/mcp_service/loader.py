"""
QA Agent System — MCP Tool Loader

Loads MCP server tools via Agno MCPTools.
Reads server configs from the MCP config file (MCP_CONFIG_PATH) and applies
L1/L2 permission tagging based on tool annotations.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# MCP tool names that always require L2 confirmation
_L2_TOOL_PATTERNS = [
    "bash",
    "write",
    "delete",
    "create",
    "modify",
    "update",
    "execute",
    "run",
]


def load_mcp_config(config_path: str = "") -> Dict[str, Any]:
    """Load MCP server configurations from a JSON file.

    Empty ``config_path`` (the default) means MCP is disabled — no servers are
    discovered and the returned dict is empty.

    Expected format:
    {
        "servers": {
            "my_stdio_server": {
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "my_mcp_server"],
                "env": {}
            },
            "my_sse_server": {
                "transport": "sse",
                "url": "http://localhost:8080/sse"
            }
        }
    }

    Args:
        config_path: Path to the MCP config JSON file.

    Returns:
        Dict of server name → server config, or empty dict if file not found.
    """
    if not (config_path or "").strip():
        logger.debug("MCP config path unset — no MCP servers loaded")
        return {}
    path = Path(config_path)
    if not path.exists():
        logger.debug("MCP config not found at %s — no MCP servers loaded", config_path)
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        servers = data.get("servers", {})
        logger.info("Loaded MCP config: %d server(s) defined", len(servers))
        return servers
    except Exception as e:
        logger.error("Failed to parse MCP config at %s: %s", config_path, e)
        return {}


async def load_mcp_tools(
    config_path: str = "",
) -> List[Any]:
    """Load all MCP tools from configured servers.

    Connects to each server, loads available tools, and tags them
    with appropriate permission levels. Failed servers are skipped gracefully.

    Args:
        config_path: Path to the MCP config JSON file.

    Returns:
        List of Agno-compatible tool objects from all available MCP servers.
    """
    servers = load_mcp_config(config_path)
    if not servers:
        return []

    all_tools: List[Any] = []

    for server_name, server_config in servers.items():
        tools = await _load_server_tools(server_name, server_config)
        all_tools.extend(tools)
        logger.info(
            "MCP server '%s': loaded %d tool(s)", server_name, len(tools)
        )

    logger.info("Total MCP tools loaded: %d", len(all_tools))
    return all_tools


async def _load_server_tools(
    server_name: str,
    server_config: Dict[str, Any],
) -> List[Any]:
    """Load tools from a single MCP server.

    Args:
        server_name: Human-readable server name (for logging).
        server_config: Server config dict with transport, command/url, args, env.

    Returns:
        List of tool objects, or empty list on failure.
    """
    transport = server_config.get("transport", "stdio")

    try:
        if transport == "stdio":
            return await _load_stdio_tools(server_name, server_config)
        elif transport == "sse":
            return await _load_sse_tools(server_name, server_config)
        else:
            logger.warning(
                "MCP server '%s': unknown transport '%s', skipping",
                server_name, transport,
            )
            return []

    except ImportError:
        logger.warning(
            "MCP server '%s': agno MCPTools not available — skipping", server_name
        )
        return []
    except Exception as e:
        logger.warning(
            "MCP server '%s': connection failed (%s) — skipping (degraded mode)",
            server_name, e,
        )
        return []


async def _load_stdio_tools(server_name: str, config: Dict[str, Any]) -> List[Any]:
    """Load tools from a stdio MCP server."""
    from agno.tools.mcp import MCPTools
    from mcp import StdioServerParameters

    command = config.get("command", "python")
    args = config.get("args", [])
    env = config.get("env", None)

    server_params = StdioServerParameters(
        command=command,
        args=args,
        env=env,
    )

    async with MCPTools(transport=server_params) as mcp_tools:
        tools = mcp_tools.tools or []
        # Tag tools with permission metadata
        _tag_mcp_tools(tools, server_name)
        return list(tools)


async def _load_sse_tools(server_name: str, config: Dict[str, Any]) -> List[Any]:
    """Load tools from an SSE MCP server."""
    from agno.tools.mcp import MCPTools

    url = config.get("url", "")
    if not url:
        logger.error("MCP server '%s': missing 'url' for SSE transport", server_name)
        return []

    async with MCPTools(url=url) as mcp_tools:
        tools = mcp_tools.tools or []
        _tag_mcp_tools(tools, server_name)
        return list(tools)


def _tag_mcp_tools(tools: List[Any], server_name: str) -> None:
    """Tag MCP tools with permission level based on name and annotations.

    Tools matching L2 patterns or with readOnlyHint=False get L2 permission.
    All others default to L1.
    """
    from tools.registry import tool_registry
    from tools.base import readonly_meta, sideeffect_meta

    for tool in tools:
        tool_name = getattr(tool, "name", "") or ""
        annotations = getattr(tool, "annotations", {}) or {}

        # Check readOnlyHint from MCP annotations
        read_only_hint = annotations.get("readOnlyHint", True)

        # Check name patterns
        is_l2 = not read_only_hint or any(
            pattern in tool_name.lower() for pattern in _L2_TOOL_PATTERNS
        )

        if is_l2:
            meta = sideeffect_meta(
                description=f"MCP:{server_name}/{tool_name}",
                category=f"mcp_{server_name}",
            )
        else:
            meta = readonly_meta(
                description=f"MCP:{server_name}/{tool_name}",
                category=f"mcp_{server_name}",
            )

        # Register in tool registry (for permission checks in hooks)
        try:
            tool_registry.register(
                name=tool_name,
                tool_fn=tool,
                meta=meta,
            )
        except Exception:
            pass  # Registration failure doesn't block tool usage

        logger.debug(
            "MCP tool '%s' tagged as %s (server=%s)",
            tool_name, "L2" if is_l2 else "L1", server_name,
        )


def create_default_mcp_config(output_path: str = "mcp.json") -> None:
    """Write a minimal MCP config template to ``output_path``.

    Args:
        output_path: Where to write the config file.
    """
    default_config = {"servers": {}}

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=2, ensure_ascii=False)

    logger.info("Created default MCP config at %s", output_path)
