"""
QA Agent System — Tool Metadata

Defines the `ToolMeta` TypedDict for attaching permission / concurrency
metadata to every Agno @tool.  The metadata is consumed by pre_hook
(permission check) and post_hook (execution logging).
"""

from __future__ import annotations

from enum import IntEnum
from typing import TypedDict


class PermissionLevel(IntEnum):
    """Four-tier permission levels for tool execution."""
    L1_AUTO = 1        # Read-only, auto-execute
    L2_CONFIRM = 2     # Side-effects, requires human confirmation
    L3_VERIFY = 3      # Result verification, human pass/fail
    L4_HUMAN_ONLY = 4  # AI only suggests, human operates


class ToolMeta(TypedDict, total=False):
    """Metadata dictionary attached to each tool via the ToolRegistry.

    Fields:
        permission_level: L1-L4 permission tier.
        is_read_only: Whether the tool only reads data (no side-effects).
        is_concurrency_safe: Whether the tool can be called in parallel.
        is_destructive: Whether the tool may cause irreversible changes.
        description: Short human-readable description for logging.
        category: Grouping label (e.g. "file", "search", "execution", "mcp").
    """
    permission_level: int
    is_read_only: bool
    is_concurrency_safe: bool
    is_destructive: bool
    description: str
    category: str


# ---------------------------------------------------------------------------
# Pre-built metadata templates for common tool patterns
# ---------------------------------------------------------------------------

def readonly_meta(description: str = "", category: str = "general") -> ToolMeta:
    """Template for L1 read-only tools."""
    return ToolMeta(
        permission_level=PermissionLevel.L1_AUTO,
        is_read_only=True,
        is_concurrency_safe=True,
        is_destructive=False,
        description=description,
        category=category,
    )


def sideeffect_meta(description: str = "", category: str = "general",
                    concurrency_safe: bool = False) -> ToolMeta:
    """Template for L2 side-effect tools that need human confirmation."""
    return ToolMeta(
        permission_level=PermissionLevel.L2_CONFIRM,
        is_read_only=False,
        is_concurrency_safe=concurrency_safe,
        is_destructive=False,
        description=description,
        category=category,
    )


def destructive_meta(description: str = "", category: str = "general") -> ToolMeta:
    """Template for L2 destructive tools."""
    return ToolMeta(
        permission_level=PermissionLevel.L2_CONFIRM,
        is_read_only=False,
        is_concurrency_safe=False,
        is_destructive=True,
        description=description,
        category=category,
    )
