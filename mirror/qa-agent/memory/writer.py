"""
QA Agent System — Memory Writer

Provides async write functions for all three Milvus collections.
All writes are non-blocking (called via asyncio.create_task from hooks).

Uses KnowledgeHub (Agno Knowledge + Milvus) for all write operations.
Embedding is handled automatically by Agno's OpenAIEmbedder.

Embedding-payload truncation
----------------------------
All ``text_content`` strings passed to ``knowledge.ainsert`` are capped at
``settings.memory_max_embed_chars`` characters before the insert is
attempted (see memory-write-reliability spec, "Embedding Payload
Truncation" requirement). Rationale: without this cap, a single large
tool result (e.g. a 100 KB ``read_file``) can exhaust the embedding
gateway's TPM budget, causing the embedder to return None and the Milvus
insert to fail with ``nil vector``. Truncation turns that hard failure
into a weaker-recall degradation — acceptable per the memory-architecture
loss model for Milvus.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)
event_logger = logging.getLogger("qa_agent.events")

# Suffix appended to the embedded text when truncation occurred. Visible to
# operators via scripts/peek_milvus.py so over-budget payloads are
# identifiable without reading code.
_EMBED_TRUNCATION_SUFFIX = "...[embed-truncated]"


def _cap_for_embedding(text: str) -> Tuple[str, bool]:
    """Truncate ``text`` to ``settings.memory_max_embed_chars`` characters.

    Returns:
        (capped_text, was_truncated). When truncation occurred, the
        returned string ends with :data:`_EMBED_TRUNCATION_SUFFIX` so the
        embedded content itself carries a visible marker (e.g. for
        ``scripts/peek_milvus.py`` output).

    The cap is read from settings on every call (not cached) so a process
    picking up a new env var on reload sees the new value.
    """
    # Lazy import to avoid circulars during module init.
    from core.config import settings

    cap = int(settings.memory_max_embed_chars)
    if text is None:
        return "", False
    if len(text) <= cap:
        return text, False

    # Reserve room for the suffix inside the cap so the total stays ≤ cap.
    body_len = max(0, cap - len(_EMBED_TRUNCATION_SUFFIX))
    capped = text[:body_len] + _EMBED_TRUNCATION_SUFFIX
    logger.info(
        "memory.writer: capped embedding payload %d → %d chars (cap=%d)",
        len(text), len(capped), cap,
    )
    return capped, True


# Recognized values of the free-form ``compression_type`` metadata field.
# - ``soft`` / ``hard``       : threshold-triggered compressions via
#                                hooks.context_threshold_hook
# - ``session_close``         : opt-in, terminal-flow writes at session end
#                                (see write_knowledge docstring)
# Unknown values are accepted but logged at WARNING by write_knowledge so
# typos don't silently create a new filter category.
_KNOWN_COMPRESSION_TYPES: frozenset[str] = frozenset(
    {"soft", "hard", "session_close"}
)


async def write_execution_log(entry: Dict[str, Any]) -> bool:
    """Write a single tool execution record to qa_execution_log.

    Called from :func:`hooks.execution_log_hook.execution_log_tool_hook`
    via ``asyncio.create_task`` (fire-and-forget).

    Args:
        entry: Dict with fields matching the qa_execution_log schema.
               Required: session_id, worker_id, step_no, tool_used,
                         tool_args_summary, result_summary, timestamp.

    Returns:
        True if written successfully, False otherwise.
    """
    from memory.knowledge_hub import get_knowledge_hub

    hub = get_knowledge_hub()
    if hub is None:
        return False

    # Build embedding text from tool + result summary
    text_content = (
        f"Tool: {entry.get('tool_used', '')} | "
        f"Result: {entry.get('result_summary', '')}"
    )
    # Cap embedder payload BEFORE calling safe_insert. This is an
    # additional, embedder-facing cap and does NOT replace the per-field
    # metadata caps in _trunc() below — metadata sizes are governed
    # independently.
    text_content, _was_truncated = _cap_for_embedding(text_content)

    metadata = {
        "session_id": _trunc(entry.get("session_id", ""), 128),
        "worker_id": _trunc(entry.get("worker_id", ""), 128),
        "step_no": int(entry.get("step_no", 0)),
        "tool_used": _trunc(entry.get("tool_used", ""), 128),
        "tool_args_summary": _trunc(entry.get("tool_args_summary", ""), 1024),
        "result_summary": _trunc(entry.get("result_summary", ""), 2048),
        "game_version": _trunc(entry.get("game_version", ""), 64),
        "module": _trunc(entry.get("module", ""), 128),
        "timestamp": float(entry.get("timestamp", time.time())),
    }

    success = await hub.safe_insert(
        hub.exec_log_knowledge,
        text_content=text_content,
        metadata=metadata,
    )

    if success:
        logger.debug(
            "Wrote execution log (session=%s, tool=%s)",
            entry.get("session_id"), entry.get("tool_used"),
        )
    return success


async def write_knowledge(entry: Dict[str, Any]) -> bool:
    """Public helper to write a compressed context summary to ``qa_knowledge``.

    This helper is public — it is NOT gated behind a hook. Two documented
    entry points currently call it:

    1. :mod:`hooks.context_threshold_hook` after soft/hard compression
       (``compression_type`` is ``"soft"`` or ``"hard"``).
    2. Terminal session flows that want to guarantee a closing knowledge
       record exists even if the token threshold was never crossed — these
       use ``compression_type="session_close"``.

    The set of documented ``compression_type`` values is therefore
    ``{"soft", "hard", "session_close"}``. Values outside this set are
    accepted (the field is a free-form string) but emit a single-line
    WARNING so typos are caught early.

    Args:
        entry: Dict with fields matching the qa_knowledge schema.

    Returns:
        True if written successfully, False otherwise.
    """
    from memory.knowledge_hub import get_knowledge_hub

    # Typo catcher — free-form accepted but any unknown value is flagged once
    # per call so `compression_type="session_cloze"` is loud instead of silent.
    _ct = entry.get("compression_type", "soft")
    if _ct not in _KNOWN_COMPRESSION_TYPES:
        logger.warning(
            "write_knowledge: unknown compression_type=%r "
            "(expected one of %s) — persisting anyway",
            _ct, sorted(_KNOWN_COMPRESSION_TYPES),
        )

    hub = get_knowledge_hub()
    if hub is None:
        return False

    # Build embedding text from knowledge fields
    embed_parts = [
        entry.get("completed_steps", ""),
        entry.get("pending_steps", ""),
        entry.get("findings", ""),
    ]
    text_content = " | ".join(p for p in embed_parts if p)
    # Cap embedder payload BEFORE calling safe_insert (see write_execution_log).
    text_content, _was_truncated = _cap_for_embedding(text_content)

    metadata = {
        "session_id": _trunc(entry.get("session_id", ""), 128),
        "worker_id": _trunc(entry.get("worker_id", ""), 128),
        "compression_type": _trunc(entry.get("compression_type", "soft"), 16),
        "compression_count": int(entry.get("compression_count", 1)),
        "completed_steps": _trunc(entry.get("completed_steps", ""), 4096),
        "pending_steps": _trunc(entry.get("pending_steps", ""), 2048),
        "findings": _trunc(entry.get("findings", ""), 4096),
        "context_coverage": _trunc(entry.get("context_coverage", ""), 512),
        "game_version": _trunc(entry.get("game_version", ""), 64),
        "module": _trunc(entry.get("module", ""), 128),
        "timestamp": float(entry.get("timestamp", time.time())),
    }

    success = await hub.safe_insert(
        hub.distill_knowledge,
        text_content=text_content,
        metadata=metadata,
    )

    if success:
        logger.info(
            "Wrote knowledge summary (session=%s, type=%s, compression_count=%d)",
            entry.get("session_id"),
            entry.get("compression_type"),
            entry.get("compression_count", 1),
        )
        event_logger.info(
            "📦 [压缩] L4 归档完成: Milvus qa_knowledge 写入成功 type=%s count=%d session=%s",
            entry.get("compression_type"),
            entry.get("compression_count", 1),
            entry.get("session_id"),
        )
    else:
        event_logger.info(
            "📦 [压缩] L4 归档失败: Milvus qa_knowledge 写入失败 type=%s session=%s",
            entry.get("compression_type"),
            entry.get("session_id"),
        )
    return success


def _trunc(value: Any, max_len: int) -> str:
    """Convert value to string and truncate to max_len."""
    s = str(value) if value is not None else ""
    return s[:max_len]