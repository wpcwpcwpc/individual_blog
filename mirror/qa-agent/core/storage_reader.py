"""
QA Agent System — Storage Reader

Reads historical conversation messages from Agno Storage (MongoDB/SQLite/PostgreSQL).
Encapsulates all Agno version-specific API access so that api/server.py
only depends on this module's stable interface.

Primary path:  Agno storage.get_session(session_id, SessionType.AGENT)
               → AgentSession.runs[*].messages
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Role normalisation map  (Agno may use "model" instead of "assistant")
_ROLE_MAP = {
    "model": "assistant",
    "assistant": "assistant",
    "user": "user",
    "tool": "tool",
    "system": "system",
}


def _merge_reasoning(*parts: Any) -> Optional[str]:
    """Merge ``reasoning_content`` + ``redacted_reasoning_content`` (agno
    thinking_combined semantics). Non-str/empty parts are dropped; empty
    result normalises to None so old data hydrates without a thinking block."""
    combined = "".join(p for p in parts if isinstance(p, str))
    return combined or None


def _normalise_message(msg: Any) -> Dict[str, Any]:
    """Convert an Agno message object or raw dict to a standard MessageRecord dict."""
    if isinstance(msg, dict):
        raw_role = msg.get("role", "assistant")
        content = msg.get("content")  # keep None as None
        created_at = float(msg.get("created_at") or msg.get("timestamp") or 0)
        tool_calls = msg.get("tool_calls") or None
        tool_call_id = msg.get("tool_call_id") or None
        name = msg.get("name") or None
        reasoning_content = _merge_reasoning(
            msg.get("reasoning_content"), msg.get("redacted_reasoning_content")
        )
    else:
        # Agno Message object
        raw_role = getattr(msg, "role", "assistant") or "assistant"
        content_val = getattr(msg, "content", None)
        content = content_val if (content_val is None or isinstance(content_val, str)) else str(content_val)
        created_at = float(getattr(msg, "created_at", 0) or 0)
        tool_calls = getattr(msg, "tool_calls", None) or None
        tool_call_id = getattr(msg, "tool_call_id", None) or None
        name = getattr(msg, "name", None) or None
        reasoning_content = _merge_reasoning(
            getattr(msg, "reasoning_content", None),
            getattr(msg, "redacted_reasoning_content", None),
        )

    role = _ROLE_MAP.get(str(raw_role).lower(), "assistant")
    return {
        "role": role,
        "content": str(content) if content is not None else None,
        "created_at": created_at,
        "tool_calls": tool_calls,
        "tool_call_id": tool_call_id,
        "name": name,
        "reasoning_content": reasoning_content,
    }


def inject_message_ids(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Inject stable ``id`` field into each message dict.

    IDs are assigned as ``msg_{index}`` (0-based) after messages have been
    sorted by ``created_at``.  This function mutates the dicts in-place and
    returns the same list for convenience.
    """
    for i, msg in enumerate(messages):
        msg["id"] = f"msg_{i}"
    return messages


def inject_message_ids_at(messages: List[Dict[str, Any]], start: int) -> List[Dict[str, Any]]:
    """Inject ``msg_{start + offset}`` ids — 分页页内消息按全量位号编号。

    前缀位号跨页/跨截断稳定（I2），使分页视图与全量视图的消息 id 一致。
    Mutates dicts in-place; returns the same list.
    """
    for offset, msg in enumerate(messages):
        msg["id"] = f"msg_{start + offset}"
    return messages


# ── Run-atomic pagination primitives ──

@dataclass
class RunBoundary:
    """Storage run 边界（run 原子分页单元的元数据）。"""

    run_id: Optional[str]
    created_at: int
    start: int   # 该 run 首消息在 ParsedSession.messages 中的下标
    count: int   # 该 run 消息数


@dataclass
class ParsedSession:
    """Agno AgentSession 全量解析结果（storage-only，不含 pending events）。"""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    runs: List[RunBoundary] = field(default_factory=list)

    @property
    def total_runs(self) -> int:
        return len(self.runs)

    @property
    def total_messages(self) -> int:
        return len(self.messages)


