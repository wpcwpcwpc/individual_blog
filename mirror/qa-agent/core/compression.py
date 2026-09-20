"""
QA Agent System — Tool-Result-Token CompressionManager

Subclass of Agno ``CompressionManager`` that triggers compression based on
the **cumulative token count of uncompressed tool RESULT messages** — not
on tool-call COUNT and not on TOTAL context tokens.

Why subclass
------------
Agno's stock triggers:

- ``compress_token_limit`` — fires when TOTAL context (system + user +
  tool defs + schema + tool results) crosses the limit. A long system
  prompt + many user turns can trip this with NO large tool results
  present — compression LLM gets called for nothing.

- count-based trigger (Agno's stock count parameter) — fires when N
  uncompressed tool messages accumulate. Three ``file_edit`` calls
  returning "Edit OK" (≈50 tokens each) trigger compression just as
  readily as three large tool results returning 1700 tokens each.
  Wastes a compression LLM call on tiny results.

Operator feedback was unambiguous: "我不想以 tool 次数来做压缩，
而是根据 tool 的返回字节数据量（换算 token）去做压缩". This
subclass delivers exactly that — sum the tokens of currently-uncompressed
``role="tool"`` messages, fire when the sum crosses
``compress_tool_results_token_limit``.

Defaults kept sane
------------------
The total-context ``compress_token_limit`` is RETAINED as a safety net
(only fires if no tool-result threshold caught a runaway context first).
The count-based trigger is structurally absent from this subclass —
there is no count parameter on ``__init__`` and no count-based branch
in the trigger evaluation (both sync and async entry points are
overridden; Agno's ``ashould_compress`` duplicates rather than
delegates, so a sync-only override would never run on ``arun()``).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type, Union

from agno.compression.manager import CompressionManager
from agno.models.base import Model
from agno.models.message import Message
from pydantic import BaseModel

logger = logging.getLogger(__name__)
event_logger = logging.getLogger("qa_agent.events")


class ToolResultTokenCompressionManager(CompressionManager):
    """CompressionManager that triggers on tool-result token sum.

    Adds one new field — ``compress_tool_results_token_limit`` — and
    overrides BOTH :meth:`should_compress` and :meth:`ashould_compress`
    (Agno's async variant duplicates the logic instead of delegating, so
    a sync-only override never runs on the ``arun()`` path). The override
    computes the token count of ONLY the currently-uncompressed
    ``role="tool"`` messages, then fires when that sum crosses the limit.
    Small tool results (file_edit "OK",
    wait confirmations) contribute negligible tokens and never trip the
    threshold alone; only large accumulated results (file reads, grep hits,
    shell output) push the sum past the limit.

    The override preserves Agno's total-context ``compress_token_limit``
    as a safety net (runaway context with no tool results still gets
    caught). Count-based triggering is structurally absent — no count
    parameter on ``__init__`` and no count-based branch in the trigger
    evaluation.
    """

    # ``compress_tool_results_token_limit`` is added via __init__ rather
    # than as a dataclass field to avoid touching the parent's dataclass
    # layout (parent uses ``field()`` for stats default_factory; adding
    # a positional field after that breaks kwargs-only instantiation).
    compress_tool_results_token_limit: Optional[int]

    def __init__(
        self,
        model: Optional[Model] = None,
        compress_tool_results: bool = True,
        compress_token_limit: Optional[int] = None,
        compress_tool_call_instructions: Optional[str] = None,
        compress_tool_results_token_limit: Optional[int] = None,
        stats: Optional[dict] = None,
    ) -> None:
        super().__init__(
            model=model,
            compress_tool_results=compress_tool_results,
            compress_token_limit=compress_token_limit,
            compress_tool_call_instructions=compress_tool_call_instructions,
            stats=stats or {},
        )
        self.compress_tool_results_token_limit = compress_tool_results_token_limit

    # ── Token estimation helpers ──────────────────────────────────────
    def _sum_tool_result_tokens(
        self,
        uncompressed: List[Message],
        model: Optional[Model],
    ) -> int:
        """Estimate total tokens of uncompressed tool-result messages.

        Prefers Agno native ``model.count_tokens`` (honors
        ``compressed_content``, tool_call args, multimodal attachments —
        closer to true request size). Falls back to chars/4 heuristic
        if model is unavailable or raises.

        Args:
            uncompressed: List of ``role="tool"`` Messages without
                ``compressed_content``.
            model: The Agent's model (used for native token counting).

        Returns:
            Estimated token sum across all uncompressed tool results.
        """
        if not uncompressed:
            return 0

        if model is not None and hasattr(model, "count_tokens"):
            try:
                # Positional call matches Agno's own usage in
                # CompressionManager.should_compress (avoids kwarg
                # name drift between ``response_format``/``output_schema``
                # across versions).
                return int(model.count_tokens(uncompressed, None, None))
            except Exception:
                logger.debug(
                    "model.count_tokens failed for tool-result subset — "
                    "falling back to chars/4",
                    exc_info=True,
                )

        # Fallback: chars/4 heuristic (close enough for trigger decisions)
        total_chars = 0
        for m in uncompressed:
            content = getattr(m, "content", None) or ""
            # ``content`` may be a list of multimodal parts; coerce to str.
            total_chars += len(str(content))
        return total_chars // 4

    # ── Shared trigger evaluation ────────────────────────────────────
    def _evaluate_trigger(
        self,
        messages: List[Message],
        tools: Optional[List],
        model: Optional[Model],
        response_format: Optional[Union[Dict, Type[BaseModel]]],
    ) -> bool:
        """Shared decision core used by BOTH sync and async entry points.

        Evaluation order (first match wins):

        1. **Disabled** → ``False``.
        2. **Tool-result-token threshold** (primary) — sum tokens of
           currently uncompressed ``role="tool"`` messages; fire if ≥
           ``compress_tool_results_token_limit``.
        3. **Total-context safety net** (secondary) — fire if total
           tokens ≥ ``compress_token_limit``. Catches runaway context
           even when no single tool result is large.

        Count-based triggering is structurally absent from this subclass
        — there is no count parameter on ``__init__`` and no count-based
        branch here.
        """
        if not self.compress_tool_results:
            return False

        uncompressed = [
            m for m in messages
            if self._is_tool_result_message(m)
            and getattr(m, "compressed_content", None) is None
        ]

        # ── Primary: tool-result-token-based trigger ────────────────
        if self.compress_tool_results_token_limit is not None and uncompressed:
            tool_tokens = self._sum_tool_result_tokens(uncompressed, model)
            limit = self.compress_tool_results_token_limit
            fired = tool_tokens >= limit
            event_logger.info(
                "📦 [压缩] L2 判定: tool_result_tokens=%d / limit=%d "
                "(across %d uncompressed tool msgs) → %s",
                tool_tokens, limit, len(uncompressed),
                "🔥 触发" if fired else "skip",
            )
            if fired:
                return True

        # ── Secondary: total-context safety net ─────────────────────
        if self.compress_token_limit is not None and model is not None:
            try:
                tokens = int(model.count_tokens(messages, tools, response_format))
            except Exception:
                logger.debug("total-context count_tokens failed", exc_info=True)
                tokens = 0
            if tokens >= self.compress_token_limit:
                event_logger.info(
                    "📦 [压缩] L2 判定: total_context_tokens=%d / limit=%d → 🔥 触发(安全网)",
                    tokens, self.compress_token_limit,
                )
                return True

        return False

    # ── Overrides (sync AND async — Agno does not delegate between them) ──
    def should_compress(
        self,
        messages: List[Message],
        tools: Optional[List] = None,
        model: Optional[Model] = None,
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
    ) -> bool:
        """Sync trigger check — called by Agno ``Model.response``/``response_stream``.

        See :meth:`_evaluate_trigger` for the decision policy.
        """
        return self._evaluate_trigger(messages, tools, model, response_format)

    async def ashould_compress(
        self,
        messages: List[Message],
        tools: Optional[List] = None,
        model: Optional[Model] = None,
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
    ) -> bool:
        """Async trigger check — called by Agno ``Model.aresponse``/``aresponse_stream``.

        CRITICAL: Agno's base ``ashould_compress`` contains a COPY of the
        sync decision logic and does NOT delegate to ``should_compress``.
        All qa-agent runs go through ``arun() → aresponse``, so overriding
        only the sync method leaves the token-sum trigger as dead code on
        the live path (observed in agent_events.log: compression fired via
        the base safety net on every model call with zero ``L2 判定`` lines).
        Both entry points must be overridden; both share
        :meth:`_evaluate_trigger`.

        Agno's ``Model.acount_tokens`` itself just wraps the sync
        ``count_tokens``, so the shared core uses sync counting — no extra
        latency, identical decision.
        """
        return self._evaluate_trigger(messages, tools, model, response_format)
