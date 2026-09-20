"""
QA Agent System — Conclusion Writer

Writes human-confirmed test conclusions to qa_conclusions.
Enforces the hard requirement that human_confirmed=True before writing.

Uses KnowledgeHub (Agno Knowledge + Milvus) for write operations.
Embedding is handled automatically by Agno's OpenAIEmbedder.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)


class UnconfirmedConclusionError(Exception):
    """Raised when attempting to write a conclusion without human confirmation."""
    pass


async def write_conclusion(entry: Dict[str, Any]) -> bool:
    """Write a human-confirmed test conclusion to qa_conclusions.

    IMPORTANT: This function enforces that human_confirmed=True.
    Any attempt to write with human_confirmed=False raises UnconfirmedConclusionError.

    Args:
        entry: Dict with fields:
            - session_id (str): Session identifier
            - verdict (str): "pass" | "fail" | "pass_with_issues"
            - human_confirmed (bool): MUST be True
            - confirmed_by (str): Name/ID of the reviewer
            - key_findings (str): Summary of key test findings
            - bugs_found (str): Bug list or "None"
            - game_version (str): Game version tested
            - module (str): Game module tested
            - notes (str, optional): Reviewer notes

    Returns:
        True if written successfully, False on Milvus failure.

    Raises:
        UnconfirmedConclusionError: If human_confirmed is not True.
    """
    # Guard: must be human-confirmed
    if not entry.get("human_confirmed", False):
        raise UnconfirmedConclusionError(
            f"Cannot write conclusion without human confirmation "
            f"(session={entry.get('session_id')}, verdict={entry.get('verdict')}). "
            "Set human_confirmed=True only after the human has verified the result."
        )

    from memory.knowledge_hub import get_knowledge_hub

    hub = get_knowledge_hub()
    if hub is None:
        logger.warning(
            "KnowledgeHub not available — conclusion NOT persisted (session=%s, verdict=%s)",
            entry.get("session_id"), entry.get("verdict"),
        )
        return False

    # Build text content for embedding
    text_content = (
        f"Verdict: {entry.get('verdict', '')} | "
        f"Module: {entry.get('module', '')} | "
        f"Findings: {entry.get('key_findings', '')} | "
        f"Bugs: {entry.get('bugs_found', '')}"
    )

    metadata = {
        "session_id": _trunc(entry.get("session_id", ""), 128),
        "verdict": _trunc(entry.get("verdict", "pass"), 32),
        "human_confirmed": True,  # always True at this point (enforced above)
        "confirmed_by": _trunc(entry.get("confirmed_by", ""), 128),
        "key_findings": _trunc(entry.get("key_findings", ""), 4096),
        "bugs_found": _trunc(entry.get("bugs_found", ""), 4096),
        "game_version": _trunc(entry.get("game_version", ""), 64),
        "module": _trunc(entry.get("module", ""), 128),
        "notes": _trunc(entry.get("notes", ""), 2048),
        "timestamp": float(entry.get("timestamp", time.time())),
    }

    success = await hub.safe_insert(
        hub.conclusion_knowledge,
        text_content=text_content,
        metadata=metadata,
    )

    if success:
        logger.info(
            "Conclusion written (session=%s, verdict=%s, confirmed_by=%s, module=%s)",
            entry.get("session_id"),
            entry.get("verdict"),
            entry.get("confirmed_by"),
            entry.get("module"),
        )
    else:
        logger.error(
            "Failed to write conclusion to Milvus (session=%s, verdict=%s)",
            entry.get("session_id"), entry.get("verdict"),
        )

    return success


def _trunc(value: Any, max_len: int) -> str:
    """Convert value to string and truncate to max_len."""
    s = str(value) if value is not None else ""
    return s[:max_len]