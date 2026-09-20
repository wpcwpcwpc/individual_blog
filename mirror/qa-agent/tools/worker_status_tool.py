"""
QA Agent System — Worker Status Tool

Provides the Coordinator with a summary view of all Workers in the pool.
Groups Workers by status and shows key metrics.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from agno.tools import tool

logger = logging.getLogger(__name__)


@tool(
    name="get_worker_status",
    description=(
        "List all Workers grouped by status (running/completed/failed/stopped) "
        "with worker_id, agent, description, duration."
    ),
)
async def get_worker_status() -> str:
    """Get a formatted summary of all Workers in the pool.

    Returns:
        Markdown-formatted status summary of all Workers.
    """
    from coordinator.worker_pool import worker_pool

    all_workers = await worker_pool.get_all()

    if not all_workers:
        return "No Workers in the pool. Use `agent_spawn` to create Workers."

    # Group by status
    groups = defaultdict(list)
    for entry in all_workers:
        groups[entry.status.value].append(entry)

    lines = ["## Worker Pool Status", ""]

    # Status display order
    status_order = ["running", "creating", "completed", "failed", "stopped", "timeout"]
    status_emoji = {
        "running": "🔄",
        "creating": "⏳",
        "completed": "✅",
        "failed": "❌",
        "stopped": "⏹️",
        "timeout": "⏰",
    }

    total = len(all_workers)
    active = sum(1 for w in all_workers if w.is_active)
    lines.append(f"**Total**: {total} Workers ({active} active)")
    lines.append("")

    for status in status_order:
        entries = groups.get(status, [])
        if not entries:
            continue

        emoji = status_emoji.get(status, "•")
        lines.append(f"### {emoji} {status.upper()} ({len(entries)})")
        lines.append("")
        lines.append("| Worker ID | Agent | Description | Duration |")
        lines.append("|-----------|-------|-------------|----------|")

        for entry in entries:
            duration = f"{entry.duration_s}s" if entry.duration_s is not None else "—"
            desc = entry.description[:50]
            if len(entry.description) > 50:
                desc += "…"
            artifact_col = entry.output_artifact_id or "—"
            lines.append(
                f"| `{entry.worker_id}` | {entry.agent_name} | {desc} | {duration} | {artifact_col} |"
            )

        lines.append("")

    return "\n".join(lines)
