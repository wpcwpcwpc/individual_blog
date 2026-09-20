"""
QA Agent System — Agent Spawn Tool (L1)

Allows an agent to spawn a sub-agent by name and delegate a task to it.
Used by Coordinator to dynamically create Workers.

Enhanced with WorkerPool lifecycle tracking and structured notifications.
Design reference: D5 (Enhanced agent_spawn)
"""

from __future__ import annotations

import logging
import time

from agno.tools import tool

logger = logging.getLogger(__name__)


@tool(
    name="agent_spawn",
    description=(
        "Spawn a named sub-agent to run a task. Returns worker_id + status + result. "
        "Use to delegate specialized work. Discover agents via the agent registry."
    ),
)
async def agent_spawn(
    agent_name: str,
    task: str,
    description: str = "",
    game_version: str = "",
    module: str = "",
    context_ids: list | None = None,  # task 10.2: upstream artifact IDs
) -> str:
    """Spawn a named agent and run a task.

    Args:
        agent_name: Name of the agent to spawn (from registry).
        task: Task description / message to run.
        description: Short human-readable description for tracking.
        game_version: Optional game version for context injection.
        module: Optional game module for context injection.
        context_ids: Optional list of artifact ObjectId strings from previous Workers.
            The Worker's reminder_pre_hook will automatically load these artifacts
            and inject their summaries into the Worker's system context.

    Returns:
        Structured JSON notification with worker_id, status, and result.
    """
    from agents.base import create_agent_from_definition
    from agents.registry import agent_registry
    from coordinator.notification import format_notification
    from coordinator.worker_pool import (
        WorkerEntry,
        WorkerStatus,
        generate_worker_id,
        worker_pool,
    )

    definition = agent_registry.get(agent_name)
    if definition is None:
        available = ", ".join(agent_registry._definitions.keys())
        return (
            f"Error: Agent '{agent_name}' not found in registry. "
            f"Available: {available}"
        )

    # Generate tracking IDs
    worker_id = generate_worker_id()
    session_id = f"worker__{worker_id}"
    worker_desc = description or task[:80]
    _context_ids = context_ids or []

    logger.info(
        "Spawning agent '%s' as worker '%s' for task: %s (context_ids=%s)",
        agent_name, worker_id, task[:80], _context_ids,
    )

    # Register in WorkerPool before running  (task 10.3: include context_ids)
    entry = WorkerEntry(
        worker_id=worker_id,
        agent_name=agent_name,
        description=worker_desc,
        status=WorkerStatus.CREATING,
        definition=definition,
        session_id=session_id,
        created_at=time.time(),
        context_ids=_context_ids,
    )
    await worker_pool.register(entry)

    try:
        agent = await create_agent_from_definition(
            definition,
            session_id=session_id,
            task_description=task,
            game_version=game_version,
            module=module,
            context_ids=_context_ids,  # task 10.4: forward to agent session_state
        )

        await worker_pool.update_status(worker_id, WorkerStatus.RUNNING)

        response = await agent.arun(task)

        # Extract result text
        result = ""
        if hasattr(response, "content") and response.content:
            result = str(response.content)
        else:
            result = str(response)

        await worker_pool.update_status(
            worker_id,
            WorkerStatus.COMPLETED,
            result=result,
        )

        logger.info(
            "Worker '%s' (%s) completed (response_len=%d)",
            worker_id, agent_name, len(result),
        )

        # Re-fetch entry to get updated fields
        entry = await worker_pool.get(worker_id) or entry
        return format_notification(entry, result=result)

    except Exception as e:
        error_msg = str(e)
        logger.exception(
            "Worker '%s' (%s) failed for task: %s", worker_id, agent_name, task[:80]
        )
        await worker_pool.update_status(
            worker_id,
            WorkerStatus.FAILED,
            error=error_msg,
        )

        # Re-fetch entry to get updated fields
        entry = await worker_pool.get(worker_id) or entry
        return format_notification(entry, error=error_msg)