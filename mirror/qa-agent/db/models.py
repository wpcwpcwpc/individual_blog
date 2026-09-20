"""
QA Agent System — Beanie Document Models

All MongoDB document models used by qa-agent.
Pattern: beanie.Document + Settings.name.

All collections live in the single ``qa_agent_db`` database.
"""

from __future__ import annotations

import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# qa_agent_db.user_sessions
# ---------------------------------------------------------------------------

class SessionEntry(BaseModel):
    """Embedded session metadata stored inside UserSession.sessions array."""
    session_id: str
    agent_name: str
    mode: str = "normal"
    game_version: str = ""
    module: str = ""
    title: str = ""
    created_at: float = Field(default_factory=time.time)
    last_active_at: float = Field(default_factory=time.time)
    # Persistent run lifecycle marker for crash detection.
    # "idle"        — no agent run in progress
    # "running"     — agent run started; if process dies, stays "running" forever
    #                 (this is the signal startup recovery hook detects)
    # "interrupted" — recovered from a crashed run; surfaced to UI as needed
    run_status: str = "idle"
    # ---- Coordinator-specific fields (mode="coordinator" only) ----
    worker_agent_names: List[str] = Field(default_factory=list)
    # Phase 0 PlanAgent output, truncated to 3000 chars; injected into
    # coordinator system prompt on restore to preserve planning context.
    plan_context: str = ""
    # User-visible hide flag for sidebar focus management.
    # Hidden sessions collapse into a separate section; search still finds them.
    hidden: bool = False


class UserSession(Document):
    """One document per user, embedding all their session metadata.

    Collection: qa_agent_db.user_sessions
    Primary key: email (unique index via Beanie index)
    """
    email: str
    sessions: List[SessionEntry] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)

    class Settings:
        name = "user_sessions"
        indexes = ["email"]


# ---------------------------------------------------------------------------
# qa_agent_db.agent_users
# qa-agent self-managed user auth store. First login auto-upserts
# (role=normal); the role is operator-managed and never set from the UI.
# ---------------------------------------------------------------------------

class AgentUser(Document):
    """qa-agent UI user auth record (qa_agent_db.agent_users).

    Minimal field set: email (unique key), name (from the identity provider,
    for UI display), role (operator-managed label).
    """
    email: str
    name: str = ""
    role: str = "normal"

    class Settings:
        name = "agent_users"
        indexes = ["email"]


# ---------------------------------------------------------------------------
# qa_agent_db.workspaces
# ---------------------------------------------------------------------------

class WorkspaceRecord(Document):
    """Workspace binding for a session — one workspace per session.

    Collection: qa_agent_db.workspaces
    Unique index: session_id
    """
    session_id: str
    root_path: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    class Settings:
        name = "workspaces"
        indexes = ["session_id"]


# ---------------------------------------------------------------------------
# qa_agent_db.qa_phase_checkpoints  (task 1.1 + 1.2)
# ---------------------------------------------------------------------------

class CheckpointStatus(str, Enum):
    """Lifecycle states for a QA phase checkpoint."""
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    ABORTED = "aborted"