def _anchor_of(run: RunBoundary) -> str:
    """锚标识：run_id 优先；缺失时退化为 created_at 形式（D3 锚退化，E4）。"""
    if run.run_id:
        return run.run_id
    return f"rt:{run.created_at}"


def _inject_agent_name(messages: List[Dict[str, Any]], agent_name: Optional[str]) -> None:
    """把 agent_name 注入缺 name 的 assistant/tool 消息（原地）。"""
    if not agent_name:
        return
    for msg in messages:
        if msg.get("role") in ("assistant", "tool") and not msg.get("name"):
            msg["name"] = agent_name


async def _parse_session(session_id: str) -> Optional[ParsedSession]:
    """全量解析 AgentSession：runs 边界 + normalized storage 消息（runs 顺序）。

    Returns:
        ParsedSession，或 None（session 不存在 / 后端不可用）。
    """
    try:
        from core.storage import get_storage
        from agno.db.base import SessionType

        storage = get_storage()
        get_fn = getattr(storage, "get_session", None)
        if get_fn is None:
            logger.debug("Agno storage has no get_session method")
            return None

        import inspect
        if inspect.iscoroutinefunction(get_fn):
            agent_session = await get_fn(session_id, session_type=SessionType.AGENT)
        else:
            # Agno MongoDb 适配层为同步 pymongo，直调会阻塞 event loop
            # （远端 Mongo 全量拉取 session 文档）
            agent_session = await asyncio.to_thread(
                get_fn, session_id, session_type=SessionType.AGENT
            )

        if agent_session is None:
            return None

        parsed = ParsedSession()
        for run in getattr(agent_session, "runs", None) or []:
            raw_messages = getattr(run, "messages", None) or []
            start = len(parsed.messages)
            for m in raw_messages:
                parsed.messages.append(_normalise_message(m))
            parsed.runs.append(RunBoundary(
                run_id=getattr(run, "run_id", None),
                created_at=int(getattr(run, "created_at", 0) or 0),
                start=start,
                count=len(raw_messages),
            ))
        return parsed

    except Exception:
        logger.warning("_parse_session failed for session %s", session_id, exc_info=True)
        return None


# ── Parse cache（D6）：TTL LRU 摊销连续翻页的重复解析 ──────────────
# 仅 read_session_page *读取*：翻页内容是历史前缀，append-only（run 完成）
# 不改变前缀，30s 内命中安全；truncate 端点显式失效（invalidate_parse_cache）。
# tail / 全量读取需要最新 runs，读取绕过缓存，但解析结果 write-through 回填
# （harden-session-history-paging）——进会话那次全量解析直接预热首次翻页。

_PARSE_CACHE_TTL_S = 30.0
_PARSE_CACHE_MAX_ENTRIES = 32


@dataclass
class _CacheEntry:
    parsed: ParsedSession
    fetched_at: float


_parse_cache: "OrderedDict[str, _CacheEntry]" = OrderedDict()


def invalidate_parse_cache(session_id: str | None = None) -> None:
    """Invalidate cached parse results (truncate 端点主动失效，D6）。"""
    if session_id is None:
        _parse_cache.clear()
    else:
        _parse_cache.pop(session_id, None)


def _cache_store(session_id: str, parsed: ParsedSession) -> None:
    """Write a fresh parse into the TTL LRU cache (harden-session-history-paging)."""
    _parse_cache[session_id] = _CacheEntry(parsed=parsed, fetched_at=time.monotonic())
    _parse_cache.move_to_end(session_id)
    while len(_parse_cache) > _PARSE_CACHE_MAX_ENTRIES:
        _parse_cache.popitem(last=False)


async def _parse_session_fresh_cached(session_id: str) -> Optional[ParsedSession]:
    """Fresh parse (always hits storage) whose result warms the parse cache.

    Used by the tail / full-read paths: they must see the latest runs, so they
    never *read* the cache — they only write it back, letting a following
    read_session_page reuse the parse instead of paying another full-document
    fetch. Prefix append-only semantics (D6 comment above) keep a warmed entry
    safe for paging; truncate still invalidates explicitly.
    """
    parsed = await _parse_session(session_id)
    if parsed is not None:
        _cache_store(session_id, parsed)
    return parsed


