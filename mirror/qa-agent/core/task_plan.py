"""
QA Agent System — Task Plan Data Model

Defines TaskItem and TaskPlan dataclasses for structured task tracking.
Used by task_list / task_update tools and injected into Agent system prompts.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

# Valid status values for TaskItem
TaskStatus = Literal["pending", "active", "done", "failed", "skipped"]
TERMINAL_STATUSES: set[str] = {"done", "failed", "skipped"}

# Artifact size limits
_ARTIFACT_VALUE_MAX_CHARS = 2000
_ARTIFACT_TOTAL_MAX_CHARS = 5000


def _truncate_artifacts(artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Enforce size limits on artifacts dict.

    - Single value serialized ≤ 2000 chars
    - Total serialized ≤ 5000 chars
    - Exceeding values are truncated with '...(truncated)' marker

    Args:
        artifacts: Raw artifacts dict from Agent.

    Returns:
        A new dict with values truncated as needed.
    """
    if not artifacts:
        return {}

    result: Dict[str, Any] = {}
    total_chars = 0

    for key, value in artifacts.items():
        serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value

        # Per-value limit
        if len(serialized) > _ARTIFACT_VALUE_MAX_CHARS:
            serialized = serialized[:_ARTIFACT_VALUE_MAX_CHARS - 15] + "...(truncated)"
            value = serialized  # store as truncated string

        # Total budget check
        entry_chars = len(key) + len(serialized) + 4  # key + value + overhead
        if total_chars + entry_chars > _ARTIFACT_TOTAL_MAX_CHARS:
            remaining = _ARTIFACT_TOTAL_MAX_CHARS - total_chars - len(key) - 4
            if remaining > 20:
                value = serialized[:remaining - 15] + "...(truncated)"
                result[key] = value
            # Skip remaining keys — budget exhausted
            break

        total_chars += entry_chars
        result[key] = value

    return result


@dataclass
class TaskItem:
    """A single task within a TaskPlan."""

    id: int
    description: str
    status: TaskStatus = "pending"
    result_summary: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)
    tools_used: List[str] = field(default_factory=list)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "result_summary": self.result_summary,
            "artifacts": dict(self.artifacts),
            "tools_used": list(self.tools_used),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskItem:
        return cls(
            id=data["id"],
            description=data["description"],
            status=data.get("status", "pending"),
            result_summary=data.get("result_summary", ""),
            artifacts=data.get("artifacts", {}),
            tools_used=data.get("tools_used", []),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )

    @property
    def is_terminal(self) -> bool:
        """True if the task is in a terminal state (done/failed/skipped)."""
        return self.status in TERMINAL_STATUSES