class QaPhaseCheckpoint(Document):
    """Phase-level checkpoint for interrupt/resume across sessions.

    Written at the start of every Skill Phase and updated as the phase
    progresses through review or interruption states.

    Collection: qa_agent_db.qa_phase_checkpoints
    """

    # --- Identity ---
    task_id: Optional[str] = None               # High-level task identifier (human-readable)
    session_id: str                             # Agno session_id
    phase: str                                  # Phase name, e.g. "test_case_generation"
    phase_index: int = 0                        # 0-based index within the skill plan

    # --- Status ---
    status: CheckpointStatus = CheckpointStatus.RUNNING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # --- Progress tracking ---
    completed_phases: List[str] = Field(default_factory=list)       # Phases already done
    artifact_ids: Dict[str, str] = Field(default_factory=dict)      # phase_name → artifact ObjectId str

    # --- Current artifact under review ---
    artifact_id: Optional[str] = None          # ObjectId str of the artifact awaiting review
    artifact_type: Optional[str] = None        # e.g. "test_cases"

    # --- Review metadata (awaiting_review only) ---
    llm_summary: Optional[str] = None          # LLM-written summary for the reviewer
    review_questions: List[str] = Field(default_factory=list)
    reviewer_feedback: Optional[str] = None
    review_approved: Optional[bool] = None

    # --- Resume context ---
    resume_hint: Optional[str] = None          # Short summary injected into system-reminder on resume

    # --- Version chain ---
    previous_checkpoint_id: Optional[PydanticObjectId] = None

    class Settings:
        name = "qa_phase_checkpoints"
        indexes = [
            [("session_id", 1), ("phase_index", -1)],
            [("session_id", 1), ("status", 1)],
        ]

    @classmethod
    async def find_latest(cls, session_id: str) -> Optional["QaPhaseCheckpoint"]:
        """Return the most recent checkpoint for a session (highest phase_index)."""
        return await cls.find(
            cls.session_id == session_id
        ).sort(-cls.phase_index).first_or_none()


# ---------------------------------------------------------------------------
# qa_agent_db.qa_artifact_versions  (task 1.3 + 1.4)
# ---------------------------------------------------------------------------

class ArtifactType(str, Enum):
    """Supported artifact types produced by QA Skill phases."""
    DIFF_ANALYSIS = "diff_analysis"
    TEST_CASES = "test_cases"
    AUTOMATION_SCRIPT = "automation_script"
    TEST_REPORT = "test_report"


class QaArtifactVersion(Document):
    """Versioned artifact produced by a Skill phase.

    Each review cycle that modifies the artifact creates a new version,
    forming a linked chain via previous_version_id.

    Collection: qa_agent_db.qa_artifact_versions
    """

    # --- Identity ---
    task_id: Optional[str] = None
    session_id: str
    phase: str
    artifact_type: str                          # ArtifactType value or custom string
    version: int = 1

    # --- Content ---
    content: Dict[str, Any] = Field(default_factory=dict)
    content_summary: str = ""                   # ≤200 tokens; injected into Worker context

    # --- Provenance ---
    created_by: str = "llm"                     # "llm" | "human_edit"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # --- Review state ---
    review_status: str = "pending"              # "pending" | "approved" | "rejected" | "modified"
    review_comment: Optional[str] = None

    # --- Version chain ---
    previous_version_id: Optional[PydanticObjectId] = None

    class Settings:
        name = "qa_artifact_versions"
        indexes = [
            [("session_id", 1), ("artifact_type", 1), ("version", -1)],
            [("session_id", 1), ("phase", 1)],
        ]


# ---------------------------------------------------------------------------
# qa_agent_db.coordinator_workers  (coordinator session restore)
# ---------------------------------------------------------------------------

class CoordinatorWorker(Document):
    """Persisted Worker entry for a Coordinator session.

    Written by WorkerPool.register() and WorkerPool.update_status() whenever
    the pool is operating under a Coordinator session (active_coordinator_session_id
    is set).  Used to reconstruct the WorkerPool on session restore.

    result is truncated to 10,000 characters at write time — the full tool
    return value is already stored in agno_sessions; this field is for quick
    status display via get_worker_status.

    Collection: qa_agent_db.coordinator_workers
    """

    # --- Identity ---
    coordinator_session_id: str          # Owning Coordinator's Agno session_id
    worker_id: str                       # e.g. "w-abc123"
    agent_name: str                      # Agent registry name
    description: str = ""               # Short human-readable task description
    status: str = "creating"            # WorkerStatus.value

    # --- Session ---
    session_id: str = ""                # Worker's own Agno session_id ("worker__w-xxx")

    # --- Timing ---
    created_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None

    # --- Result ---
    result: Optional[str] = None        # Truncated to 10 000 chars
    error: Optional[str] = None

    # --- Usage stats ---
    turns_used: int = 0
    tools_used: List[str] = Field(default_factory=list)

    # --- Artifact links ---
    context_ids: List[str] = Field(default_factory=list)
    output_artifact_id: Optional[str] = None

    class Settings:
        name = "coordinator_workers"
        indexes = [
            [("coordinator_session_id", 1)],   # query all workers for a coordinator
            [("worker_id", 1)],                 # single-worker lookup
        ]