async def parse_session_runs(session_id: str) -> Optional[ParsedSession]:
    """Public storage-only parse：fresh 解析 + 缓存预热。

    flatten 消息与 runs 边界（run_id / start / count）同源，供重答端点定位
    轮起点 U（按位号 `msg_{i}` 或 turn: last）及其所属边界 run。消息为 runs
    顺序、未注入位号 id（调用方自行 inject；位号语义与 tail 端点一致，依赖
    「created_at 排序 = runs 顺序」既有不变式）。
    """
    return await _parse_session_fresh_cached(session_id)


async def _parse_session_cached(session_id: str) -> Optional[ParsedSession]:
    """带 TTL LRU 缓存的解析（仅翻页路径使用，见上）。"""
    now = time.monotonic()
    entry = _parse_cache.get(session_id)
    if entry is not None:
        if now - entry.fetched_at <= _PARSE_CACHE_TTL_S:
            _parse_cache.move_to_end(session_id)
            return entry.parsed
        _parse_cache.pop(session_id, None)

    parsed = await _parse_session(session_id)
    if parsed is None:
        return None
    _cache_store(session_id, parsed)
    return parsed


def _shrink_page_to_item_budget(
    runs: List[RunBoundary],
    page_first: int,
    before: int,
    item_budget: int,
) -> int:
    """页 run 区间 [page_first, before) 若超 items 预算，从最老侧收缩。

    至少保留 1 run（超限单 run 整页返回，虚拟化兜底渲染）。
    Returns:
        收缩后的 page_first。
    """
    total_items = sum(r.count for r in runs[page_first:before])
    while total_items > item_budget and before - page_first > 1:
        total_items -= runs[page_first].count
        page_first += 1
    return page_first