@dataclass
class TaskPlan:
    """A structured plan of ordered tasks with lifecycle management.

    Usage::

        plan = TaskPlan(goal="Test combat", strategy="...", tasks=[...])
        plan.mark_task(1, "done", "Found 5 issues")
        print(plan.to_prompt_context())
    """

    goal: str
    strategy: str
    tasks: List[TaskItem]
    created_at: float = field(default_factory=time.time)
    current_task_id: int = 0  # 0 = not started

    # ── Task lifecycle ─────────────────────────────────────────────────

    def mark_task(
        self,
        task_id: int,
        status: TaskStatus,
        summary: str = "",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> TaskItem:
        """Mark a task with a new status and auto-advance.

        Args:
            task_id: The task ID to update.
            status: New status.
            summary: Brief result summary.
            artifacts: Optional structured key-value data from this task's output.

        Returns:
            The updated TaskItem.

        Raises:
            KeyError: If task_id is not found.
        """
        task = self._get_task(task_id)

        task.status = status
        task.result_summary = summary

        if artifacts:
            task.artifacts = _truncate_artifacts(artifacts)

        if status == "active" and task.started_at is None:
            task.started_at = time.time()

        if status in TERMINAL_STATUSES:
            task.completed_at = time.time()
            # Auto-advance to next pending task
            self.advance_to_next()

        return task

    def advance_to_next(self) -> Optional[TaskItem]:
        """Advance the current_task_id to the next pending task, setting it active.

        Returns:
            The newly activated TaskItem, or None if all tasks are terminal.
        """
        for task in self.tasks:
            if task.status == "pending":
                task.status = "active"
                task.started_at = time.time()
                self.current_task_id = task.id
                return task

        # No more pending tasks
        self.current_task_id = 0
        return None

    @property
    def is_complete(self) -> bool:
        """True if all tasks are in terminal states."""
        if not self.tasks:
            return True
        return all(t.is_terminal for t in self.tasks)

    @property
    def active_task(self) -> Optional[TaskItem]:
        """Return the currently active task, or None."""
        for t in self.tasks:
            if t.status == "active":
                return t
        return None

    @property
    def progress(self) -> tuple[int, int]:
        """Return (completed_count, total_count)."""
        done = sum(1 for t in self.tasks if t.is_terminal)
        return done, len(self.tasks)

    def _get_task(self, task_id: int) -> TaskItem:
        """Look up a task by ID or raise KeyError."""
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(f"Task ID {task_id} not found")

    # ── Prompt context generation ──────────────────────────────────────

    def to_prompt_context(self) -> str:
        """Generate a system prompt section showing current task progress.

        Returns:
            Multi-line string suitable for injection into Agent system prompt.
            Empty string if there are no tasks.
        """
        if not self.tasks:
            return ""

        done_count, total = self.progress
        lines = [
            "## Current Task Progress",
            f"**Goal:** {self.goal}",
            f"**Strategy:** {self.strategy}",
            f"**Progress:** {done_count}/{total} tasks complete",
            "",
        ]

        for task in self.tasks:
            if task.status == "done":
                marker = "[x]"
                suffix = ""
                if task.artifacts:
                    keys = ", ".join(task.artifacts.keys())
                    suffix = f"  [artifacts: {keys}]"
            elif task.status == "active":
                marker = "[>]"
                suffix = "  ← YOU ARE HERE"
            elif task.status == "failed":
                marker = "[!]"
                suffix = f"  (FAILED: {task.result_summary})" if task.result_summary else "  (FAILED)"
            elif task.status == "skipped":
                marker = "[-]"
                suffix = "  (skipped)"
            else:
                marker = "[ ]"
                suffix = ""

            lines.append(f"{marker} {task.id}. {task.description}{suffix}")

        lines.append("")

        if self.is_complete:
            lines.append("All tasks complete! Generate your final summary/report.")
        elif self.active_task:
            lines.append(
                f"Focus on task {self.active_task.id}: {self.active_task.description}"
            )
            lines.append(
                "After completing it, call task_update() to mark it done and proceed."
            )

        return "\n".join(lines)

    # ── Summary for task_list output ───────────────────────────────────

    def to_summary(self) -> str:
        """Generate a text summary for the task_list tool response."""
        done_count, total = self.progress
        lines = [
            f"Goal: {self.goal}",
            f"Strategy: {self.strategy}",
            f"Progress: {done_count}/{total} tasks complete",
            "",
        ]

        for task in self.tasks:
            status_icon = {
                "pending": "⬜", "active": "▶️", "done": "✅",
                "failed": "❌", "skipped": "⏭️",
            }.get(task.status, "?")
            summary_part = f" — {task.result_summary}" if task.result_summary else ""
            lines.append(f"  {status_icon} [{task.id}] {task.description}{summary_part}")
            # Show artifacts key-value summary for completed tasks
            if task.artifacts and task.status in TERMINAL_STATUSES:
                for k, v in task.artifacts.items():
                    v_str = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                    if len(v_str) > 200:
                        v_str = v_str[:185] + "...(truncated)"
                    lines.append(f"       📦 {k}: {v_str}")

        if self.is_complete:
            lines.append("\nAll tasks complete!")

        return "\n".join(lines)

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "strategy": self.strategy,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "current_task_id": self.current_task_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskPlan:
        tasks = [TaskItem.from_dict(t) for t in data.get("tasks", [])]
        return cls(
            goal=data["goal"],
            strategy=data["strategy"],
            tasks=tasks,
            created_at=data.get("created_at", time.time()),
            current_task_id=data.get("current_task_id", 0),
        )

    @classmethod
    def from_create_json(cls, json_data: Dict[str, Any]) -> TaskPlan:
        """Create a TaskPlan from the simplified JSON format used by task_update(create).

        Expected format::

            {
                "goal": "...",
                "strategy": "...",
                "tasks": ["step 1 description", "step 2 description", ...]
            }

        Returns:
            A new TaskPlan with the first task auto-activated.
        """
        goal = json_data.get("goal", "")
        strategy = json_data.get("strategy", "")
        raw_tasks = json_data.get("tasks", [])

        if not goal:
            raise ValueError("'goal' is required in the plan JSON")
        if not raw_tasks:
            raise ValueError("'tasks' list is required and must not be empty")

        tasks = []
        for i, desc in enumerate(raw_tasks, start=1):
            if isinstance(desc, str):
                tasks.append(TaskItem(id=i, description=desc))
            elif isinstance(desc, dict):
                tasks.append(TaskItem(id=i, description=desc.get("description", str(desc))))
            else:
                tasks.append(TaskItem(id=i, description=str(desc)))

        plan = cls(goal=goal, strategy=strategy, tasks=tasks)

        # Auto-activate the first task
        if plan.tasks:
            plan.tasks[0].status = "active"
            plan.tasks[0].started_at = time.time()
            plan.current_task_id = plan.tasks[0].id

        return plan
