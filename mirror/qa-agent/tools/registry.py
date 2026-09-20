"""
QA Agent System — Tool Registry

Global registry that maps tool names to their metadata and callable references.
Supports filtering by agent_type (normal returns all, coordinator returns
only orchestration tools).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from tools.base import PermissionLevel, ToolMeta, readonly_meta

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Singleton-style registry for all available tools and their metadata.

    Usage::

        registry = ToolRegistry()
        registry.register("file_read", file_read_fn, readonly_meta("Read a file", "file"))
        tools = registry.get_tools(agent_type="normal")
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Callable] = {}
        self._meta: Dict[str, ToolMeta] = {}
        self._coordinator_tools: set[str] = set()  # tool names available to coordinator

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        tool_fn: Callable,
        meta: ToolMeta,
        *,
        coordinator_visible: bool = False,
    ) -> None:
        """Register a tool with its metadata.

        Args:
            name: Unique tool name (must match the Agno @tool function name).
            tool_fn: The decorated Agno tool function.
            meta: ToolMeta dict with permission / concurrency info.
            coordinator_visible: If True, this tool is available to coordinator agents.
        """
        if name in self._tools:
            logger.warning("Tool '%s' is being re-registered (overwrite).", name)
        self._tools[name] = tool_fn
        self._meta[name] = meta
        if coordinator_visible:
            self._coordinator_tools.add(name)
        logger.debug("Registered tool '%s' (L%d, category=%s)",
                      name, meta.get("permission_level", 0), meta.get("category", "?"))

    def unregister(self, name: str) -> bool:
        """Unregister a single tool by name.

        Removes the tool callable, metadata, and coordinator visibility.

        Returns:
            True if the tool was found and removed, False otherwise.
        """
        if name not in self._tools:
            return False
        del self._tools[name]
        del self._meta[name]
        self._coordinator_tools.discard(name)
        logger.debug("Unregistered tool '%s'", name)
        return True

    def unregister_by_category(self, category: str) -> List[str]:
        """Unregister all tools belonging to a category.

        Useful for bulk-removing MCP tools when a server is disabled.

        Args:
            category: The category label to match (e.g. ``"mcp_my_server"``).

        Returns:
            List of tool names that were removed.
        """
        to_remove = [
            name for name, meta in self._meta.items()
            if meta.get("category") == category
        ]
        for name in to_remove:
            self.unregister(name)
        if to_remove:
            logger.info("Unregistered %d tools in category '%s'", len(to_remove), category)
        return to_remove

    def register_batch(
        self,
        entries: List[tuple[str, Callable, ToolMeta]],
        *,
        coordinator_visible: bool = False,
    ) -> None:
        """Register multiple tools at once."""
        for name, fn, meta in entries:
            self.register(name, fn, meta, coordinator_visible=coordinator_visible)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_tools(self, agent_type: str = "normal") -> List[Callable]:
        """Return tool callables filtered by agent_type.

        - ``"normal"`` / ``"worker"``: returns ALL registered tools.
        - ``"coordinator"``: returns only tools marked ``coordinator_visible``.
        """
        if agent_type == "coordinator":
            return [
                fn for name, fn in self._tools.items()
                if name in self._coordinator_tools
            ]
        return list(self._tools.values())

    def get_tools_by_names(self, names: List[str]) -> List[Callable]:
        """Return tool callables for the given names (skip missing)."""
        result = []
        for name in names:
            fn = self._tools.get(name)
            if fn is not None:
                result.append(fn)
            else:
                logger.warning("Tool '%s' requested but not registered.", name)
        return result

    def get_meta(self, tool_name: str) -> Optional[ToolMeta]:
        """Look up metadata for a tool by name."""
        return self._meta.get(tool_name)

    def get_permission_level(self, tool_name: str) -> int:
        """Shortcut to get the permission level for a tool (default L1)."""
        meta = self._meta.get(tool_name)
        if meta is None:
            return PermissionLevel.L1_AUTO
        return meta.get("permission_level", PermissionLevel.L1_AUTO)

    def is_concurrency_safe(self, tool_name: str) -> bool:
        """Check if a tool is safe for concurrent execution."""
        meta = self._meta.get(tool_name)
        if meta is None:
            return False
        return meta.get("is_concurrency_safe", False)

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return a summary list of all registered tools (for GET /tools or debugging)."""
        return [
            {
                "name": name,
                "permission_level": meta.get("permission_level", 1),
                "is_read_only": meta.get("is_read_only", True),
                "category": meta.get("category", "general"),
                "description": meta.get("description", ""),
            }
            for name, meta in self._meta.items()
        ]

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
tool_registry = ToolRegistry()
