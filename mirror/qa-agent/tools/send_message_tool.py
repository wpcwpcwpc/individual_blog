"""
QA Agent System — Send Message Tool

Allows the Coordinator to continue an existing Worker's conversation
by sending it a follow-up message. Uses the Worker's original session_id
to preserve Agno conversation history (warm path).

Design reference: D3 (SendMessage Tool — Worker Continuation)
"""

from __future__ import annotations

import logging

from agno.tools import tool

logger = logging.getLogger(__name__)


@tool(
    name="send_message",
    description=(
        "Continue an existing Worker with a follow-up message (reuses its history). "
        "Pass worker_id from a prior agent_spawn. Prefer over re-spawning when "
        "the Worker already has relevant context."
    ),
)
async def send_message(to: str, message: str) -> str:
    """Continue an existing Worker with a new message.

    Args:
        to: The worker_id of the Worker to continue.
        message: The follow-up instructions to send.

    Returns:
        Structured JSON notification with the Worker's new response.
    """
    from agents.base import create_agent_from_definition
    from coordinator.notification import format_notification
    from coordinator.worker_pool import WorkerStatus, worker_pool

    # Look up Worker
    entry = await worker_pool.get(to)
    if entry is None:
        return f"Error: Worker '{to}' not found in the pool."

    # Phase 1: Only warm path (completed/stopped Workers)
    if entry.status == WorkerStatus.RUNNING:
        return (
            f"Error: Worker '{to}' is still running. "
            "Wait for it to complete before sending a follow-up message."
        )

    if not entry.is_continuable:
        return (
            f"Error: Worker '{to}' is in status '{entry.status.value}' "
            "and cannot receive messages. Only COMPLETED or STOPPED Workers "
            "can be continued."
        )

    logger.info(
        "Continuing worker '%s' (%s) with message: %s",
        to, entry.agent_name, message[:80],
    )

    try:
        # Reconstruct Agent with the SAME session_id → Agno restores history
        agent = await create_agent_from_definition(
            entry.definition,
            session_id=entry.session_id,  # KEY: same session = same history
            task_description=message,
        )

        # Update pool status to RUNNING
        await worker_pool.update_status(to, WorkerStatus.RUNNING)

        # Run with the new message
        response = await agent.arun(message)

        # Extract result text
        result = ""
        if hasattr(response, "content") and response.content:
            result = str(response.content)
        else:
            result = str(response)

        await worker_pool.update_status(
            to,
            WorkerStatus.COMPLETED,
            result=result,
        )

        logger.info(
            "Worker '%s' (%s) continued successfully (response_len=%d)",
            to, entry.agent_name, len(result),
        )

        # Re-fetch entry to get updated fields
        entry = await worker_pool.get(to) or entry
        return format_notification(entry, result=result)

    except Exception as e:
        error_msg = str(e)
        logger.exception(
            "Worker '%s' (%s) failed during continuation: %s",
            to, entry.agent_name, message[:80],
        )
        await worker_pool.update_status(
            to,
            WorkerStatus.FAILED,
            error=error_msg,
        )

        entry = await worker_pool.get(to) or entry
        return format_notification(entry, error=error_msg)
