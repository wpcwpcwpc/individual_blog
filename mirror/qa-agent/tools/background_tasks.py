"""
Background Task Manager

Manages long-running shell commands executed in background threads.
Supports:
- Explicit background execution (run_in_background=True)
- Auto-backgrounding (commands exceeding BLOCKING_BUDGET_MS)
- Status queries and output retrieval
- Timeout enforcement and cleanup

References:
  - Claude Code ASSISTANT_BLOCKING_BUDGET_MS
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class BackgroundTask:
    """Tracks a single background command execution."""
    task_id: str
    command: str
    description: str
    status: TaskStatus = TaskStatus.RUNNING
    start_time: float = field(default_factory=time.monotonic)
    end_time: Optional[float] = None
    output: str = ""
    exit_code: Optional[int] = None
    _process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def elapsed_s(self) -> float:
        end = self.end_time or time.monotonic()
        return end - self.start_time

    @property
    def is_done(self) -> bool:
        return self.status != TaskStatus.RUNNING


# Commands that should NOT be auto-backgrounded
# (they are intentionally long-running or time-dependent)
DISALLOWED_AUTO_BACKGROUND = frozenset({
    "sleep", "watch", "top", "htop", "tail -f",
})


class BackgroundTaskManager:
    """Manages background command execution with thread-safe state tracking."""

    def __init__(self, default_timeout: int = 120):
        self._tasks: Dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()
        self._default_timeout = default_timeout

    def start_task(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        description: str = "",
    ) -> str:
        """Start a command in a background thread.

        Args:
            command: Shell command to execute.
            cwd: Working directory.
            timeout: Max seconds before killing the process.
            description: Human-readable description.

        Returns:
            task_id (UUID string).
        """
        task_id = str(uuid.uuid4())[:8]
        effective_timeout = timeout or self._default_timeout

        task = BackgroundTask(
            task_id=task_id,
            command=command,
            description=description or command[:80],
        )

        thread = threading.Thread(
            target=self._run_command,
            args=(task, cwd, effective_timeout),
            daemon=True,
            name=f"bg-task-{task_id}",
        )
        task._thread = thread

        with self._lock:
            self._tasks[task_id] = task

        thread.start()

        logger.info(
            "[bg_tasks] Started task %s: %s (timeout=%ds)",
            task_id, command[:80], effective_timeout,
        )
        return task_id

    def _run_command(
        self,
        task: BackgroundTask,
        cwd: Optional[str],
        timeout: int,
    ) -> None:
        """Execute command in subprocess (runs in background thread)."""
        try:
            if sys.platform == "win32":
                shell_cmd = ["cmd", "/c", task.command]
            else:
                shell_cmd = ["bash", "-c", task.command]

            proc = subprocess.Popen(
                shell_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
            )
            task._process = proc

            try:
                stdout, _ = proc.communicate(timeout=timeout)
                with task._lock:
                    task.output = stdout or ""
                    task.exit_code = proc.returncode
                    task.status = (
                        TaskStatus.COMPLETED if proc.returncode == 0
                        else TaskStatus.FAILED
                    )
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    stdout, _ = proc.communicate(timeout=5)
                    with task._lock:
                        task.output = (stdout or "") + "\n[Process killed after timeout]"
                except Exception:
                    with task._lock:
                        task.output = "[Process killed after timeout, output unavailable]"
                with task._lock:
                    task.exit_code = -9
                    task.status = TaskStatus.TIMEOUT

        except FileNotFoundError:
            with task._lock:
                task.output = f"Error: Shell not found for command: {task.command}"
                task.status = TaskStatus.FAILED
                task.exit_code = -1

        except Exception as e:
            with task._lock:
                task.output = f"Error: {e}"
                task.status = TaskStatus.FAILED
                task.exit_code = -1
            logger.exception("[bg_tasks] Task %s failed", task.task_id)

        finally:
            with task._lock:
                task.end_time = time.monotonic()
                task._process = None

                # Truncate very long output
                max_output = 50_000
                if len(task.output) > max_output:
                    task.output = (
                        task.output[:max_output]
                        + f"\n\n[Output truncated at {max_output} chars]"
                    )

    def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a background task.

        Returns:
            Dict with task info, or None if task_id not found.
        """
        with self._lock:
            task = self._tasks.get(task_id)

        if task is None:
            return None

        # Snapshot mutable fields under per-task lock for consistency
        with task._lock:
            status_val = task.status.value
            exit_code_val = task.exit_code
            end_time_val = task.end_time
            output_val = task.output
            is_done_val = task.is_done

        return {
            "task_id": task.task_id,
            "command": task.command,
            "description": task.description,
            "status": status_val,
            "elapsed_s": (end_time_val or time.monotonic()) - task.start_time,
            "exit_code": exit_code_val,
            "output": output_val if is_done_val else self._get_partial_output(task),
        }

    def _get_partial_output(self, task: BackgroundTask) -> str:
        """Try to get partial output from a still-running task."""
        proc = task._process
        if proc is None:
            return "(still running, no output yet)"

        # Read whatever is available without blocking
        # This is best-effort; subprocess doesn't support non-blocking reads easily
        return "(still running...)"

    def cleanup_expired(self, max_age_s: float = 600) -> int:
        """Remove completed tasks older than max_age_s.

        Returns:
            Number of tasks cleaned up.
        """
        now = time.monotonic()
        to_remove = []

        with self._lock:
            for task_id, task in self._tasks.items():
                if task.is_done and task.end_time and (now - task.end_time) > max_age_s:
                    to_remove.append(task_id)

            for task_id in to_remove:
                del self._tasks[task_id]

        if to_remove:
            logger.info("[bg_tasks] Cleaned up %d expired tasks", len(to_remove))
        return len(to_remove)

    def can_auto_background(self, command: str) -> bool:
        """Check if a command is eligible for auto-backgrounding.

        Commands like `sleep` should NOT be auto-backgrounded because
        they are intentionally long-running.
        """
        cmd_lower = command.strip().lower()
        for blocked in DISALLOWED_AUTO_BACKGROUND:
            if cmd_lower.startswith(blocked):
                return False
        return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)