# ---------------------------------------------------------------------------
# qa_agent_db.session_events  (persistent event store)
# ---------------------------------------------------------------------------

class SessionEvent(Document):
    """Persisted event from an agent run's streaming output.

    Written asynchronously by PersistentEventStore.append() during agent
    execution.  Used to recover in-progress run messages when the frontend
    reconnects (page refresh, crash, abort).  Cleaned up after a run
    completes successfully.

    Collection: qa_agent_db.session_events
    """

    session_id: str
    seq_id: int
    event_type: str                              # "token" | "tool_start" | "tool_end" | ...
    agent_name: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)   # full event dict from stream_adapter
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "session_events"
        indexes = [
            [("session_id", 1), ("seq_id", 1)],   # unique compound for ordered replay
            "session_id",                           # simple index for batch delete
        ]


# ---------------------------------------------------------------------------
# qa_agent_db.session_seq_watermarks
# ---------------------------------------------------------------------------

class SessionSeqWatermark(Document):
    """Per-session event seq counter watermark.

    Written by PersistentEventStore.clear_persisted() when a run finishes
    (or logs are cleaned up) so a restarted process can reseed the session's
    seq counter above every previously issued seq_id — keeping seq_id
    monotonic across runs AND backend restarts.  Without it, a restart
    resets the counter and the WS live-dedup (``seq <= last_event_id``)
    silently drops an entire new run's events for clients holding a stale
    high-water mark.

    Collection: qa_agent_db.session_seq_watermarks
    Unique key: session_id (single-writer upsert, plain index like WorkspaceRecord)
    """

    session_id: str
    next_seq: int = 1

    class Settings:
        name = "session_seq_watermarks"
        indexes = ["session_id"]


# ---------------------------------------------------------------------------
# qa_agent_db.user_skills  (用户上传 skill 包)
# ---------------------------------------------------------------------------

class UserSkill(Document):
    """用例生成管线 skill 持久化记录（内置 + 用户上传统一表）。

    _id = f"{owner_email}:{name}" 复合键（幂等替换，同名重传覆盖）。
    Collection: qa_agent_db.user_skills
    zip_data 存原始 zip 字节（BSON Binary），物化到 ~/.qa-agent/skills/<name>/ 后
    供 SkillLoader 扫描加载。DB 是 skill 真相源，本地物化是适配层。

    source 字段区分：
    - "builtin": 内置 skill（owner_email="system"，zip_data=b""，本地 .claude/skills/ 已存在，不物化）
    - "user": 用户上传 skill（owner_email=用户 email，zip_data 非空，物化到 ~/.qa-agent/skills/）

    用例生成页 skill 下拉读 GET /skills/testcase-gen 聚合返回本表记录。
    """
    id: Optional[str] = Field(default=None, alias="_id")  # str composite key
    owner_email: str                             # "system" 表内置；用户 email 表用户上传
    name: str
    zip_data: bytes = b""                        # BSON Binary,原始 zip 字节；内置为空（本地已有）
    agent: str                                   # 绑定 agent_id（frontmatter agent 字段）
    description: str = ""
    paradigm: str = ""                           # 测试范式：combat_bd / task_trigger / ...
    source: str = "user"                         # "builtin" | "user"
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "user_skills"
        indexes = [
            [("owner_email", 1), ("name", 1)],
            "source",
        ]

