"""
QA Agent System — MongoDB / Beanie Initialization

  - Uses AsyncIOMotorClient (no explicit ping — lazy connection avoids wire-version check)
  - Initializes Beanie ODM for qa_agent_db (user_sessions, workspaces, checkpoints, …)
  - The server location comes from the ``MONGO_URI`` environment variable; no host or
    database other than qa_agent_db is assumed.

Usage:
    from db.mongo import init_db, close_db
"""

from __future__ import annotations

import logging

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


async def init_db() -> None:
    """Initialize Beanie ODM for qa_agent_db.

    No explicit ping — lazy connection only.
    """
    global _client

    # Import Document models here to avoid circular imports at module level
    from db.models import (
        AgentUser,
        CoordinatorWorker,
        QaArtifactVersion,
        QaPhaseCheckpoint,
        SessionEvent,
        SessionSeqWatermark,
        UserSession,
        UserSkill,
        WorkspaceRecord,
    )

    try:
        _client = AsyncIOMotorClient(settings.mongo_uri)

        # qa-agent dedicated database
        qa_agent_db = _client.get_database("qa_agent_db")
        await init_beanie(
            database=qa_agent_db,
            document_models=[
                UserSession,
                AgentUser,
                WorkspaceRecord,
                QaPhaseCheckpoint,
                QaArtifactVersion,
                SessionEvent,
                SessionSeqWatermark,
                CoordinatorWorker,
                UserSkill,
            ],
        )

        logger.info("✓ MongoDB / Beanie initialized")

    except Exception:
        logger.warning("⚠ MongoDB unavailable — user-session mapping disabled", exc_info=True)
        _client = None


async def close_db() -> None:
    """Close the MongoDB client connection."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("MongoDB connection closed")


def get_motor_db():
    """Return the qa_agent_db Motor database, or None if not initialized.

    Used by skill materializer (sync / DB fallback) which needs raw Motor
    access (distinct from Beanie document methods used by routes).
    """
    if _client is None:
        return None
    return _client.get_database("qa_agent_db")