"""
QA Agent System — Worker Notification Protocol

Structured JSON notifications that Workers return to the Coordinator.
These are formatted as tool return values from agent_spawn / send_message,
allowing the Coordinator LLM to parse and reason about Worker results.

Design reference: D4 (Worker Notification Protocol)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from coordinator.worker_pool import WorkerEntry, WorkerStatus

logger = logging.getLogger(__name__)


@dataclass
class WorkerNotification:
    """Structured notification returned to the Coordinator after Worker completes."""
    type: str = "worker_notification"
    worker_id: str = ""
    agent_name: str = ""
    status: str = ""          # WorkerStatus.value
    summary: str = ""         # Human-readable one-line summary
    result: Optional[str] = None    # Worker's final text response (on success)
    error: Optional[str] = None     # Error message (on failure)
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON string for injection into tool response."""
        data = asdict(self)
        # Remove None values for cleaner output
        data = {k: v for k, v in data.items() if v is not None}
        return json.dumps(data, ensure_ascii=False, indent=2)


def format_notification(
    entry: WorkerEntry,
    *,
    result: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Format a WorkerEntry into a structured notification string.

    This is the primary interface used by agent_spawn and send_message
    to build their return values.

    Args:
        entry: The WorkerEntry from the pool.
        result: Override result text (if not already on entry).
        error: Override error text (if not already on entry).

    Returns:
        JSON-formatted notification string.
    """
    final_result = result or entry.result
    final_error = error or entry.error
    status = entry.status.value

    # Build summary
    if entry.status == WorkerStatus.COMPLETED:
        summary = f"Worker '{entry.description}' completed successfully"
    elif entry.status == WorkerStatus.FAILED:
        summary = f"Worker '{entry.description}' failed: {final_error or 'unknown error'}"
    elif entry.status == WorkerStatus.STOPPED:
        summary = f"Worker '{entry.description}' was stopped"
    elif entry.status == WorkerStatus.TIMEOUT:
        summary = f"Worker '{entry.description}' timed out"
    else:
        summary = f"Worker '{entry.description}' status: {status}"

    # Build usage info
    usage: Dict[str, Any] = {}
    if entry.turns_used > 0:
        usage["turns"] = entry.turns_used
    if entry.duration_s is not None:
        usage["duration_s"] = entry.duration_s
    if entry.tools_used:
        usage["tools_used"] = entry.tools_used

    notification = WorkerNotification(
        worker_id=entry.worker_id,
        agent_name=entry.agent_name,
        status=status,
        summary=summary,
        result=final_result,
        error=final_error,
        usage=usage if usage else {},
    )

    return notification.to_json()


def parse_notification(text: str) -> Optional[WorkerNotification]:
    """Parse a notification JSON string back into a WorkerNotification.

    Useful for testing and log inspection.

    Args:
        text: JSON string (as produced by format_notification).

    Returns:
        WorkerNotification instance, or None if parsing fails.
    """
    try:
        data = json.loads(text)
        if data.get("type") != "worker_notification":
            return None
        return WorkerNotification(
            type=data.get("type", "worker_notification"),
            worker_id=data.get("worker_id", ""),
            agent_name=data.get("agent_name", ""),
            status=data.get("status", ""),
            summary=data.get("summary", ""),
            result=data.get("result"),
            error=data.get("error"),
            usage=data.get("usage", {}),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse worker notification: %s", exc)
        return None
