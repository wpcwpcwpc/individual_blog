"""
QA Agent System — Context Threshold Hook (Run post_hook)

After each Agent run, checks token usage against soft/hard thresholds.
Triggers memory distillation and writes compressed summary to Milvus.

Token estimation uses Agno native ``agent.model.count_tokens(messages,
tools, output_schema)`` — accounts for messages (incl. CompressionManager's
``compressed_content``), tool definitions, output schema, reasoning, and
multimodal attachments. This is the L4 (cross-session Milvus distiller)
trigger; L2 (in-run CompressionManager) is configured separately in
``core/memory_setup.get_compression_agent_kwargs`` and fires DURING arun().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)
event_logger = logging.getLogger("qa_agent.events")


async def context_threshold_post_hook(agent: Any, run_output: Any) -> None:
    """Run post_hook: check context usage and trigger compression if needed.

    This hook runs after every Agent turn (not every tool call).
    It uses Agno native ``agent.model.count_tokens`` to estimate current
    context size (messages + tool defs + output schema + compressed_content)
    and compares against ``settings.context_soft_threshold`` /
    ``settings.context_hard_threshold`` (configured in ``.env``, wired
    into ``QASessionState`` by ``engine.py`` / ``coordinator_agent.py``).

    Args:
        agent: The Agno Agent instance.
        run_output: The RunOutput from the current turn (Agno hook convention).
    """
    state = getattr(agent, "session_state", None)
    if state is None:
        return

    # Update token count estimate
    _update_token_count(agent, state, run_output)

    soft = state.is_over_soft_threshold()
    hard = state.is_over_hard_threshold()

    if not soft:
        return  # below threshold, nothing to do

    session_id = getattr(state, "session_id", "")
    usage_pct = state.context_usage_ratio * 100

    logger.info(
        "[Hook] Context threshold triggered (session=%s, usage=%.1f%%, hard=%s)",
        session_id, usage_pct, hard,
    )
    event_logger.info(
        "📦 [压缩] L4 阈值触发: type=%s usage=%.1f%% (当前 %d/%d tokens) session=%s",
        "hard" if hard else "soft",
        usage_pct,
        state.current_context_tokens,
        state.max_context_tokens,
        session_id,
    )

    # Fire distillation asynchronously (non-blocking)
    asyncio.create_task(
        _distill_and_write(agent, state, hard=hard)
    )


async def _distill_and_write(agent: Any, state: Any, hard: bool) -> None:
    """Run LLM distillation and write summary to Milvus."""
    session_id = getattr(state, "session_id", "")
    event_logger.info(
        "📦 [压缩] L4 蒸馏开始: type=%s session=%s",
        "hard" if hard else "soft", session_id,
    )
    try:
        from memory.distiller import distill_context
        summary = await distill_context(agent, hard=hard)

        if summary:
            from memory.writer import write_knowledge
            await write_knowledge({
                "session_id": session_id,
                "worker_id": getattr(state, "worker_id", None) or session_id,
                "compression_type": "hard" if hard else "soft",
                "compression_count": state.compression_count + 1,
                "completed_steps": summary.get("completed_steps", ""),
                "pending_steps": summary.get("pending_steps", ""),
                "findings": summary.get("findings", ""),
                "context_coverage": summary.get("context_coverage", ""),
                "game_version": getattr(state, "game_version", ""),
                "module": getattr(state, "module", ""),
            })

            # Update state bookkeeping
            state.compression_count += 1
            state.last_compression_turn = state.turn_count

            # Set System Reminder flag for post-compact context recovery
            state._reminder_pending = True
            state._reminder_reason = "compact"

            logger.info(
                "[Hook] Context %s compression complete (session=%s, compression_count=%d)",
                "hard" if hard else "soft", session_id, state.compression_count,
            )
            event_logger.info(
                "📦 [压缩] L4 蒸馏完成: type=%s compression_count=%d pending=%d chars findings=%d chars session=%s",
                "hard" if hard else "soft",
                state.compression_count,
                len(summary.get("pending_steps", "")),
                len(summary.get("findings", "")),
                session_id,
            )
            event_logger.info(
                "📦 [压缩] L4 恢复提醒: _reminder_pending=True reason=compact session=%s",
                session_id,
            )

    except Exception:
        logger.warning(
            "[Hook] Context distillation failed (session=%s) — continuing without compression",
            session_id, exc_info=True,
        )
        event_logger.info(
            "📦 [压缩] L4 蒸馏失败: session=%s — 继续运行但未压缩",
            session_id,
        )


# Diminishing returns detection thresholds (task 11.1–11.4)
_DIMINISHING_THRESHOLD = 300    # tokens per turn below which it's "low delta"
_DIMINISHING_MAX_TURNS = 3      # consecutive low-delta turns before warning


def _update_token_count(agent: Any, state: Any, run_output: Any) -> None:
    """Estimate current context token count via Agno native API.

    Uses ``agent.model.count_tokens(messages, tools, output_schema)`` which
    accounts for: message content (incl. ``compressed_content`` populated by
    CompressionManager), tool_call arguments, reasoning_content, multimodal
    attachments, tool definitions, and output schema. Closer to the true
    request size than the previous tiktoken-on-content loop.

    Also updates diminishing returns counters (tasks 11.1–11.4).
    """
    try:
        model = getattr(agent, "model", None)
        if model is None or not hasattr(model, "count_tokens"):
            logger.debug(
                "[Hook] agent.model.count_tokens unavailable (session=%s) — skip",
                getattr(state, "session_id", "?"),
            )
            return

        # Collect message history from agent memory
        messages: list = []
        mem = getattr(agent, "memory", None)
        if mem is not None:
            messages = getattr(mem, "messages", None) or []

        # tools + output_schema from Agent (both optional; count_tokens handles None)
        tools = getattr(agent, "tools", None)
        output_schema = getattr(agent, "output_schema", None)

        total_tokens = model.count_tokens(
            messages=messages,
            tools=tools,
            output_schema=output_schema,
        )

        # ── task 11.1–11.3: diminishing returns tracking ───────────────
        # L2 (CompressionManager) firing is now detected directly via
        # Agno's ``CompressionStartedEvent`` / ``CompressionCompletedEvent``
        # in ``core/stream_adapter._compression_event`` (surfaced through
        # ``server._log_event`` as ``📦 [压缩] L2 已触发``). The previous
        # delta<0 heuristic here was unreliable — compression happens
        # between tool calls DURING arun(), so by the time this post_hook
        # measures tokens, the count already reflects the compressed state,
        # and delta is usually still positive (new content added + savings
        # from compression). Keep only the diminishing-returns loop check.
        last = getattr(state, "last_turn_tokens", 0)
        delta = total_tokens - last

        if delta < 0:
            # Context shrank — productive signal (L2 compression or memory
            # trimming). Not a loop. Reset diminishing counter.
            state.diminishing_turn_count = 0
        elif delta < _DIMINISHING_THRESHOLD:
            state.diminishing_turn_count = getattr(state, "diminishing_turn_count", 0) + 1
        else:
            state.diminishing_turn_count = 0

        state.last_turn_tokens = total_tokens
        state.current_context_tokens = total_tokens

        # ── task 11.4: warn / stop on sustained low delta ──────────────
        if state.diminishing_turn_count >= _DIMINISHING_MAX_TURNS:
            session_id = getattr(state, "session_id", "?")
            logger.warning(
                "[Hook] Diminishing returns detected (session=%s, delta=%d, consecutive=%d) "
                "— LLM may be looping. Setting stop_reason.",
                session_id, delta, state.diminishing_turn_count,
            )
            state.stop_reason = "diminishing_returns"
            # Reset counter so we don't spam the log
            state.diminishing_turn_count = 0

    except Exception:
        # Agno count_tokens unavailable or raised — skip update, leave
        # state.current_context_tokens at previous value (no crash).
        logger.debug(
            "[Hook] token count update failed (session=%s) — Agno count_tokens raised",
            getattr(state, "session_id", "?"), exc_info=True,
        )
