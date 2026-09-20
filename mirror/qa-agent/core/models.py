"""
QA Agent System — LLM Model Configuration

Creates Agno-compatible model instances from the centralized config.
Uses OpenAICompatModel (a subclass of OpenAILike), so any endpoint that
speaks the OpenAI chat-completions protocol can be plugged in — the
shipped default targets the DeepSeek official API. Upstream services may
emit incomplete tool_call entries, so clean OpenAI-format messages are
enforced — especially tool_calls with valid function.name fields.

Compatible with Agno 2.5.14+
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Type, Union

import httpx
from agno.models.message import Message
from agno.models.openai.like import OpenAILike
from pydantic import BaseModel

from core.config import QAAgentSettings, settings
from core.errors import LLMNotConfiguredError
from core.rate_limiter import RateLimitedModel, get_rate_limiter

logger = logging.getLogger(__name__)


@dataclass
class OpenAICompatModel(OpenAILike):
    """OpenAILike subclass with message sanitization for OpenAI-compatible APIs.

    Fixes a compatibility issue where streaming responses may produce
    incomplete tool_call entries (only containing an 'id' field, missing
    'type' and 'function'). When these are sent back in conversation history:

    1. The malformed tool_call (missing function.name) causes Claude to reject
       the tool_use block with "tool_use.name: Field required".
    2. The orphaned tool_result message referencing the removed tool_call's id
       causes Claude to reject with "unexpected tool_use_id found in tool_result
       blocks".

    This subclass overrides _format_all_messages to:
    - Remove malformed tool_calls (missing function.name) from assistant messages
    - Remove orphaned tool_result messages whose tool_call_id no longer has a
      matching tool_use in the preceding assistant message

    It also overrides _format_message to:
    - For deepseek-* models, pass back ``reasoning_content`` on assistant
      messages (required by the DeepSeek thinking-mode protocol — without this,
      multi-turn calls fail with
      ``invalid_request_error: The reasoning_content in the thinking mode
      must be passed back to the API.``).
    """

    def _format_message(
        self, message: Message, compress_tool_results: bool = True
    ) -> Dict[str, Any]:
        """Format a single Message, with deepseek thinking-mode support.

        Delegates to ``OpenAIChat._format_message`` for base OpenAI shape,
        then re-attaches ``reasoning_content`` for assistant messages when
        the active model is a deepseek-family thinking model. That is what the
        provider's ``deepseek-*`` endpoints require to be passed back on the
        next turn; other OpenAI-compatible models neither produce nor expect
        this field, so we deliberately scope the override to the
        ``deepseek-`` prefix.
        """
        msg_dict = super()._format_message(message, compress_tool_results)

        model_id = (getattr(self, "id", "") or "")
        if (
            model_id.startswith("deepseek-")
            and message.role == "assistant"
            and getattr(message, "reasoning_content", None) is not None
        ):
            msg_dict["reasoning_content"] = message.reasoning_content

        return msg_dict

    def _format_all_messages(
        self, messages: List[Message], compress_tool_results: bool = True
    ) -> List[Dict[str, Any]]:
        """Format all messages with sanitization for OpenAI-compatible providers.

        Sanitizes the message list to remove:
        1. Malformed tool_calls (missing function.name) from assistant messages
        2. Orphaned tool result messages whose tool_call_id references a removed
           tool_call

        This ensures Claude API receives a consistent message history where every
        tool_result has a matching tool_use in the previous assistant message.
        """
        from agno.utils.message import normalize_tool_messages, reformat_tool_call_ids

        # Step 1: Standard Agno normalization
        messages = normalize_tool_messages(messages)
        normalized = reformat_tool_call_ids(messages, provider="openai_chat")

        # Step 2: Collect IDs of malformed tool_calls that will be removed,
        # AND normalize empty/null arguments to "{}" to prevent the upstream
        # API from producing tool_use.input=null when converting to Claude-native
        # format.
        removed_tool_call_ids: Set[str] = set()
        for msg in normalized:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    func = tc.get("function")
                    if not (func and func.get("name")):
                        tc_id = tc.get("id")
                        if tc_id:
                            removed_tool_call_ids.add(tc_id)
                    elif func is not None:
                        # Ensure arguments is always a valid JSON object string.
                        # The upstream API converts function.arguments →
                        # tool_use.input; if arguments is "" / null / None, Claude
                        # rejects with "tool_use.input: Field required".
                        args = func.get("arguments")
                        if not args:  # None, "", "null"
                            func["arguments"] = "{}"

        if removed_tool_call_ids:
            logger.warning(
                "Tool-call sanitizer: removing %d malformed tool_call(s) and their "
                "orphaned tool_result(s).",
                len(removed_tool_call_ids),
            )

            # Step 3: Clean up assistant messages — remove malformed tool_calls
            for msg in normalized:
                if msg.role == "assistant" and msg.tool_calls:
                    msg.tool_calls = [
                        tc for tc in msg.tool_calls
                        if tc.get("function") and tc["function"].get("name")
                    ]
                    if not msg.tool_calls:
                        msg.tool_calls = None

            # Step 4: Remove orphaned tool result messages
            normalized = [
                msg for msg in normalized
                if not (msg.role == "tool" and msg.tool_call_id in removed_tool_call_ids)
            ]

        # Step 5: Standard formatting
        return [self._format_message(m, compress_tool_results) for m in normalized]


def build_model_from_slot_config(
    slot_config: Any,
    app_settings: QAAgentSettings | None = None,
) -> Union[OpenAICompatModel, RateLimitedModel]:
    """Build an OpenAICompatModel + RateLimitedModel from a SlotConfig.

    This is the low-level factory used by ModelSlotRegistry.resolve().
    External callers should prefer ``get_model()`` or the registry.

    Args:
        slot_config: A SlotConfig instance with model, base_url, api_key.
        app_settings: Settings for rate limiter config. Uses global if None.

    Returns:
        Configured OpenAICompatModel wrapped with RateLimitedModel.

    Raises:
        LLMNotConfiguredError: when the slot resolves to no API key (D15). The
            API layer maps this to a 409 the frontend understands, instead of
            sending a placeholder key and surfacing a provider-side 401.
    """
    cfg = app_settings or settings

    if not (getattr(slot_config, "api_key", None) or "").strip():
        raise LLMNotConfiguredError()

    inner_model = OpenAICompatModel(
        id=slot_config.model,
        api_key=slot_config.api_key,
        base_url=slot_config.base_url,
        # Rate limit protection — prevents 429 Too Many Requests
        retries=3,                      # Agno-level: retry up to 3 times on model errors
        delay_between_retries=2,        # Base delay 2 seconds between retries
        exponential_backoff=True,       # Exponential: 2s → 4s → 8s
        max_retries=5,                  # httpx-level: retry transport errors + 429
        # httpx 超时:防止上游半开连接/慢响应导致流式请求无限 hang。
        # connect 10s(连接建立),read 180s(单次读取间隙,留足长输出思考时间),
        # write 60s(发送大请求体),pool 10s(连接池获取)。
        # read 超时 → 抛 ReadTimeout → RateLimitedModel 触发 timeout 重试 →
        # 仍失败则抛给 agno 正常结束本轮,而非卡死到外层 asyncio.wait_for(1200s)。
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=60.0, pool=10.0),
    )

    # Wrap with adaptive rate limiter for TPM quota protection
    return RateLimitedModel(inner_model, get_rate_limiter(cfg))


def get_model(config: QAAgentSettings | None = None) -> Union[OpenAICompatModel, RateLimitedModel]:
    """Create an Agno OpenAICompatModel for the DEFAULT slot.

    Backward-compatible entry point. Equivalent to resolving ModelSlot.DEFAULT.

    Args:
        config: Settings override. Uses global ``settings`` if None.

    Returns:
        Configured OpenAICompatModel instance (optionally wrapped with rate limiter).
    """
    from core.model_slots import get_model_slot_registry, ModelSlot
    return get_model_slot_registry().resolve(ModelSlot.DEFAULT)