async def read_session_page(
    session_id: str,
    *,
    before: Optional[int] = None,
    limit: Optional[int] = None,
    anchor: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """向前翻页读取：返回 runs[page_first:before) 的 run 原子页（D1）。

    Args:
        before: 排他 run 下标上界（缺省 = 最新，即从尾部往回）。
        limit: 请求的 run 数（服务端 clamp 到 [1, 3]，items 预算 30）。
        anchor: 客户端窗口最老 run 的锚标识（run_id 或退化形式），
                必须落在 runs[before]；不一致 → resync 信号（ABA 防护，D3）。

    Returns:
        ``{resync, messages, page_meta}``；messages 已注入全量位号 id
        与 agent_name。resync=True 时 messages 为空，page_meta 携带新鲜
        totals 供客户端重拉尾页。
    """
    parsed = await _parse_session_cached(session_id)
    if parsed is None:
        return {
            "resync": True,
            "messages": [],
            "page_meta": {
                "has_more": False,
                "next_before": 0,
                "total_runs": 0,
                "total_messages": 0,
                "anchor": None,
            },
        }

    total_runs = parsed.total_runs

    # ── 锚校验（D3 ABA 防护）：anchor 必须落在 runs[before] ──
    # （翻页边界 = 客户端已加载窗口的最老 run；前缀漂移即多标签并发 truncate）
    if anchor is not None and before is not None:
        anchor_idx = next(
            (i for i, r in enumerate(parsed.runs) if _anchor_of(r) == anchor), None
        )
        if anchor_idx is None or anchor_idx != before:
            logger.info(
                "page anchor mismatch for session %s (anchor=%s before=%s anchor_idx=%s) — resync",
                session_id, anchor, before, anchor_idx,
            )
            return {
                "resync": True,
                "messages": [],
                "page_meta": {
                    "has_more": True,
                    "next_before": max(total_runs, 1),
                    "total_runs": total_runs,
                    "total_messages": parsed.total_messages,
                    "anchor": None,
                },
            }

    # harden-session-history-paging：页预算对齐 tail（3 runs / 30 items），
    # 减半单页 payload；缓存预热后单页服务端代价 ~10ms 级。
    run_cap = 3
    item_budget = 30
    limit_runs = min(max(limit, 1), run_cap) if limit is not None else run_cap
    upper = before if before is not None else total_runs
    upper = max(min(upper, total_runs), 0)

    if upper == 0:
        return {
            "resync": False,
            "messages": [],
            "page_meta": {
                "has_more": False,
                "next_before": 0,
                "total_runs": total_runs,
                "total_messages": parsed.total_messages,
                "anchor": None,
            },
        }

    page_first = max(0, upper - limit_runs)
    page_first = _shrink_page_to_item_budget(parsed.runs, page_first, upper, item_budget)

    start = parsed.runs[page_first].start
    end = parsed.runs[upper - 1].start + parsed.runs[upper - 1].count
    page_messages = [dict(m) for m in parsed.messages[start:end]]
    inject_message_ids_at(page_messages, start)
    _inject_agent_name(page_messages, agent_name)

    return {
        "resync": False,
        "messages": page_messages,
        "page_meta": {
            "has_more": page_first > 0,
            "next_before": page_first,
            "total_runs": total_runs,
            "total_messages": parsed.total_messages,
            "anchor": _anchor_of(parsed.runs[page_first]),
        },
    }


async def read_session_tail(
    session_id: str,
    *,
    limit: Optional[int] = None,
    agent_name: Optional[str] = None,
    session_status: Optional[str] = None,
) -> Dict[str, Any]:
    """尾页原语（D2）：最近 N runs + 进行中 run 的 pending 事件合并。

    页预算 ``min(3 runs, 30 items)``（至少 1 run）。消息按 created_at
    稳定排序后注入全量位号（pending 消息位于尾部，storage 位号与全量
    端点一致）。读取绕过缓存（需要最新 runs），解析结果 write-through
    回填预热翻页（harden-session-history-paging）。

    Returns:
        ``{messages, page_meta}``。
    """
    parsed = await _parse_session_fresh_cached(session_id)
    if parsed is None:
        return {
            "messages": [],
            "page_meta": {
                "has_more": False,
                "next_before": 0,
                "total_runs": 0,
                "total_messages": 0,
                "anchor": None,
            },
        }

    messages = list(parsed.messages)

    # 合并进行中 run 的持久化事件（与全量端点同源语义）
    _ACTIVE_STATES = {"running", "aborted", "failed"}
    if session_status and session_status.lower() in _ACTIVE_STATES:
        pending = await _read_pending_events(session_id)
        if pending:
            messages.extend(pending)

    # 稳定排序：正常数据下 pending 位于尾部，storage 相对顺序不变
    messages.sort(key=lambda m: m.get("created_at", 0))

    total_runs = parsed.total_runs
    run_cap = 3
    item_budget = 30
    limit_runs = min(max(limit, 1), run_cap) if limit is not None else run_cap

    page_first = max(0, total_runs - limit_runs)
    page_first = _shrink_page_to_item_budget(parsed.runs, page_first, total_runs, item_budget)

    # 页起点下标：page_first run 的 storage 起始（排序后 storage 前缀位不变）
    start = parsed.runs[page_first].start if total_runs > 0 else 0
    tail_messages = [dict(m) for m in messages[start:]]
    inject_message_ids_at(tail_messages, start)
    _inject_agent_name(tail_messages, agent_name)

    return {
        "messages": tail_messages,
        "page_meta": {
            "has_more": page_first > 0,
            "next_before": page_first,
            "total_runs": total_runs,
            "total_messages": parsed.total_messages,
            "anchor": _anchor_of(parsed.runs[page_first]) if total_runs > 0 else None,
        },
    }


async def read_session_messages(
    session_id: str,
    *,
    agent_name: Optional[str] = None,
    session_status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read all messages for a session from Agno Storage, with event aggregation.

    When the session is in an active state (RUNNING / ABORTED / FAILED),
    messages from the in-progress run are recovered by aggregating persisted
    events from ``PersistentEventStore`` (backed by MongoDB ``session_events``).

    Args:
        session_id: The session UUID.
        agent_name: Optional agent name to inject into assistant messages whose
                    ``name`` field is None. Agno does not persist the agent name
                    on stored messages, so callers can pass it from session meta.
        session_status: Current session status string (e.g. "running", "aborted").
                        When set to an active state, event aggregation is performed
                        to recover in-progress run messages.

    Returns:
        List of normalised message dicts sorted by created_at ascending.
        Returns empty list if session has no messages or storage is unavailable.
    """
    messages = await _read_via_agno_api(session_id)

    # Merge in-progress run messages from persisted events
    _ACTIVE_STATES = {"running", "aborted", "failed"}
    if session_status and session_status.lower() in _ACTIVE_STATES:
        pending = await _read_pending_events(session_id)
        if pending:
            messages.extend(pending)

    # Sort ascending by created_at (oldest first)
    messages.sort(key=lambda m: m.get("created_at", 0))

    # Inject agent_name into assistant messages that lack a name
    if agent_name:
        for msg in messages:
            if msg.get("role") in ("assistant", "tool") and not msg.get("name"):
                msg["name"] = agent_name

    return messages


async def read_history_stats(session_id: str) -> Optional[Dict[str, int]]:
    """Count persisted runs and messages for a session (storage-only view).

    Feeds the D14 ``history_version`` tuple. Excludes in-progress event
    aggregation — the version describes the persisted runs JSON, which is
    the authoritative history.

    Returns:
        ``{"total_runs": R, "total_messages": M}`` or None when the session
        is unavailable in storage.

    离线收敛: history_version 元组。
    """
    parsed = await _parse_session(session_id)
    if parsed is None:
        return None
    return {"total_runs": parsed.total_runs, "total_messages": parsed.total_messages}


async def _read_pending_events(session_id: str) -> List[Dict[str, Any]]:
    """Read and aggregate in-progress run events from PersistentEventStore."""
    try:
        from api.event_store import event_store
        from core.message_aggregator import aggregate_events_to_messages

        events = await event_store.replay_async(session_id, after_seq=0)
        if not events:
            return []

        aggregated = aggregate_events_to_messages(events)
        logger.debug(
            "Aggregated %d events into %d messages for session %s",
            len(events),
            len(aggregated),
            session_id,
        )
        return aggregated

    except Exception:
        logger.warning(
            "Failed to read pending events for session %s",
            session_id,
            exc_info=True,
        )
        return []


async def _read_via_agno_api(session_id: str) -> List[Dict[str, Any]]:
    """Primary path: use Agno storage API to read messages.

    Agno 2.5.14: SqliteDb.get_session(session_id, session_type=SessionType.AGENT)
    returns an AgentSession whose `.runs` contains List[RunOutput],
    each RunOutput having `.messages` → List[Message].
    """
    parsed = await _parse_session_fresh_cached(session_id)
    if parsed is None:
        return []
    logger.debug("Agno API: read %d messages for session %s", len(parsed.messages), session_id)
    return list(parsed.messages)


async def _read_via_sql(session_id: str) -> List[Dict[str, Any]]:
    """Fallback path: direct SQL query on qa_agent_sessions.runs column.

    The ``runs`` column stores data as double-JSON-encoded text:
      json.loads(raw) → str → json.loads() again → List[RunDict]
    Each RunDict contains a ``messages`` list.
    """
    try:
        from core.config import settings, StorageBackend
        import sqlite3

        if settings.storage_backend != StorageBackend.SQLITE:
            logger.debug("SQL fallback only supports SQLite; skipping for postgres backend")
            return []

        db_path = str(settings.sqlite_path_resolved)
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT runs FROM qa_agent_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row is None or not row[0]:
            return []

        # Defensive double-JSON decoding: handle both single and double encoding
        decoded = row[0]
        if isinstance(decoded, str):
            decoded = json.loads(decoded)
            if isinstance(decoded, str):
                # Double-encoded: json.loads again
                logger.debug("runs column is double-JSON-encoded for session %s", session_id)
                decoded = json.loads(decoded)

        if not isinstance(decoded, list):
            logger.warning("Unexpected runs data type %s for session %s", type(decoded).__name__, session_id)
            return []

        # decoded is List[RunDict]; each run has a "messages" key
        result: List[Dict[str, Any]] = []
        for run in decoded:
            if not isinstance(run, dict):
                continue
            for msg in run.get("messages", []):
                result.append(_normalise_message(msg))

        logger.debug("SQL fallback: read %d messages for session %s", len(result), session_id)
        return result

    except Exception:
        logger.debug("_read_via_sql failed for session %s", session_id, exc_info=True)
        return []
