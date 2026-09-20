"""
QA Agent System — Tool Execution Scheduler

Manages concurrent tool execution based on concurrency safety:
- Read-only tools (is_concurrency_safe=True) → parallel via asyncio.gather
- Side-effect tools (is_concurrency_safe=False) → sequential, after all safe complete

References: Claude Code StreamingToolExecutor
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TrackedToolCall:
    """A tool call tracked by the scheduler."""
    call_id: str
    tool_name: str
    tool_args: Dict[str, Any]
    is_concurrency_safe: bool
    execute_fn: Callable[..., Coroutine]   # async fn to call
    order: int                              # original request order

    # Filled after execution
    result: Any = None
    error: Optional[Exception] = None
    elapsed_ms: float = 0.0
    status: str = "queued"   # queued | executing | completed | failed


class ToolExecutionScheduler:
    """Schedule tool calls for optimal parallel/sequential execution.

    Usage::

        scheduler = ToolExecutionScheduler()

        # Add tool calls from a single LLM response
        scheduler.add(TrackedToolCall(...))
        scheduler.add(TrackedToolCall(...))

        # Execute with optimal parallelism
        results = await scheduler.execute_all()
        # results are in original request order
    """

    def __init__(self) -> None:
        self._calls: List[TrackedToolCall] = []

    def add(self, call: TrackedToolCall) -> None:
        """Add a tool call to the execution queue."""
        self._calls.append(call)

    def clear(self) -> None:
        """Clear all queued calls."""
        self._calls.clear()

    async def execute_all(self) -> List[TrackedToolCall]:
        """Execute all queued calls with optimal parallelism.

        Strategy:
        1. Group consecutive safe calls into batches
        2. Execute each safe batch in parallel (asyncio.gather)
        3. Execute unsafe calls sequentially between batches
        4. Return results in original request order

        Returns:
            List of TrackedToolCall objects with results filled in,
            sorted by original order.
        """
        if not self._calls:
            return []

        batches = self._build_batches()
        completed: List[TrackedToolCall] = []

        for batch in batches:
            if batch.is_parallel:
                # Execute all safe calls in parallel
                logger.debug(
                    "[Scheduler] Parallel batch: %s",
                    [c.tool_name for c in batch.calls],
                )
                tasks = [self._execute_single(c) for c in batch.calls]
                await asyncio.gather(*tasks)
            else:
                # Execute unsafe calls one by one
                for call in batch.calls:
                    logger.debug("[Scheduler] Sequential: %s", call.tool_name)
                    await self._execute_single(call)

            completed.extend(batch.calls)

        # Sort by original order
        completed.sort(key=lambda c: c.order)

        self.clear()
        return completed

    def _build_batches(self) -> List[_Batch]:
        """Group calls into parallel/sequential batches.

        Consecutive safe calls form a parallel batch.
        Each unsafe call forms its own sequential batch.
        """
        batches: List[_Batch] = []
        current_safe: List[TrackedToolCall] = []

        for call in self._calls:
            if call.is_concurrency_safe:
                current_safe.append(call)
            else:
                # Flush any accumulated safe calls as parallel batch
                if current_safe:
                    batches.append(_Batch(calls=current_safe, is_parallel=True))
                    current_safe = []
                # Unsafe call as sequential batch
                batches.append(_Batch(calls=[call], is_parallel=False))

        # Flush remaining safe calls
        if current_safe:
            batches.append(_Batch(calls=current_safe, is_parallel=True))

        return batches

    async def _execute_single(self, call: TrackedToolCall) -> None:
        """Execute a single tool call and record result/error/timing."""
        call.status = "executing"
        start = time.monotonic()

        try:
            call.result = await call.execute_fn(call.tool_name, call.tool_args)
            call.status = "completed"
        except Exception as e:
            call.error = e
            call.status = "failed"
            logger.warning(
                "[Scheduler] Tool '%s' failed: %s", call.tool_name, e,
            )

        call.elapsed_ms = (time.monotonic() - start) * 1000

    @property
    def pending_count(self) -> int:
        return len(self._calls)


@dataclass
class _Batch:
    """A group of tool calls to execute together."""
    calls: List[TrackedToolCall]
    is_parallel: bool
