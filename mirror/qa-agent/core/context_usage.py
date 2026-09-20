"""
QA Agent System — Context Usage Computation

Backs ``GET /sessions/{session_id}/context-usage``.

Computes per-session context window breakdown (system_prompt / tools / mcp_tools /
messages / free_space) and cumulative token consumption (usage_totals / per_agent).

Data sources:
  - active session: the in-memory Agno agent — last run's messages (includes the
    system message Agno composed from instructions), ``agent.tools`` split into
    builtin vs MCP via ``tool_registry`` metadata categories, metrics aggregated
    from the cached AgentSession runs.
  - inactive session: ``core.storage.get_storage().get_session()`` — AgentSession
    runs carry per-run metrics and the last run's message history. Agent-side
    breakdown (system_prompt / tools / mcp_tools / segments) degrades to None.

Token counting uses Agno ``count_tokens`` (tiktoken estimate for non-OpenAI
models — values are ESTIMATES, not official tokenizers).

Current-context anchor (fix undercount): ``categories.messages`` is derived
from the last assistant message's provider-reported ``metrics.input_tokens``
(the exact prompt size of the most recent LLM call — system + tools + full
history, post-compression) minus the estimated other categories; the badge
numerator and free_space are therefore exact, only the split is estimated.
Tokenizer-only fallback applies when no message carries usage.

Known limitation (design Context): the legacy ``agent.memory`` attribute no
longer exists in Agno 2.6.22, so ``QASessionState.current_context_tokens``
(updated by context_threshold_hook) undercounts. This module deliberately does
NOT read that field — it recomputes from RunOutput.messages.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from api.schemas import ContextUsageResponse, RunUsageEntry, UsageMetrics

logger = logging.getLogger(__name__)

# Cap for recent_runs (per-turn usage table payload size)
_MAX_RECENT_RUNS = 50

# ---------------------------------------------------------------------------
# Session-level result cache (invalidated on run_complete / approval_pending —
# see the api/server.py stream loop)
# ---------------------------------------------------------------------------

_cache: Dict[str, ContextUsageResponse] = {}


def invalidate(session_id: str) -> None:
    """Drop the cached ContextUsageResponse for a session (called after run_complete)."""
    _cache.pop(session_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _metrics_to_usage(metrics: Any) -> Optional[UsageMetrics]:
    """Convert an Agno RunMetrics/SessionMetrics object (or dict) to UsageMetrics.

    Returns None when no token counts are present at all.
    """
    get = (lambda k, d=None: metrics.get(k, d)) if isinstance(metrics, dict) else (lambda k, d=None: getattr(metrics, k, d))
    input_tokens = get("input_tokens", 0) or 0
    output_tokens = get("output_tokens", 0) or 0
    total_tokens = get("total_tokens", 0) or 0
    if not (input_tokens or output_tokens or total_tokens):
        return None
    return UsageMetrics(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=get("cache_read_tokens") or None,
        cache_write_tokens=get("cache_write_tokens") or None,
        reasoning_tokens=get("reasoning_tokens") or None,
        cost=get("cost"),
        duration_s=get("duration"),
    )


def _sum_usage(a: Optional[UsageMetrics], b: UsageMetrics) -> UsageMetrics:
    if a is None:
        return b
    return UsageMetrics(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
        cache_read_tokens=_opt_add(a.cache_read_tokens, b.cache_read_tokens),
        cache_write_tokens=_opt_add(a.cache_write_tokens, b.cache_write_tokens),
        reasoning_tokens=_opt_add(a.reasoning_tokens, b.reasoning_tokens),
        cost=_opt_add(a.cost, b.cost),
        duration_s=_opt_add(a.duration_s, b.duration_s),
    )


def _opt_add(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def _coerce_messages(raw: Any) -> List[Any]:
    """Normalize run messages to Agno Message objects (db round-trips yield dicts)."""
    from agno.models.message import Message

    out: List[Any] = []
    if not raw:
        return out
    for m in raw:
        if isinstance(m, Message):
            out.append(m)
        elif isinstance(m, dict):
            try:
                out.append(Message.from_dict(m))
            except Exception:
                logger.debug("Failed to coerce message dict to Message", exc_info=True)
        # Unknown shapes are skipped — count stays an estimate
    return out


def _partition_system(messages: List[Any]) -> Tuple[List[Any], List[Any]]:
    """Split messages into (system, non-system) by role."""
    sys_msgs: List[Any] = []
    other: List[Any] = []
    for m in messages:
        role = getattr(m, "role", None)
        if role is None and isinstance(m, dict):
            role = m.get("role")
        (sys_msgs if role == "system" else other).append(m)
    return sys_msgs, other


def _last_assistant_input_tokens(runs: List[Any]) -> Optional[int]:
    """Provider-reported prompt size of the most recent assistant message.

    最后一条带 usage 的 assistant 消息的 ``metrics.input_tokens`` = 该次 LLM
    调用的全量 prompt（system + tools + 完整历史，含压缩后的状态）——
    provider 亲自计的数，是「当前上下文」的精确锚点。旧实现只数
    last_run.messages（单轮消息），长会话低估 5-15 倍。
    """
    for run in reversed(runs):
        for msg in reversed(getattr(run, "messages", None) or []):
            role = getattr(msg, "role", None)
            if role is None and isinstance(msg, dict):
                role = msg.get("role")
            if role != "assistant":
                continue
            metrics = getattr(msg, "metrics", None)
            if metrics is None and isinstance(msg, dict):
                metrics = msg.get("metrics")
            if metrics is None:
                continue
            get = (
                (lambda k, d=None: metrics.get(k, d))
                if isinstance(metrics, dict)
                else (lambda k, d=None: getattr(metrics, k, d))
            )
            inp = get("input_tokens", 0) or 0
            if inp:
                return int(inp)
    return None


def _split_tools(tools: List[Any]) -> Tuple[List[Any], List[Any]]:
    """Split agent tools into (builtin, mcp) via tool_registry metadata category."""
    from tools.registry import tool_registry

    builtin: List[Any] = []
    mcp: List[Any] = []
    for t in tools:
        name = getattr(t, "name", "") or ""
        meta = tool_registry.get_meta(name)
        category = (meta or {}).get("category", "") or ""
        (mcp if str(category).startswith("mcp_") else builtin).append(t)
    return builtin, mcp


def _build_segments(system_text: str) -> Optional[Dict[str, int]]:
    """Estimate system-prompt section breakdown against engine's known sections.

    Section texts are re-imported and tokenized independently; the remainder is
    attributed to ``extra`` (agent-definition instructions, skills, rules...).
    Returned values are estimates — tokenizing parts separately does not sum
    exactly to tokenizing the whole.
    """
    if not system_text:
        return None

    from agno.utils.tokens import count_text_tokens

    def _t(text: str) -> int:
        return count_text_tokens(text)

    sections: List[Tuple[str, str]] = []
    try:
        from core.instructions import NORMAL_AGENT_INSTRUCTIONS
        sections.append(("base", NORMAL_AGENT_INSTRUCTIONS))
    except Exception:
        pass
    try:
        from core.prompts import PLAN_FIRST_INSTRUCTIONS, TOOL_ANTI_PATTERNS, get_task_management_section

        sections.append(("plan_first", PLAN_FIRST_INSTRUCTIONS))
        sections.append(("tool_anti_patterns", TOOL_ANTI_PATTERNS))
        sections.append(("task_management", get_task_management_section()))
    except Exception:
        pass

    segments: Dict[str, int] = {}
    known_total = 0
    for name, text in sections:
        if text:
            n = _t(text)
            segments[name] = n
            known_total += n
    total = _t(system_text)
    segments["extra"] = max(0, total - known_total)
    return segments


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

async def compute_context_usage(
    session_id: str, agent: Any = None, force: bool = False
) -> Optional[ContextUsageResponse]:
    """Compute (or return cached) context usage for a session.

    Args:
        session_id: Target session.
        agent: Live Agno agent instance from SessionRecord (None for inactive
            sessions — agent-side breakdown degrades to None).
        force: Bypass the cache read and recompute（弹窗手动刷新按钮）。
            结果仍写回缓存。run_complete / approval_pending 已失效缓存的
            常规路径无需 force。

    Returns:
        ContextUsageResponse, or None when the session exists neither in
        memory nor in storage (caller maps this to 404).
    """
    if force:
        _cache.pop(session_id, None)
    cached = _cache.get(session_id)
    if cached is not None:
        return cached

    # ── Resolve the AgentSession (runs + metrics) ──────────────────────
    session = None
    active = agent is not None
    if active:
        try:
            from agno.agent._session import aget_session

            session = await aget_session(agent, session_id=session_id)
        except Exception:
            logger.debug("aget_session failed for %s — falling back to storage", session_id, exc_info=True)
            session = None
            active = False
    if session is None:
        try:
            from agno.db.base import SessionType

            from core.storage import get_storage

            session = get_storage().get_session(session_id=session_id, session_type=SessionType.AGENT)
        except Exception:
            logger.debug("storage get_session failed for %s", session_id, exc_info=True)
            session = None
    if session is None:
        return None

    runs = list(getattr(session, "runs", None) or [])

    # ── usage_totals / per_agent / recent_runs aggregation ─────────────
    per_agent: Dict[str, UsageMetrics] = {}
    recent_runs: List[RunUsageEntry] = []
    for run in runs:
        metrics = getattr(run, "metrics", None)
        usage = _metrics_to_usage(metrics) if metrics is not None else None
        if usage is None:
            continue
        name = getattr(run, "agent_name", "") or "main"
        per_agent[name] = _sum_usage(per_agent.get(name), usage)
        recent_runs.append(
            RunUsageEntry(
                agent_name=name,
                usage=usage,
                created_at=getattr(run, "created_at", None),
            )
        )
    recent_runs = recent_runs[-_MAX_RECENT_RUNS:]

    usage_totals = UsageMetrics()
    for v in per_agent.values():
        usage_totals = _sum_usage(usage_totals, v)

    # Fallback: persisted session-level metrics (session_data["session_metrics"])
    if not per_agent:
        sd = getattr(session, "session_data", None)
        sm = sd.get("session_metrics") if isinstance(sd, dict) else None
        if sm:
            fallback = _metrics_to_usage(sm)
            if fallback is not None:
                per_agent["main"] = fallback
                usage_totals = fallback

    # ── Context window limit ───────────────────────────────────────────
    if active:
        state = getattr(agent, "session_state", None)
        max_ctx = getattr(state, "max_context_tokens", 0) or 0
    else:
        max_ctx = 0
    if not max_ctx:
        from core.config import settings as _settings

        max_ctx = _settings.max_context_tokens

    # ── Categories ─────────────────────────────────────────────────────
    categories: Dict[str, Optional[int]] = {
        "system_prompt": None,
        "tools": None,
        "mcp_tools": None,
        "messages": None,
        "free_space": None,
    }
    segments: Optional[Dict[str, int]] = None

    last_run = runs[-1] if runs else None
    messages = _coerce_messages(getattr(last_run, "messages", None))

    # Token counter: prefer the live model's count_tokens; else agno util.
    # The fallback literal is a *tokenizer hint*, not a model selection — agno's
    # count_tokens uses it to pick a tiktoken encoding and defaults to the
    # o200k_base family for ids it does not recognise.
    model_id = str(getattr(last_run, "model", "") or "gpt-4o")
    if active:
        model = getattr(agent, "model", None)
    else:
        model = None

    def _count_msgs(msgs: List[Any]) -> int:
        if not msgs:
            return 0
        if model is not None and hasattr(model, "count_tokens"):
            return model.count_tokens(messages=msgs)
        from agno.utils.tokens import count_tokens as _agno_count

        return _agno_count(msgs, model_id=model_id)

    def _count_tools(tools: List[Any]) -> int:
        if not tools:
            return 0
        if model is not None and hasattr(model, "count_tokens"):
            return model.count_tokens(messages=[], tools=tools)
        from agno.utils.tokens import count_tokens as _agno_count

        return _agno_count([], tools=list(tools), model_id=model_id)

    sys_msgs, other_msgs = _partition_system(messages)
    try:
        categories["messages"] = _count_msgs(other_msgs)
        if active:
            # Agent-side breakdown is active-only — inactive sessions return
            # null per spec (system_prompt / tools / mcp_tools / segments).
            if sys_msgs:
                categories["system_prompt"] = _count_msgs(sys_msgs)
            else:
                # Run messages carried no system message — fall back to instructions
                instructions = getattr(agent, "instructions", None)
                if isinstance(instructions, str) and instructions:
                    categories["system_prompt"] = _count_msgs(_coerce_messages([{"role": "system", "content": instructions}]))
    except Exception:
        logger.debug("message token counting failed (session=%s)", session_id, exc_info=True)

    if active:
        tools = list(getattr(agent, "tools", None) or [])
        try:
            builtin, mcp = _split_tools(tools)
            categories["tools"] = _count_tools(builtin)
            categories["mcp_tools"] = _count_tools(mcp)
        except Exception:
            logger.debug("tool token counting failed (session=%s)", session_id, exc_info=True)
        # Section-level breakdown (estimate, active only — see design D2)
        if isinstance(categories.get("system_prompt"), int):
            sys_text = ""
            if sys_msgs:
                sys_text = str(getattr(sys_msgs[-1], "content", "") or "")
            elif isinstance(getattr(agent, "instructions", None), str):
                sys_text = agent.instructions
            try:
                segments = _build_segments(sys_text)
            except Exception:
                logger.debug("segment breakdown failed (session=%s)", session_id, exc_info=True)
                segments = None

    # ── Anchor: provider-reported exact current context ───────────────
    # categories.messages 旧实现 = last_run.messages（单轮）的 tokenizer 估算，
    # 长会话严重低估（实测 22.4k vs 真实 154.7k）。锚点取最后一条带 usage 的
    # assistant 消息的 input_tokens（该次调用全量 prompt，压缩后状态），messages
    # 改为锚点减去其余分类的残差 —— 徽章 used 与 free_space 随之精确。
    anchor = _last_assistant_input_tokens(runs)
    if anchor is not None:
        est_others = sum(
            v for v in (categories.get("system_prompt"), categories.get("tools"), categories.get("mcp_tools")) if v
        )
        categories["messages"] = max(0, anchor - est_others)

    # free_space = max − used (floor 0)
    used = sum(v for v in (categories.get("system_prompt"), categories.get("tools"), categories.get("mcp_tools"), categories.get("messages")) if v)
    categories["free_space"] = max(0, max_ctx - used)

    response = ContextUsageResponse(
        session_id=session_id,
        max_context_tokens=max_ctx,
        categories=categories,
        segments=segments,
        usage_totals=usage_totals,
        per_agent=per_agent or None,
        recent_runs=recent_runs,
        updated_at=time.time(),
    )
    _cache[session_id] = response
    return response
