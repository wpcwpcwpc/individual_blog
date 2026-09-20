"""
QA Agent System — Task Stop Tool

Allows the Coordinator to stop a running Worker by marking it as STOPPED
in the WorkerPool registry.

Phase 1: Registry update only (no actual process cancellation).
Phase 2 will add abort controller support for truly cancelling running Workers.

Design reference: D7 (TaskStop Tool)
"""

from __future__ import annotations

import logging

from agno.tools import tool

logger = logging.getLogger(__name__)


@tool(
    name="task_stop",
    description=(
        "Stop a running Worker by worker_id. Use to cancel work that is no "
        "longer needed."
    ),
)
async def task_stop(worker_id: str, reason: str = "") -> str:
    """Stop a running Worker.

    Args:
        worker_id: The ID of the Worker to stop.
        reason: Optional reason for stopping.

    Returns:
        Confirmation message or error.
    """
    from coordinator.worker_pool import WorkerStatus, worker_pool

    entry = await worker_pool.get(worker_id)
    if entry is None:
        return f"Error: Worker '{worker_id}' not found in the pool."

    if entry.status != WorkerStatus.RUNNING:
        return (
            f"Worker '{worker_id}' is not running (status: {entry.status.value}). "
            "Only running Workers can be stopped."
        )

    # Phase 1: Registry update only.
    # In the current synchronous model, Workers run inside agent_spawn
    # and complete before this tool could be called. This will be more
    # useful in Phase 2 with async Workers.
    await worker_pool.update_status(worker_id, WorkerStatus.STOPPED)

    stop_reason = reason or "coordinator request"
    logger.info(
        "Worker '%s' (%s) stopped. Reason: %s",
        worker_id, entry.agent_name, stop_reason,
    )

    return (
        f"Worker '{worker_id}' ({entry.agent_name}) has been stopped. "
        f"Reason: {stop_reason}"
    )
