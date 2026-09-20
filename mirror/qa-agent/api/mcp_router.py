"""
QA Agent System — MCP Management API Router

Provides REST endpoints for managing MCP servers:
- List / detail / add / update / delete servers
- Enable / disable / reconnect servers
- Get / update mcp.json config
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.schemas import (
    AddMCPServerRequest,
    MCPConfigResponse,
    MCPServerInfo,
    MCPServersResponse,
    MCPToolInfo,
    UpdateMCPConfigRequest,
    UpdateMCPServerRequest,
)
from mcp_service.manager import mcp_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_to_info(server_dict: dict) -> MCPServerInfo:
    """Convert an MCPManager server dict to an MCPServerInfo response model."""
    return MCPServerInfo(
        name=server_dict["name"],
        transport=server_dict.get("transport", "stdio"),
        enabled=server_dict.get("enabled", False),
        status=server_dict.get("status", "disconnected"),
        tool_count=server_dict.get("tool_count", 0),
        tools=[
            MCPToolInfo(
                name=t.get("name", ""),
                description=t.get("description", ""),
                permission_level=t.get("permission_level", "L1"),
            )
            for t in server_dict.get("tools", [])
        ],
        error=server_dict.get("error"),
        connected_at=server_dict.get("connected_at"),
        config=server_dict.get("config"),
    )


# ---------------------------------------------------------------------------
# Server list & detail
# ---------------------------------------------------------------------------

@router.get("/servers", response_model=MCPServersResponse)
async def list_mcp_servers():
    """List all configured MCP servers and their current status."""
    servers = mcp_manager.list_servers()
    return MCPServersResponse(
        servers=[_state_to_info(s) for s in servers]
    )


@router.get("/servers/{name}", response_model=MCPServerInfo)
async def get_mcp_server(name: str):
    """Get details of a single MCP server."""
    server = mcp_manager.get_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    return _state_to_info(server)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("/servers", response_model=MCPServerInfo, status_code=201)
async def add_mcp_server(req: AddMCPServerRequest):
    """Add a new MCP server configuration."""
    if mcp_manager.has_server(req.name):
        raise HTTPException(status_code=409, detail=f"MCP server '{req.name}' already exists")

    # Build config dict from request
    config: dict = {"transport": req.transport}
    if req.transport == "stdio":
        if req.command:
            config["command"] = req.command
        if req.args is not None:
            config["args"] = req.args
        if req.env is not None:
            config["env"] = req.env
    elif req.transport == "sse":
        if req.url:
            config["url"] = req.url

    try:
        state = await mcp_manager.add_server(req.name, config, enabled=req.enabled)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    server = mcp_manager.get_server(req.name)
    return _state_to_info(server)


@router.put("/servers/{name}", response_model=MCPServerInfo)
async def update_mcp_server(name: str, req: UpdateMCPServerRequest):
    """Update an existing MCP server's configuration."""
    if not mcp_manager.has_server(name):
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    updates = {}
    if req.command is not None:
        updates["command"] = req.command
    if req.args is not None:
        updates["args"] = req.args
    if req.env is not None:
        updates["env"] = req.env
    if req.url is not None:
        updates["url"] = req.url

    try:
        await mcp_manager.update_server(name, updates)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    server = mcp_manager.get_server(name)
    return _state_to_info(server)


@router.delete("/servers/{name}")
async def delete_mcp_server(name: str):
    """Delete an MCP server (disconnects if connected)."""
    if not mcp_manager.has_server(name):
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    try:
        await mcp_manager.remove_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    return {"removed": True}


# ---------------------------------------------------------------------------
# Enable / disable / reconnect
# ---------------------------------------------------------------------------

@router.post("/servers/{name}/enable", response_model=MCPServerInfo)
async def enable_mcp_server(name: str):
    """Enable and connect an MCP server."""
    try:
        await mcp_manager.enable_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    server = mcp_manager.get_server(name)
    return _state_to_info(server)


@router.post("/servers/{name}/disable", response_model=MCPServerInfo)
async def disable_mcp_server(name: str):
    """Disable and disconnect an MCP server."""
    try:
        await mcp_manager.disable_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    server = mcp_manager.get_server(name)
    return _state_to_info(server)


@router.post("/servers/{name}/reconnect", response_model=MCPServerInfo)
async def reconnect_mcp_server(name: str):
    """Reconnect an MCP server (close old connection + establish new)."""
    try:
        await mcp_manager.reconnect_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    server = mcp_manager.get_server(name)
    return _state_to_info(server)


# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------

@router.get("/config", response_model=MCPConfigResponse)
async def get_mcp_config():
    """Get the full mcp.json configuration."""
    config = mcp_manager.get_config()
    return MCPConfigResponse(servers=config.get("servers", {}))


@router.put("/config", response_model=MCPConfigResponse)
async def update_mcp_config(req: UpdateMCPConfigRequest):
    """Replace the entire mcp.json configuration.

    Reconciles differences: disables removed servers, enables new ones.
    """
    await mcp_manager.apply_config({"servers": req.servers})
    config = mcp_manager.get_config()
    return MCPConfigResponse(servers=config.get("servers", {}))
