"""
Stream Adapter — bridges Agno Agent/Team arun(stream=True) output to a
normalized event dict stream consumed by the WebSocket handler and debug printer.

Compatible with Agno 2.5.14+

Agno Event Types (from agno.run.response)
-----------------------------------------
  RunResponseStartedEvent   — agent run started
  RunResponseContentEvent   — incremental LLM text content
  ToolCallStartedEvent      — tool invocation started (has .tool with ToolExecution)
  ToolCallCompletedEvent    — tool invocation completed (has .tool with result)
  ToolCallErrorEvent        — tool invocation errored
  RunResponseCompletedEvent — agent finished the turn
  RunResponseErrorEvent     — agent raised an unhandled exception
  + others (Reasoning, Compression, Memory, etc.)

Normalized output event schema
------------------------------
Every event is a plain dict with at minimum:
    {"event_type": str, "session_id": str, "agent_name": str, ...payload}

agent_name is always present on every event so clients can attribute events
to a specific agent/worker in multi-agent coordinator sessions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

from core.rate_limiter import _detect_tpm_content_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AbortMessagesCollector — tracks messages during stream for abort persistence
# ---------------------------------------------------------------------------

class AbortMessagesCollector:
    """Collects structured messages from Agno events during streaming.

    When an abort occurs, the collector's ``to_messages()`` method produces
    an Agno-compatible ``Message`` list that can be saved as a ``RunOutput``
    to preserve conversation history for resume.
    """

    def __init__(self) -> None:
        self.run_id: str = ""
        self._messages: list = []           # finalised Message-like dicts
        self._current_tokens: list = []     # token buffer for current assistant turn
        self._pending_tool_calls: Dict[str, dict] = {}  # tool_call_id → {name, args}
        self._has_pending_assistant: bool = False

    # -- Recording methods (called from stream_agent_events) ----------------

    def set_run_id(self, run_id: str) -> None:
        self.run_id = run_id

    def add_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def append_token(self, content: str) -> None:
        self._current_tokens.append(content)
        self._has_pending_assistant = True

    def start_tool_call(self, tool_call_id: str, tool_name: str, tool_args: Any) -> None:
        # Flush any accumulated assistant text + this tool_call as an assistant message
        assistant_content = "".join(self._current_tokens) if self._current_tokens else ""
        self._current_tokens.clear()
        self._has_pending_assistant = False

        # Build tool_call entry (OpenAI-compatible format used by Agno)
        # IMPORTANT: arguments must be valid JSON string, not Python repr.
        # str() produces single-quoted Python repr which breaks JSON parsing.
        import json as _json
        if isinstance(tool_args, str):
            args_str = tool_args
        elif tool_args:
            args_str = _json.dumps(tool_args, ensure_ascii=False)
        else:
            args_str = "{}"
        tc_entry = {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": args_str,
            },
        }

        # If the last message is an assistant with tool_calls in the same model turn,
        # append this tool_call to it. Otherwise create a new assistant message.
        if (self._messages
                and self._messages[-1].get("role") == "assistant"
                and self._messages[-1].get("tool_calls")):
            self._messages[-1]["tool_calls"].append(tc_entry)
        else:
            self._messages.append({
                "role": "assistant",
                "content": assistant_content or None,
                "tool_calls": [tc_entry],
            })

        self._pending_tool_calls[tool_call_id] = {"name": tool_name, "args": tool_args}

    def end_tool_call(self, tool_call_id: str, result: Any) -> None:
        self._pending_tool_calls.pop(tool_call_id, None)
        result_str = str(result) if result is not None else ""
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_str,
        })

    def mark_model_turn(self) -> None:
        """Called on ModelRequestStarted — flush pending assistant text."""
        self._flush_pending_assistant()

    # -- Output methods -----------------------------------------------------

    def _flush_pending_assistant(self) -> None:
        if self._has_pending_assistant and self._current_tokens:
            content = "".join(self._current_tokens)
            self._messages.append({"role": "assistant", "content": content})
            self._current_tokens.clear()
            self._has_pending_assistant = False

    def is_empty(self) -> bool:
        return len(self._messages) == 0 and not self._current_tokens

    def to_messages(self) -> list:
        """Build final Agno Message list for persistence.

        Flushes any pending assistant text and generates ``[interrupted]``
        tool results for tool_calls that started but never completed.
        """
        from agno.models.message import Message

        # Flush any trailing assistant text
        self._flush_pending_assistant()

        # Add [interrupted] results for pending tool_calls
        for tc_id in list(self._pending_tool_calls.keys()):
            self._messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": "[interrupted]",
            })
        self._pending_tool_calls.clear()

        # Convert raw dicts to Agno Message objects
        result: List[Message] = []
        for m in self._messages:
            kwargs: dict = {"role": m["role"]}
            if m.get("content") is not None:
                kwargs["content"] = m["content"]
            if m.get("tool_call_id"):
                kwargs["tool_call_id"] = m["tool_call_id"]
            if m.get("tool_calls"):
                kwargs["tool_calls"] = m["tool_calls"]
            result.append(Message(**kwargs))

        return result

# Import Agno event types for isinstance checks (more reliable than string matching)
# Agno 2.5.14: events are in agno.run.response module
try:
    from agno.run.response import (
        ToolCallStartedEvent,
        ToolCallCompletedEvent,
        ToolCallErrorEvent,
        RunResponseContentEvent as RunContentEvent,
        RunResponseCompletedEvent as RunCompletedEvent,
        RunResponseErrorEvent as RunErrorEvent,
        RunResponseStartedEvent as RunStartedEvent,
        CompressionStartedEvent as _CS1,
        CompressionCompletedEvent as _CC1,
        RunPausedEvent,
    )
    CompressionStartedEvent = _CS1  # type: ignore
    CompressionCompletedEvent = _CC1  # type: ignore
    _AGNO_EVENTS_AVAILABLE = True
except ImportError:
    # Fallback: try old import path
    try:
        from agno.run.agent import (
            ToolCallStartedEvent,
            ToolCallCompletedEvent,
            ToolCallErrorEvent,
            RunContentEvent,
            RunCompletedEvent,
            RunErrorEvent,
            RunStartedEvent,
            CompressionStartedEvent,
            CompressionCompletedEvent,
            RunPausedEvent,
        )
        _AGNO_EVENTS_AVAILABLE = True
    except ImportError:
        RunPausedEvent = None  # type: ignore[assignment]
        _AGNO_EVENTS_AVAILABLE = False
        logger.warning("Could not import Agno event types — falling back to string-based classification")


# ---------------------------------------------------------------------------
# Normalized event builders
# ---------------------------------------------------------------------------

def _token_event(session_id: str, agent_name: str, content: str, model: str = "") -> dict:
    return {"event_type": "token", "session_id": session_id, "agent_name": agent_name, "content": content, "model": model}


def _reasoning_delta_event(session_id: str, agent_name: str, content: str, model: str = "") -> dict:
    """思考增量事件。

    模型原生 thinking（deepseek 系经 provider 以 ``reasoning_content`` 增量
    下发）的逐 delta 透传，与 ``token`` 同构；仅用于前端思考折叠块渲染，
    MUST NOT 进入 assistant 消息内容（恢复历史/回传协议均不含思考）。
    """
    return {
        "event_type": "reasoning_delta",
        "session_id": session_id,
        "agent_name": agent_name,
        "content": content,
        "model": model,
    }


def _tool_start_event(session_id: str, agent_name: str, tool_name: str, tool_call_id: str, inputs: dict) -> dict:
    return {
        "event_type": "tool_start",
        "session_id": session_id,
        "agent_name": agent_name,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "inputs": inputs,
    }


def _tool_end_event(session_id: str, agent_name: str, tool_name: str, tool_call_id: str,
                     outputs: Any, elapsed_ms: float) -> dict:
    return {
        "event_type": "tool_end",
        "session_id": session_id,
        "agent_name": agent_name,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "outputs": outputs,
        "elapsed_ms": elapsed_ms,
    }


def _tool_error_event(session_id: str, agent_name: str, tool_name: str, tool_call_id: str, error: str) -> dict:
    return {
        "event_type": "tool_error",
        "session_id": session_id,
        "agent_name": agent_name,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "error": error,
    }


def _extract_usage(metrics: Any) -> dict | None:
    """Convert an Agno Metrics object to the normalized usage payload.

    Returns None when the metrics object carries no token counts at all
    (e.g. the upstream gateway omitted the usage block) — callers treat None as
    "usage unavailable" and the frontend shows "—".
    """
    try:
        input_tokens = getattr(metrics, "input_tokens", 0) or 0
        output_tokens = getattr(metrics, "output_tokens", 0) or 0
        total_tokens = getattr(metrics, "total_tokens", 0) or 0
        if not (input_tokens or output_tokens or total_tokens):
            return None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_tokens": getattr(metrics, "cache_read_tokens", None),
            "cache_write_tokens": getattr(metrics, "cache_write_tokens", None),
            "reasoning_tokens": getattr(metrics, "reasoning_tokens", None),
            "cost": getattr(metrics, "cost", None),
            "duration_s": getattr(metrics, "duration", None),
        }
    except Exception:
        logger.debug("Failed to extract usage metrics", exc_info=True)
        return None


def _run_complete_event(
    session_id: str,
    agent_name: str,
    final_response: str,
    usage: dict | None = None,
) -> dict:
    """RunCompleted WS event.

    ``final_response`` is the full assistant token stream accumulated across the
    run.

    ``usage`` carries the run's token consumption extracted from Agno
    ``RunCompletedEvent.metrics`` (see ``_extract_usage``). None when metrics
    are unavailable — backward-compatible optional field, old consumers
    ignore it.
    """
    return {
        "event_type": "run_complete",
        "session_id": session_id,
        "agent_name": agent_name,
        "final_response": final_response,
        "usage": usage,
    }


def _run_error_event(session_id: str, agent_name: str, error: str) -> dict:
    return {"event_type": "run_error", "session_id": session_id, "agent_name": agent_name, "error": error}


def _run_started_event(session_id: str, agent_name: str) -> dict:
    return {"event_type": "run_started", "session_id": session_id, "agent_name": agent_name}


def _approval_pending_event(session_id: str, agent_name: str, run_id: str, tool_exec: Any) -> dict:
    """RunPausedEvent → 审批卡事件（审批卡 schema）。

    tools[0] 携带 approval_id（resolve 凭据）/ tool_name / tool_args（审批载荷
    kind/title/content_md/purpose/expect）——前端审批卡完整评审信息源。
    """
    return {
        "event_type": "approval_pending",
        "session_id": session_id,
        "agent_name": agent_name,
        "run_id": run_id,
        "approval_id": getattr(tool_exec, "approval_id", None),
        "tool_name": getattr(tool_exec, "tool_name", None),
        "tool_args": getattr(tool_exec, "tool_args", None),
        "approval_type": getattr(tool_exec, "approval_type", None),
    }


def _user_message_event(session_id: str, agent_name: str, content: str) -> dict:
    return {"event_type": "user_message", "session_id": session_id, "agent_name": agent_name, "content": content}


def _serialize_user_feedback_questions(schema: Any) -> List[Dict[str, Any]]:
    """UserFeedbackQuestion list → JSON 安全载荷（澄清卡选项渲染源）。"""
    questions: List[Dict[str, Any]] = []
    for q in schema or []:
        questions.append({
            "question": getattr(q, "question", ""),
            "header": getattr(q, "header", None),
            "multi_select": bool(getattr(q, "multi_select", False)),
            "options": [
                {"label": getattr(opt, "label", ""), "description": getattr(opt, "description", None)}
                for opt in (getattr(q, "options", None) or [])
            ],
        })
    return questions


def _serialize_user_input_fields(schema: Any) -> List[Dict[str, Any]]:
    """UserInputField list → JSON 安全载荷（澄清卡表单渲染源）。"""
    fields: List[Dict[str, Any]] = []
    for f in schema or []:
        ftype = getattr(f, "field_type", None)
        type_name = ftype if isinstance(ftype, str) else getattr(ftype, "__name__", "str")
        fields.append({
            "name": getattr(f, "name", ""),
            "field_type": type_name,
            "description": getattr(f, "description", None),
            "value": getattr(f, "value", None),
        })
    return fields


def serialize_clarification_payload(tool_exec: Any) -> Dict[str, Any]:
    """澄清工具载荷 → JSON 安全 dict（事件层与自愈端点共用，单一口径）。

    澄清卡无 DB 权威记录，schema 全量随载荷走。
    """
    is_feedback = bool(getattr(tool_exec, "user_feedback_schema", None))
    return {
        "tool_call_id": getattr(tool_exec, "tool_call_id", None),
        "tool_name": getattr(tool_exec, "tool_name", None),
        "kind": "feedback" if is_feedback else "form",
        "questions": _serialize_user_feedback_questions(getattr(tool_exec, "user_feedback_schema", None)),
        "fields": _serialize_user_input_fields(getattr(tool_exec, "user_input_schema", None)),
        "tool_args": getattr(tool_exec, "tool_args", None),
    }


def _clarification_request_event(session_id: str, agent_name: str, run_id: str, tool_exec: Any) -> dict:
    """RunPausedEvent（无 approval_type 的 HITL 暂停）→ 澄清卡事件。

    澄清门（agno 内置 ask_user / get_user_input）不走 approvals 审批链（无
    approval_type → 无 approval_id、无落库记录）→ 前端 key = run_id + tool_call_id；
    无权威表，schema 全量进 payload —— 刷新回放的唯一数据源。
    """
    ev = {
        "event_type": "clarification_request",
        "session_id": session_id,
        "agent_name": agent_name,
        "run_id": run_id,
    }
    ev.update(serialize_clarification_payload(tool_exec))
    return ev


def _paused_tool_count(tools: Any) -> int:
    """统计本轮真正处于暂停态的工具数（run.tools 是整轮累积列表，须按 HITL 标记过滤）。"""
    return sum(
        1 for t in (tools or [])
        if getattr(t, "requires_confirmation", None)
        or getattr(t, "requires_user_input", None)
        or getattr(t, "external_execution_required", None)
    )


def _generic_event(session_id: str, agent_name: str, event_name: str, content: Any = None) -> dict:
    return {"event_type": event_name, "session_id": session_id, "agent_name": agent_name, "content": str(content) if content else ""}


def _compression_event(
    session_id: str,
    agent_name: str,
    *,
    stage: str,
    tool_results_compressed: int | None = None,
    original_size: int | None = None,
    compressed_size: int | None = None,
) -> dict:
    """Normalized Agno CompressionManager event.

    Preserves the quantitative fields (``tool_results_compressed``,
    ``original_size``, ``compressed_size``) that Agno's
    ``CompressionCompletedEvent`` carries — without this builder the
    generic-event fallback stringifies ``content`` (which is None for
    compression events) and loses all evidence that compression fired.

    Args:
        stage: ``"started"`` or ``"completed"`` — maps to Agno's
            ``CompressionStartedEvent`` / ``CompressionCompletedEvent``.
        tool_results_compressed: Count of tool results collapsed.
        original_size: Pre-compression token (or char) count.
        compressed_size: Post-compression token (or char) count.
    """
    return {
        "event_type": "compression",
        "session_id": session_id,
        "agent_name": agent_name,
        "stage": stage,
        "tool_results_compressed": tool_results_compressed,
        "original_size": original_size,
        "compressed_size": compressed_size,
    }


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------

async def stream_agent_events(
    agent: Any,
    message: str,
    session_id: str,
    *,
    stream: bool = True,
    user_id: Optional[str] = None,
    session_extra: Optional[dict] = None,
) -> AsyncIterator[dict]:
    """
    Run `agent` with `message` and yield normalized event dicts.

    Every yielded event includes an ``agent_name`` field so clients can
    attribute events to a specific agent/worker in multi-agent sessions.

    When stream=True, uses agent.arun(stream=True) which returns an async iterable
    of Agno RunOutputEvent dataclass instances (token-by-token).
    When stream=False, uses agent.arun(stream=False) which returns a complete
    RunResponse at once (no incremental token events).

    Abort (user stop) is handled at the task level in _run_agent_task via
    asyncio.Task.cancel().  This coroutine catches CancelledError, saves the
    session, and yields a run_aborted event before re-raising.
    """
    # Resolve agent_name once at the call site — used as default for all events.
    # RunStartedEvent may carry its own agent_name which takes precedence.
    agent_name: str = getattr(agent, "name", "") or ""

    # Collector for abort-time persistence: tracks messages in parallel with streaming
    collector = AbortMessagesCollector()

    # Yield user message event first so it gets persisted to EventStore
    # before the agent starts processing. This ensures the user's input
    # is recoverable after page refresh or crash.
    yield _user_message_event(session_id, agent_name, message)
    collector.add_user_message(message)

    # Agent.arun with stream=True + stream_events=True returns an async generator
    # that yields ALL event types (tokens, tool calls, run lifecycle, etc.)
    # Without stream_events=True, only token content events are yielded.
    arun_kwargs: dict = {"stream": stream, "stream_events": True, "session_id": session_id}
    if user_id:
        arun_kwargs["user_id"] = user_id
    if session_extra:
        arun_kwargs["session_state"] = {"_session_extra_data": session_extra}
    response_gen = agent.arun(message, **arun_kwargs)

    # If the return is a coroutine (non-streaming path), await it first
    if asyncio.iscoroutine(response_gen):
        response_gen = await response_gen

    async for normalized in _drive_agent_stream(
        agent, response_gen, session_id, agent_name=agent_name, collector=collector,
    ):
        yield normalized


async def stream_continue_agent_events(
    agent: Any,
    run_id: str,
    session_id: str,
    *,
    run_info: Optional[dict] = None,
    stream: bool = True,
    user_id: Optional[str] = None,
    session_extra: Optional[dict] = None,
    close_pending_hitl: bool = False,
) -> AsyncIterator[dict]:
    """Continue a previous run (``acontinue_run``) and yield normalized event dicts.

    轮次重答原语：与 ``stream_agent_events`` 同源
    事件归一化，差异仅四处：

    1. 不 yield ``user_message`` 事件、不喂 collector user 消息——该轮 U 已在
       存储（截断保留至 U），重答 MUST NOT 重复渲染/落库（spec 不变量）；
    2. 原语为 ``acontinue_run(run_id=…, continue_from="last_user", input=None)``：
       completed 边界 run 自动 fork 新兄弟 run（新 run_id），aborted run（本仓
       落库 status=running）原地续跑——不追加用户消息（``input=None``）；
    3. ``run_info["run_id"]`` 透传实际执行的 run_id（fork 路径 ≠ 入参 run_id），
       供任务层在成功后 drop 原边界 run 去重（storage_writer.drop_session_run）。
    4. ``close_pending_hitl``：边界 run 停在 HITL
       暂停态时先终结被丢弃轮残留的挂起门，并经 ``updated_tools`` 回传——修好
       agno 的 run.tools / requirements 双对象分裂。否则 Case 3a 会向 LLM 注入
       「用户作答全 null」的伪造工具结果，模型循环也会因残留未 resolved
       requirement 在首个工具批次后 break（2026-09-17 真机空内容收尾事故）。
       默认 False：非重答调用方行为不变；清理失败 fail-open。

    ``session_extra``（real_user_id 等 tool hook 依赖）经 ``run_context`` 注入
    ——acontinue_run 无 session_state 入参（agno 2.6.22）。
    """
    agent_name: str = getattr(agent, "name", "") or ""

    collector = AbortMessagesCollector()

    continue_kwargs: dict = {
        "run_id": run_id,
        "session_id": session_id,
        "stream": stream,
        "stream_events": True,
        "continue_from": "last_user",
    }
    if user_id:
        continue_kwargs["user_id"] = user_id
    if session_extra:
        run_ctx = await _build_extra_run_context(
            agent, run_id, session_id, user_id, session_extra,
        )
        if run_ctx is not None:
            continue_kwargs["run_context"] = run_ctx

    # 被丢弃轮的 HITL 挂起态清理：边界 run 停在
    # 暂停态时，挂起门 MUST 被终结而非以 null 作答续跑。清理后的 tools 经
    # updated_tools 回传，借 agno _sync_requirements_with_tools 修好
    # run.tools / requirements 双对象分裂。fail-open：取不到 run 即按现状续跑。
    if close_pending_hitl:
        boundary_run = await _load_boundary_run(agent, run_id, session_id)
        if boundary_run is not None:
            cleaned_tools = _close_pending_hitl_gates(boundary_run)
            if cleaned_tools is not None:
                continue_kwargs["updated_tools"] = cleaned_tools
                await _cancel_discarded_approvals(agent, boundary_run)
                logger.info(
                    "stream_continue_agent_events: pending HITL gates closed, passing "
                    "%d tool execution(s) as updated_tools (session=%s run=%s)",
                    len(cleaned_tools), session_id, run_id,
                )

    response_gen = agent.acontinue_run(**continue_kwargs)

    # If the return is a coroutine (non-streaming path), await it first
    if asyncio.iscoroutine(response_gen):
        response_gen = await response_gen

    async for normalized in _drive_agent_stream(
        agent, response_gen, session_id, agent_name=agent_name, collector=collector,
        run_info=run_info,
    ):
        yield normalized


async def _build_extra_run_context(
    agent: Any,
    run_id: str,
    session_id: str,
    user_id: Optional[str],
    session_extra: dict,
) -> Any:
    """为 acontinue_run 构建带 ``_session_extra_data`` 的 RunContext（fail-open）。

    agno 2.6.22 的 acontinue_run 无 ``session_state`` 入参，且 dispatch 以
    ``load_session_state(..., session_state={})`` 装载（DB 态胜出，agent 实例态
    被丢弃）—— ``real_user_id`` 等 tool hook 依赖只能经调用方构造的
    ``run_context`` 注入：DB session_state 浅拷 + 覆写 _session_extra_data。

    构建失败返回 None（fail-open，回退无参续跑，仅丢失 hook 身份注入）。
    """
    try:
        from agno.db.base import SessionType
        from agno.run.context import RunContext

        from core.storage import get_storage

        storage = get_storage()
        get_fn = getattr(storage, "get_session", None)
        if get_fn is None:
            return None

        import inspect

        if inspect.iscoroutinefunction(get_fn):
            sess = await get_fn(session_id, session_type=SessionType.AGENT)
        else:
            # 同步 pymongo 直调会阻塞 event loop
            sess = await asyncio.to_thread(
                get_fn, session_id, session_type=SessionType.AGENT,
            )

        db_state: dict = {}
        if sess is not None:
            data = getattr(sess, "session_data", None) or {}
            if isinstance(data, dict):
                state = data.get("session_state")
                if isinstance(state, dict):
                    db_state = dict(state)
        db_state["_session_extra_data"] = session_extra
        return RunContext(
            run_id=run_id,
            session_id=session_id,
            user_id=user_id,
            session_state=db_state,
        )
    except Exception:
        logger.warning(
            "_build_extra_run_context failed (session=%s) — fallback to no-arg continue",
            session_id,
            exc_info=True,
        )
        return None


# ── Section: 被丢弃轮 HITL 挂起态清理 ────────────────────────────────
# 被丢弃轮的 HITL 挂起态终结（不作答续跑）/ 挂起态清理 fail-open。


async def _load_boundary_run(agent: Any, run_id: str, session_id: str) -> Any | None:
    """取回边界 run（含 tools/requirements）；fail-open（取不到返回 None）。

    重答的边界 run 若停在 HITL 暂停态，存储截断只裁 ``run.messages``，tools /
    requirements 原样残留（agno 自己的 ``_truncate_run_to_checkpoint`` 因
    ``message_index >= len(messages)`` early return）—— 清理需要这份 run。
    """
    try:
        get_run = getattr(agent, "aget_run_output", None)
        if get_run is None:
            logger.warning(
                "_load_boundary_run: agent has no aget_run_output (session=%s) — "
                "pending HITL gates left as-is",
                session_id,
            )
            return None
        return await get_run(run_id, session_id=session_id)
    except Exception:
        logger.warning(
            "_load_boundary_run failed (session=%s run=%s) — pending HITL gates left as-is",
            session_id,
            run_id,
            exc_info=True,
        )
        return None


def _close_pending_hitl_gates(run: Any) -> list | None:
    """终结被丢弃轮的 HITL 挂起门；返回清理后的完整 tools（无挂起项 → None）。

    **作废语义**（design D2）：只清标记、MUST NOT 回填任何答案——该轮消息已被截断，
    挂起门不再可达；回填 null 会让模型把「用户作答全是 null」当真（本变更修的病根）。

    清完这批量 MUST 作为 ``updated_tools`` 回传 ``acontinue_run``：agno 才会经
    ``_sync_requirements_with_tools`` 把 ``requirements[].tool_execution`` 重指到
    同一批对象（``run.tools`` 与 ``requirements`` 是两份对象，只改一份则
    ``is_resolved()`` 恒 False → ``agno/models/base.py`` 模型循环 break）。
    """
    tools = list(getattr(run, "tools", None) or [])

    # requirements 侧引用若不在 tools 里（双对象分裂 / 老数据），必须补进回传列表并
    # 同样清标记：agno 的 sync 按 tool_call_id 匹配，漏一条即该 requirement 仍
    # 未 resolved → 模型循环照旧 break
    covered = {getattr(t, "tool_call_id", None) for t in tools}
    for req in getattr(run, "requirements", None) or []:
        tool_exec = getattr(req, "tool_execution", None)
        tool_call_id = getattr(tool_exec, "tool_call_id", None)
        if tool_exec is not None and tool_call_id and tool_call_id not in covered:
            tools.append(tool_exec)
            covered.add(tool_call_id)

    cleared = 0
    for tool in tools:
        if getattr(tool, "requires_user_input", None):
            tool.requires_user_input = False
            tool.answered = True
            cleared += 1
        if getattr(tool, "requires_confirmation", None):
            tool.requires_confirmation = False
            tool.confirmed = False
            cleared += 1
        if getattr(tool, "external_execution_required", None):
            tool.external_execution_required = False
            cleared += 1
    if cleared == 0:
        return None
    return tools


async def _cancel_discarded_approvals(agent: Any, run: Any) -> None:
    """把被丢弃轮残留的 ``pending`` 审批置为 cancelled（fail-open）。

    不取消则前端 ``refreshApprovals``（``GET /approvals?session_id=…`` 按 session
    过滤 status=pending）会复活一张已无法续跑的审批卡。``expected_status="pending"``
    保证不覆盖已 resolve 的历史审批。
    """
    db = getattr(agent, "db", None)
    update = getattr(db, "update_approval", None)
    if update is None:
        return

    def _cancel(approval_id: str) -> Any:
        return update(
            approval_id,
            expected_status="pending",
            status="cancelled",
            note="轮次已重答，该审批作废",
        )

    approval_ids = [
        approval_id
        for approval_id in (
            getattr(t, "approval_id", None) for t in (getattr(run, "tools", None) or [])
        )
        if approval_id
    ]

    if not approval_ids:
        # approval_id 缺失兜底：按 run_id 反查 pending 审批
        get_approvals = getattr(db, "get_approvals", None)
        if get_approvals is None:
            return
        try:
            listed = get_approvals(
                run_id=getattr(run, "run_id", None),
                approval_type="required",
                status="pending",
            )
            if asyncio.iscoroutine(listed):
                listed = await listed
        except Exception:
            logger.warning(
                "_cancel_discarded_approvals: list failed (run=%s)",
                getattr(run, "run_id", None),
                exc_info=True,
            )
            return
        rows = listed[0] if isinstance(listed, tuple) else listed
        approval_ids = [
            row.get("id") for row in (rows or []) if isinstance(row, dict) and row.get("id")
        ]

    for approval_id in approval_ids:
        try:
            result = _cancel(approval_id)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.warning(
                "_cancel_discarded_approvals: cancel %s failed — approval card may "
                "resurface until manually resolved",
                approval_id,
                exc_info=True,
            )


async def _drive_agent_stream(
    agent: Any,
    response_gen: Any,
    session_id: str,
    *,
    agent_name: str,
    collector: AbortMessagesCollector,
    run_info: Optional[dict] = None,
) -> AsyncIterator[dict]:
    """arun / acontinue_run 共用的事件驱动层。

    职责：事件归一化（``_process_event``）、abort 落库
    （``_save_agent_session_on_abort``）、终态收口（run_complete / approval
    / TPM 兜底）。调用方负责构造响应原语（arun / acontinue_run）与
    user_message 事件的取舍。

    ``run_info`` 非空时回填实际执行的 run_id（acontinue fork 路径 ≠ 原
    run_id；首个携带者生效，空值不覆盖）。
    """
    final_response = ""
    last_usage: dict | None = None

    # 审批暂停标记：RunPaused（approval_pending / clarification_request）即本轮
    # 终态，此后不再发 run_complete — 否则前端渲染「🏁 执行完成」分隔条误导
    # 用户（实际是等待人工介入，2026-08-31 真机：用户误以为执行已完成）。
    approval_paused = False
    active_tools: dict[str, float] = {}  # tool_call_id → start_ts

    try:
        # Direct async iteration — no asyncio.ensure_future() wrapping.
        # This keeps all OpenTelemetry spans (agent, model, tool) within
        # the same asyncio Task, preserving contextvars propagation.
        async for event in _iter_response(response_gen):
            # ── Capture actual run_id (acontinue fork 路径新 run_id) ──
            if run_info is not None and not run_info.get("run_id"):
                _rid = getattr(event, "run_id", None)
                if _rid:
                    run_info["run_id"] = _rid

            # ── Capture run usage metrics ──────────────────────────
            # RunCompletedEvent（流式）或裸 RunOutput（非流式）都带 .metrics；
            # 捕获后附加到循环结束时的 run_complete 事件。防御性 getattr：
            # provider 不下发 usage 时 metrics 为空对象/None，_extract_usage 返回 None。
            _m = getattr(event, "metrics", None)
            if _m is not None:
                _u = _extract_usage(_m)
                if _u is not None:
                    last_usage = _u

            # ── Process event ──────────────────────────────────────
            # 一帧可产出多个归一化事件（RunContent 同帧 token + reasoning_delta）
            for normalized in _process_event(event, session_id, agent_name, active_tools):
                # ── Feed collector for abort-time persistence ──────
                _feed_collector(collector, event, normalized)

                # Accumulate final response from token events
                if normalized["event_type"] == "token":
                    final_response += normalized.get("content", "")

                # 审批暂停即终态：标记后跳过结尾的 run_complete（见 approval_paused 注释）
                if normalized["event_type"] in ("approval_pending", "clarification_request"):
                    approval_paused = True

                yield normalized

        # ── TPM 内容错误兜底检测 ────────────────────────────────────
        # 即使 RateLimitedModel 内容层检测漏检(如 provider 换新错误格式绕过特征),
        # 此处对累积的 final_response 做最终检测,命中则 raise RuntimeError
        # (message 含用户友好文案),让外层 except Exception 统一 yield run_error
        # + 让 _run_agent_task 走 set_failed 而非 set_completed,避免会话静默标
        # completed 把错误文本当成功消息展示给用户。复用 rate_limiter 的检测函数
        # (单点定义,零漂移)。
        if _detect_tpm_content_error(final_response):
            logger.warning(
                "[TPM-Content-Fallback] session=%s final_response 疑似上游限流错误文本, "
                "改发 run_error 而非 run_complete。原文摘要: %s",
                session_id, final_response[:200],
            )
            # 不在此 yield run_error — 外层 except Exception 会统一 yield,
            # 避免双 run_error。message 直接写用户友好文案供外层 yield。
            raise RuntimeError(
                "LLM 响应内容疑似上游限流错误,请稍后重试。原文摘要: "
                + final_response[:200]
            )
        # 审批/澄清暂停的 run 不发 run_complete：暂停事件已是终态，前端
        # （sessions store）据此渲染对应卡片并把会话置 completed。再发
        # run_complete 会渲染「🏁 执行完成」分隔条，误导用户以为执行已完成
        # （实际在等审批/澄清作答）。
        if approval_paused:
            logger.info(
                "stream_agent_events: run paused on approval (session=%s) — "
                "skip run_complete",
                session_id,
            )
            return
        yield _run_complete_event(session_id, agent_name, final_response, usage=last_usage)

    except asyncio.CancelledError:
        # Abort triggered by _run_agent_task's watcher via Task.cancel().
        # Save session BEFORE the generator's GeneratorExit skips Agno's
        # acleanup_and_store().  Use asyncio.shield() so the save itself
        # is not cancelled mid-operation.
        await asyncio.shield(_save_agent_session_on_abort(agent, session_id, collector))

        # Fire-and-forget checkpoint for resume capability
        asyncio.create_task(
            _write_abort_checkpoint(session_id),
            name=f"abort_checkpoint_{session_id}",
        )

        logger.info(
            "stream_agent_events: cancelled by abort watcher, "
            "session saved (session=%s)", session_id,
        )
        yield {
            "event_type": "run_aborted",
            "session_id": session_id,
            "agent_name": agent_name,
            "resumable": True,
            "partial_response": final_response,
        }
        # Re-raise so _run_agent_task can set session state
        raise

    except Exception as exc:
        logger.exception("stream_agent_events error (session=%s)", session_id)
        yield _run_error_event(session_id, agent_name, str(exc))
        raise


def _feed_collector(collector: AbortMessagesCollector, raw_event: Any, normalized: dict | None) -> None:
    """Feed an Agno event into the AbortMessagesCollector.

    Uses the raw Agno event objects (isinstance checks) for structured data
    like tool_calls, and the normalized dict for simpler events.
    """
    try:
        if _AGNO_EVENTS_AVAILABLE:
            if isinstance(raw_event, RunStartedEvent):
                run_id = getattr(raw_event, "run_id", "") or ""
                collector.set_run_id(run_id)
                return

            if isinstance(raw_event, ToolCallStartedEvent):
                tool_exec = raw_event.tool
                if tool_exec:
                    collector.start_tool_call(
                        tool_call_id=tool_exec.tool_call_id or "",
                        tool_name=tool_exec.tool_name or "unknown",
                        tool_args=tool_exec.tool_args or {},
                    )
                return

            if isinstance(raw_event, ToolCallCompletedEvent):
                tool_exec = raw_event.tool
                if tool_exec:
                    collector.end_tool_call(
                        tool_call_id=tool_exec.tool_call_id or "",
                        result=tool_exec.result,
                    )
                return

        # Token events — use normalized dict
        if normalized and normalized.get("event_type") == "token":
            content = normalized.get("content", "")
            if content:
                collector.append_token(content)
            return

        # ModelRequestStarted — detect via event string attr
        event_str = getattr(raw_event, "event", "")
        if event_str == "ModelRequestStarted":
            collector.mark_model_turn()

    except Exception:
        # Never let collector errors break the stream
        logger.debug("_feed_collector error", exc_info=True)


def feed_collector_from_event(
    collector: AbortMessagesCollector,
    event: Any,
    session_id: str,
    agent_name: str,
    active_tools: dict,
) -> None:
    """Public helper: classify one Agno event and feed it into the collector.

    Reused by ``api/server.py`` (WS path) and ``api/agent_os_adapter.py``
    (SSE path) so both build identical partial-message state for resume
    persistence.

    Args:
        collector: The AbortMessagesCollector accumulating this stream's msgs.
        event: A raw Agno event yielded by ``agent.arun(stream=True,
            stream_events=True)``.
        session_id / agent_name: Context for event normalisation.
        active_tools: Caller-owned dict (``tool_call_id → start_ts``) that
            MUST persist across all events of a single stream. Passed in so
            tool-call timing state survives across calls; the collector keys
            on ``tool_call_id`` and does not itself need timing.
    """
    for normalized in _process_event(event, session_id, agent_name, active_tools):
        _feed_collector(collector, event, normalized)


async def _save_agent_session_on_abort(
    agent: Any, session_id: str, collector: AbortMessagesCollector
) -> None:
    """Best-effort: persist the Agno agent session when aborting.

    When ``response_gen.aclose()`` is called on the Agno async generator,
    Python throws ``GeneratorExit`` (a ``BaseException``, NOT ``Exception``).
    This bypasses all ``except Exception`` branches inside Agno's
    ``_arun_stream``, including ``acleanup_and_store()`` which is responsible
    for ``session.upsert_run()`` + ``asave_session()``.

    We use the ``AbortMessagesCollector`` (which has been tracking messages
    from the Agno event stream) to build a ``RunOutput`` and persist it via
    ``session.upsert_run()`` + ``db.upsert_session()``.
    """
    try:
        db = getattr(agent, "db", None)
        if db is None:
            logger.debug("_save_agent_session_on_abort: agent has no db, skipping (session=%s)", session_id)
            return

        if collector.is_empty():
            logger.info("_save_agent_session_on_abort: no messages to save (session=%s)", session_id)
            return

        # 1. Get the session object — prefer cached, fallback to DB read, then create new
        session_obj = getattr(agent, "_cached_session", None)

        if session_obj is None:
            logger.debug("_save_agent_session_on_abort: _cached_session is None, trying DB read (session=%s)", session_id)
            try:
                get_fn = getattr(db, "get_session", None)
                if get_fn:
                    import inspect as _inspect
                    if _inspect.iscoroutinefunction(get_fn):
                        session_obj = await get_fn(session_id=session_id)
                    else:
                        session_obj = get_fn(session_id=session_id)
            except Exception:
                logger.debug("_save_agent_session_on_abort: DB read failed, will create new session", exc_info=True)

        if session_obj is None:
            from agno.session.agent import AgentSession
            session_obj = AgentSession(
                session_id=session_id,
                agent_id=getattr(agent, "id", None),
                user_id=getattr(agent, "user_id", None),
            )
            logger.debug("_save_agent_session_on_abort: created new AgentSession (session=%s)", session_id)

        # 2. Build RunOutput from collector
        from agno.run.agent import RunOutput
        messages = collector.to_messages()
        run = RunOutput(
            run_id=collector.run_id or str(uuid4()),
            session_id=session_id,
            agent_id=getattr(agent, "id", None),
            agent_name=getattr(agent, "name", None),
            messages=messages,
            content="".join(
                m.content for m in messages
                if m.role == "assistant" and m.content
            ),
        )

        # 3. Upsert run into session (same as acleanup_and_store does)
        session_obj.upsert_run(run=run)

        # 4. Persist to DB
        import inspect
        upsert_fn = getattr(db, "upsert_session", None)
        if upsert_fn is None:
            logger.warning("_save_agent_session_on_abort: db has no upsert_session method (session=%s)", session_id)
            return

        if inspect.iscoroutinefunction(upsert_fn):
            await upsert_fn(session=session_obj)
        else:
            upsert_fn(session=session_obj)

        msg_count = len(messages)
        logger.info(
            "_save_agent_session_on_abort: saved %d messages to session (session=%s, run=%s)",
            msg_count, session_id, run.run_id,
        )

    except Exception:
        logger.warning("_save_agent_session_on_abort: failed (session=%s)", session_id, exc_info=True)


async def _write_abort_checkpoint(session_id: str) -> None:
    """Best-effort: write an INTERRUPTED checkpoint when the stream is aborted."""
    try:
        from datetime import datetime
        from db.models import CheckpointStatus, QaPhaseCheckpoint

        # Find the latest RUNNING checkpoint for this session
        checkpoint = await QaPhaseCheckpoint.find_latest(session_id)
        if checkpoint and checkpoint.status == CheckpointStatus.RUNNING:
            checkpoint.status = CheckpointStatus.INTERRUPTED
            checkpoint.updated_at = datetime.utcnow()
            await checkpoint.save()
            logger.info("_write_abort_checkpoint: checkpoint updated (session=%s)", session_id)
    except Exception:
        logger.warning("_write_abort_checkpoint failed (session=%s)", session_id, exc_info=True)


def _process_event(event: Any, session_id: str, agent_name: str, active_tools: dict) -> list[dict]:
    """Convert an Agno event to a list of normalized dicts (empty = skip).

    列表形态：一个 RunContentEvent 帧可同时
    携带 content 与 reasoning_content 增量，分流后同帧产出 token +
    reasoning_delta 两个事件。
    """

    if _AGNO_EVENTS_AVAILABLE:
        return _process_event_typed(event, session_id, agent_name, active_tools)

    # Fallback: string-based classification
    return _process_event_string(event, session_id, agent_name, active_tools)


def _process_event_typed(event: Any, session_id: str, agent_name: str, active_tools: dict) -> list[dict]:
    """Process event using isinstance checks (preferred)."""

    if isinstance(event, RunContentEvent):
        events: list[dict] = []
        content = event.content
        if content and isinstance(content, str):
            events.append(_token_event(session_id, agent_name, content))
        # 防御性读取：agno 版本漂移缺字段时静默走原 token 路径（design D1）
        reasoning = getattr(event, "reasoning_content", None)
        if reasoning and isinstance(reasoning, str):
            events.append(_reasoning_delta_event(session_id, agent_name, reasoning))
        return events

    elif isinstance(event, ToolCallStartedEvent):
        tool_exec = event.tool
        if tool_exec is None:
            return []
        tool_name = tool_exec.tool_name or "unknown_tool"
        call_id = tool_exec.tool_call_id or ""
        inputs = tool_exec.tool_args or {}
        active_tools[call_id] = time.monotonic()
        return [_tool_start_event(session_id, agent_name, tool_name, call_id, inputs)]

    elif isinstance(event, ToolCallCompletedEvent):
        tool_exec = event.tool
        if tool_exec is None:
            return []
        tool_name = tool_exec.tool_name or "unknown_tool"
        call_id = tool_exec.tool_call_id or ""
        result = tool_exec.result
        start_ts = active_tools.pop(call_id, time.monotonic())
        elapsed_ms = (time.monotonic() - start_ts) * 1000
        return [_tool_end_event(session_id, agent_name, tool_name, call_id, result, elapsed_ms)]

    elif isinstance(event, ToolCallErrorEvent):
        tool_exec = event.tool
        tool_name = (tool_exec.tool_name if tool_exec else "unknown_tool") or "unknown_tool"
        call_id = (tool_exec.tool_call_id if tool_exec else "") or ""
        error_msg = str(event.content) if event.content else "unknown error"
        active_tools.pop(call_id, None)
        return [_tool_error_event(session_id, agent_name, tool_name, call_id, error_msg)]

    elif isinstance(event, RunCompletedEvent):
        # RunCompleted carries the full final content as a summary, but all content
        # has already been yielded token-by-token via RunContentEvent during streaming.
        # Re-yielding here would cause every output line to appear twice in the log.
        return []

    elif isinstance(event, RunErrorEvent):
        error = str(event.content) if event.content else "Agent run error"
        return [_run_error_event(session_id, agent_name, error)]

    elif isinstance(event, RunStartedEvent):
        # RunStartedEvent may carry its own agent_name (takes precedence)
        event_agent_name = getattr(event, "agent_name", "") or agent_name
        return [_run_started_event(session_id, event_agent_name)]

    elif RunPausedEvent is not None and isinstance(event, RunPausedEvent):
        # 暂停时 run.tools 是整轮工具调用累积列表；按 HITL 标记分流：
        # 带 approval_type/approval_id → 审批卡（既有链路）；带澄清 schema → 澄清卡。
        paused_tools = getattr(event, "tools", None) or []
        n_paused = _paused_tool_count(paused_tools)
        if n_paused >= 2:
            # agno _response.py 仅为最后一个 paused 工具建 requirement —— 同轮多门
            # 必丢澄清/审批其一（design R1）。skill 铁律 6 已约束独占一轮，这里保可观测。
            logger.warning(
                "RunPaused with %d paused tools (run_id=%s): agno 只保留最后一个 "
                "requirement，澄清门/审批门 MUST 独占一轮",
                n_paused, getattr(event, "run_id", ""),
            )
        if paused_tools:
            approval_tool = next(
                (
                    t for t in paused_tools
                    if getattr(t, "approval_type", None) is not None
                    or getattr(t, "approval_id", None) is not None
                ),
                None,
            )
            if approval_tool is not None:
                return [
                    _approval_pending_event(
                        session_id, agent_name, getattr(event, "run_id", "") or "", approval_tool,
                    )
                ]
            # 澄清门：无 approval_type 的 HITL 暂停 → 取最后一个带澄清 schema 的工具
            # （与 agno requirement 选取口径一致）。spike 结论：澄清暂停带
            # user_input_schema / user_feedback_schema，前端 key = run_id + tool_call_id。
            clarify_tool = next(
                (
                    t for t in reversed(paused_tools)
                    if getattr(t, "user_feedback_schema", None)
                    or getattr(t, "user_input_schema", None)
                ),
                None,
            )
            if clarify_tool is not None:
                return [
                    _clarification_request_event(
                        session_id, agent_name, getattr(event, "run_id", "") or "", clarify_tool,
                    )
                ]
            return [_generic_event(session_id, agent_name, "RunPaused")]
        return [_generic_event(session_id, agent_name, "RunPaused")]

    elif isinstance(event, CompressionCompletedEvent):
        return [
            _compression_event(
                session_id,
                getattr(event, "agent_name", "") or agent_name,
                stage="completed",
                tool_results_compressed=getattr(event, "tool_results_compressed", None),
                original_size=getattr(event, "original_size", None),
                compressed_size=getattr(event, "compressed_size", None),
            )
        ]

    elif isinstance(event, CompressionStartedEvent):
        return [
            _compression_event(
                session_id,
                getattr(event, "agent_name", "") or agent_name,
                stage="started",
            )
        ]

    else:
        # Log unhandled event types for debugging
        event_name = getattr(event, "event", type(event).__name__)
        logger.debug("Unhandled Agno event: %s", event_name)
        return [_generic_event(session_id, agent_name, str(event_name), getattr(event, "content", None))]


def _process_event_string(event: Any, session_id: str, agent_name: str, active_tools: dict) -> list[dict]:
    """Fallback: classify events by the .event string field."""
    ev_str = getattr(event, "event", "")

    if ev_str == "RunContent":
        events: list[dict] = []
        content = getattr(event, "content", "")
        if content and isinstance(content, str):
            events.append(_token_event(session_id, agent_name, content))
        reasoning = getattr(event, "reasoning_content", None)
        if reasoning and isinstance(reasoning, str):
            events.append(_reasoning_delta_event(session_id, agent_name, reasoning))
        return events

    elif ev_str == "ToolCallStarted":
        tool = getattr(event, "tool", None)
        if tool is None:
            return []
        tool_name = getattr(tool, "tool_name", "unknown") or "unknown"
        call_id = getattr(tool, "tool_call_id", "") or ""
        inputs = getattr(tool, "tool_args", {}) or {}
        active_tools[call_id] = time.monotonic()
        return [_tool_start_event(session_id, agent_name, tool_name, call_id, inputs)]

    elif ev_str == "ToolCallCompleted":
        tool = getattr(event, "tool", None)
        if tool is None:
            return []
        tool_name = getattr(tool, "tool_name", "unknown") or "unknown"
        call_id = getattr(tool, "tool_call_id", "") or ""
        result = getattr(tool, "result", None)
        start_ts = active_tools.pop(call_id, time.monotonic())
        elapsed_ms = (time.monotonic() - start_ts) * 1000
        return [_tool_end_event(session_id, agent_name, tool_name, call_id, result, elapsed_ms)]

    elif ev_str == "ToolCallError":
        tool = getattr(event, "tool", None)
        tool_name = getattr(tool, "tool_name", "unknown") if tool else "unknown"
        call_id = getattr(tool, "tool_call_id", "") if tool else ""
        error_msg = str(getattr(event, "content", "unknown error"))
        active_tools.pop(call_id, None)
        return [_tool_error_event(session_id, agent_name, tool_name, call_id or "", error_msg)]

    elif ev_str == "RunCompleted":
        # Same as typed path: content already streamed via RunContent events, skip.
        return []

    elif ev_str == "RunError":
        error = str(getattr(event, "content", "Agent run error"))
        return [_run_error_event(session_id, agent_name, error)]

    elif ev_str == "RunStarted":
        event_agent_name = getattr(event, "agent_name", "") or agent_name
        return [_run_started_event(session_id, event_agent_name)]

    elif ev_str in ("CompressionStarted", "compression_started"):
        return [
            _compression_event(
                session_id,
                getattr(event, "agent_name", "") or agent_name,
                stage="started",
            )
        ]

    elif ev_str in ("CompressionCompleted", "compression_completed"):
        return [
            _compression_event(
                session_id,
                getattr(event, "agent_name", "") or agent_name,
                stage="completed",
                tool_results_compressed=getattr(event, "tool_results_compressed", None),
                original_size=getattr(event, "original_size", None),
                compressed_size=getattr(event, "compressed_size", None),
            )
        ]

    else:
        logger.debug("Unhandled Agno event string: %s", ev_str)
        return [_generic_event(session_id, agent_name, ev_str, getattr(event, "content", None))]


# ---------------------------------------------------------------------------
# Agno response iterator helper
# ---------------------------------------------------------------------------

async def _iter_response(response_gen: Any) -> AsyncIterator[Any]:
    """
    Normalise the Agno response: it may be an async generator or a
    plain RunOutput.  Yield each event uniformly.
    """
    # Async generator path (stream=True)
    if hasattr(response_gen, "__aiter__"):
        async for event in response_gen:
            yield event
        return

    # Sync generator path (edge case)
    if hasattr(response_gen, "__iter__") and not isinstance(response_gen, (str, bytes)):
        for event in response_gen:
            yield event
        return

    # Plain RunOutput — treat as a single terminal event
    yield response_gen