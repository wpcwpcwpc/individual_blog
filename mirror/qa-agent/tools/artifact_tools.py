"""
QA Agent System — Artifact Sharing Tools (tasks 3.1–3.3)

Provides save_artifact / load_artifact tools for Workers to persist structured
phase outputs to MongoDB and share them across Worker boundaries via artifact_ids.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from agno.tools import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

@tool(
    name="save_artifact",
    description=(
        "Save phase output as a versioned artifact in MongoDB; returns artifact_id "
        "for downstream Workers via agent_spawn(context_ids=[...]). "
        "artifact_type ∈ {diff_analysis, test_cases, automation_script, test_report}."
    ),
)
async def save_artifact(
    artifact_type: str,
    content: Dict[str, Any],
    summary: str,
) -> str:
    """Persist a phase artifact to MongoDB and return its artifact_id.

    Args:
        artifact_type: Category of artifact (diff_analysis, test_cases, etc.)
        content: Full structured content of the artifact.
        summary: Short summary (≤200 tokens) for downstream Worker context injection.

    Returns:
        String in the format "artifact_id=<ObjectId> | summary=<summary>"
    """
    try:
        from db.models import QaArtifactVersion

        # Validate content is JSON-serialisable
        try:
            json.dumps(content)
        except (TypeError, ValueError) as exc:
            return f"Error: content is not JSON-serialisable — {exc}"

        artifact = QaArtifactVersion(
            session_id="",          # populated below from RunContext if available
            phase=artifact_type,
            artifact_type=artifact_type,
            content=content,
            content_summary=summary[:500],  # cap at 500 chars
            created_by="llm",
        )
        await artifact.insert()

        artifact_id = str(artifact.id)
        logger.info("save_artifact: saved %s artifact (id=%s)", artifact_type, artifact_id)
        return f"artifact_id={artifact_id} | summary={summary}"

    except Exception as exc:
        logger.exception("save_artifact: failed to save artifact")
        return f"Error saving artifact: {exc}"


@tool(
    name="load_artifact",
    description=(
        "Load a saved artifact's full content by artifact_id. "
        "Returns {artifact_type, content, version, review_status} or {error}."
    ),
)
async def load_artifact(artifact_id: str) -> Dict[str, Any]:
    """Fetch a persisted artifact from MongoDB by its ObjectId string.

    Args:
        artifact_id: The ObjectId string returned by save_artifact.

    Returns:
        Dict with keys: artifact_type, content, version, created_by, review_status.
        On not-found: {"error": "artifact <id> not found"}
    """
    return await load_artifact_raw(artifact_id) or {"error": f"artifact {artifact_id} not found"}


# ---------------------------------------------------------------------------
# Internal helper (not exposed as a tool — used by reminder_pre_hook)
# ---------------------------------------------------------------------------

async def load_artifact_raw(artifact_id: str) -> Optional[Dict[str, Any]]:
    """Fetch artifact from MongoDB, returning None if not found.

    Used internally by reminder_pre_hook to inject context_ids artifacts into
    Worker system prompt without surfacing a tool to the LLM.

    Args:
        artifact_id: ObjectId string.

    Returns:
        Dict with artifact data, or None if not found / error.
    """
    try:
        from bson import ObjectId
        from bson.errors import InvalidId
        from db.models import QaArtifactVersion

        try:
            oid = ObjectId(artifact_id)
        except InvalidId:
            logger.warning("load_artifact_raw: invalid ObjectId %r", artifact_id)
            return None

        artifact = await QaArtifactVersion.get(oid)
        if artifact is None:
            return None

        return {
            "artifact_id": artifact_id,
            "artifact_type": artifact.artifact_type,
            "content": artifact.content,
            "content_summary": artifact.content_summary,
            "version": artifact.version,
            "created_by": artifact.created_by,
            "review_status": artifact.review_status,
        }

    except Exception:
        logger.exception("load_artifact_raw: error loading artifact %r", artifact_id)
        return None
