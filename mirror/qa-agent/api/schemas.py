"""
QA Agent System — API Schemas

All Pydantic request/response models for the FastAPI service.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SessionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    INTERRUPT_PENDING = "interrupt_pending"
    COMPLETED = "completed"
    FAILED = "failed"
    INACTIVE = "inactive"
    ABORTED = "aborted"


class AgentMode(str, Enum):
    NORMAL = "normal"
    COORDINATOR = "coordinator"


# ---------------------------------------------------------------------------
# Auth / User models
# ---------------------------------------------------------------------------

class SessionMeta(BaseModel):
    """Metadata for a historical session stored in user_sessions."""
    session_id: str
    agent_name: str
    mode: str
    game_version: str = ""
    module: str = ""
    title: str = ""
    created_at: float = 0.0
    last_active_at: float = 0.0
    status: str = "inactive"
    hidden: bool = False


class UpdateSessionRequest(BaseModel):
    """Request body for PATCH /users/{email}/sessions/{session_id}."""
    title: str = Field(..., description="New session title", max_length=256)


class UpdateSessionVisibilityRequest(BaseModel):
    """Request body for PUT /users/{email}/sessions/{session_id}/visibility."""
    hidden: bool = Field(..., description="True to hide, False to unhide")


class UserSessionsResponse(BaseModel):
    """Response for GET /users/{email}/sessions."""
    sessions: List[SessionMeta]


class MessageRecord(BaseModel):
    """A single message in a session's conversation history."""
    id: Optional[str] = None             # dynamic message ID (e.g. "msg_0", "msg_1")
    role: str  # "user" | "assistant" | "tool"
    content: Optional[str] = None
    created_at: float = 0.0
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None  # present when role='tool', links result to tool_calls entry
    name: Optional[str] = None          # agent name (assistant) or tool name (tool)
    # LLM thinking content persisted on the assistant message (reasoning_content +
    # redacted_reasoning_content merged); None = no thinking (old data / non-reasoning model)
    reasoning_content: Optional[str] = None


class PageMeta(BaseModel):
    """分页页元数据。

    附着于 GET /messages 分页响应与 GET /messages/tail 尾页响应。
    """
    has_more: bool = Field(..., description="是否存在更早的历史页")
    next_before: int = Field(..., description="下一页游标（本页最老 run 的下标，作为下次请求的 before）")
    total_runs: int = Field(..., description="history_version：存储 run 总数")
    total_messages: int = Field(..., description="history_version：存储消息总数")
    anchor: Optional[str] = Field(default=None, description="本页最老 run 的锚标识（run_id 或退化形式），下次翻页请求携带")


class SessionMessagesResponse(BaseModel):
    """Response for GET /sessions/{session_id}/messages (及 /messages/tail)。

    无分页参数的请求保持原结构（page_meta=None, resync=False）。
    """
    session_id: str
    messages: List[MessageRecord]
    page_meta: Optional[PageMeta] = Field(default=None, description="分页元数据（仅分页/尾页请求返回）")
    resync: bool = Field(default=False, description="锚校验失败信号（D3 ABA 防护）：客户端应丢弃旧页重拉尾页")


class RestoreSessionResponse(BaseModel):
    """Response for POST /users/{email}/sessions/{session_id}/restore."""
    session_id: str
    status: str
    already_active: bool
    message_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ModelSlotOverride(BaseModel):
    """Session-level override for a model slot. Only specified fields
    are overridden; unspecified fields fall back to .env / global defaults."""
    model: Optional[str] = Field(default=None, description="Model identifier override")
    base_url: Optional[str] = Field(default=None, description="API base URL override")
    api_key: Optional[str] = Field(default=None, description="API key override")


# Type alias: model_slots values can be a string (model name shorthand)
# or a full ModelSlotOverride object.
# e.g. {"orchestrate": "deepseek-flash"} or {"orchestrate": {"model": "deepseek-flash"}}
ModelSlotValue = Union[str, ModelSlotOverride]


class CreateSessionRequest(BaseModel):
    agent_name: str = Field(
        default="general-agent",
        description="Name of the agent to use (from registry)",
    )
    mode: AgentMode = Field(
        default=AgentMode.NORMAL,
        description="Execution mode: normal or coordinator",
    )
    worker_agent_names: Optional[List[str]] = Field(
        default=None,
        description="Worker agent names for coordinator mode",
    )
    permission_mode: Optional[str] = Field(
        default=None,
        description="Permission mode override: default | plan | bypass",
    )
    game_version: str = Field(default="", description="Game version context")
    module: str = Field(default="", description="Game module context")
    model_slots: Optional[Dict[str, ModelSlotValue]] = Field(
        default=None,
        description=(
            "Session-level model slot overrides. Keys are slot names "
            "(default, orchestrate, reason, vision). "
            'Value can be a model name string or an object. Examples: '
            '{"orchestrate": "deepseek-flash"} or '
            '{"orchestrate": {"model": "deepseek-flash", "base_url": "..."}}'
        ),
    )


class WorkspaceContext(BaseModel):
    """Workspace context attached to a message — paths selected in the UI tree."""
    selected_paths: List[str] = Field(
        ...,
        description="Paths selected in workspace tree (relative to workspace root)",
    )


class UploadedFileRef(BaseModel):
    """Reference to a file uploaded via POST /sessions/{sid}/uploads.

    Carried in SendMessageRequest.uploaded_files so that send_message can
    inject a [用户上传文件] block into the agent prompt (path injection
    pattern, mirroring workspace_context). The abs_path MUST be present
    in session_state.uploads for the injection to take effect.
    """
    file_id: str
    name: str
    abs_path: str


class SendMessageRequest(BaseModel):
    content: str = Field(..., description="Message content to send to the agent")
    stream: bool = Field(default=True, description="Whether to stream the response")
    workspace_context: Optional[WorkspaceContext] = Field(
        default=None,
        description="Workspace context with selected file/directory paths",
    )
    activate_skills: Optional[List[str]] = Field(
        default=None,
        description="List of on-demand Skill names to activate for this message",
    )
    model_override: Optional[str] = Field(
        default=None,
        description=(
            "Override the default slot model for this message's agent run. "
            "Must be a value from the server's available-models list. "
            "Only affects the 'default' slot; orchestrate/reason slots are unchanged."
        ),
    )
    uploaded_files: Optional[List["UploadedFileRef"]] = Field(
        default=None,
        description=(
            "User-uploaded file references (from POST /sessions/{sid}/uploads). "
            "When non-empty, send_message injects a [用户上传文件] block into "
            "user_content listing each file's abs_path, and the agent reads them "
            "via file_read. Empty/None = no upload context, behavior unchanged. "
            "Entries with abs_path not in session_state.uploads are skipped + warned."
        ),
    )


class ReviewRequest(BaseModel):
    action: str = Field(
        ...,
        description="Review action: confirm | cancel | modify | pass | fail | rerun | investigate",
    )
    modified_content: Optional[str] = Field(
        default=None,
        description="Modified content (used when action=modify)",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional reviewer notes",
    )
    confirmed_by: Optional[str] = Field(
        default=None,
        description="Reviewer identifier (for audit trail)",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CreateSessionResponse(BaseModel):
    session_id: str
    status: SessionStatus
    agent_name: str
    mode: str


class SendMessageResponse(BaseModel):
    message_id: str
    session_id: str
    status: str = "accepted"


class InterruptPayload(BaseModel):
    interrupt_id: str
    interrupt_type: str
    payload: Dict[str, Any]
    created_at: float
    age_seconds: float
    actions: List[str]


class SessionStatusResponse(BaseModel):
    session_id: str
    status: SessionStatus
    agent_name: str
    mode: str
    game_version: str
    module: str
    interrupt_payload: Optional[InterruptPayload] = None
    error: Optional[str] = None
    restorable: Optional[bool] = None


class UsageMetrics(BaseModel):
    """Token consumption metrics for a run (extracted from Agno RunOutput.metrics).

    Mirrors Agno ``agno.models.metrics.Metrics`` subset surfaced to the frontend.
    All optional fields are None-safe: a gateway may omit usage payloads,
    in which case the UI shows "—" instead of misleading zeros.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cost: Optional[float] = None
    duration_s: Optional[float] = None


class RunUsageEntry(BaseModel):
    """Per-run token consumption entry (one row in the frontend usage table)."""

    agent_name: str = ""
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    created_at: Optional[float] = None


class ContextUsageResponse(BaseModel):
    """Response for GET /sessions/{session_id}/context-usage.

    ``categories`` keys: system_prompt / tools / mcp_tools / messages / free_space.
    ``segments`` carries system-prompt sub-section breakdown (active sessions
    only — None when the agent instance is not in memory).
    ``per_agent`` groups usage_totals by agent_name (multi-agent sessions).
    ``recent_runs`` carries the last N run-level entries for the per-turn
    usage table (newest last).
    """

    session_id: str
    max_context_tokens: int
    categories: Dict[str, Optional[int]]
    segments: Optional[Dict[str, int]] = None
    usage_totals: UsageMetrics = Field(default_factory=UsageMetrics)
    per_agent: Optional[Dict[str, UsageMetrics]] = None
    recent_runs: List[RunUsageEntry] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class LastSeqResponse(BaseModel):
    """Response for GET /sessions/{session_id}/stream/last-seq — lightweight probe
    used by the frontend staleness detector to compare backend event progress
    against the local ``lastSeqId`` without disturbing the existing WS.
    """

    session_id: str
    last_seq_id: int = Field(default=0, description="Latest event seq_id known to backend (memory or storage)")
    status: SessionStatus = Field(default=SessionStatus.IDLE, description="Current session status as seen by backend")
    # History version probe fields
    total_runs: Optional[int] = Field(default=None, description="History version: run count in storage (None = unavailable)")
    total_messages: Optional[int] = Field(default=None, description="History version: message count in storage (None = unavailable)")


class IdleStatusResponse(BaseModel):
    """Response for GET /api/idle — system-level idle probe for update coordination."""
    idle: bool = Field(..., description="True when no in-memory session is RUNNING or INTERRUPT_PENDING")
    running_sessions: int = Field(..., description="Count of sessions in RUNNING or INTERRUPT_PENDING status")
    running_session_ids: List[str] = Field(
        default_factory=list,
        description="IDs of sessions currently in RUNNING or INTERRUPT_PENDING status",
    )


class HistoryVersion(BaseModel):
    """Storage-derived history version tuple.

    离线收敛: history_version 元组。
    Strictly monotonic under truncate (total_messages decreases) and run completion
    (both increase), so any fetch-time comparison detects divergence.
    """
    total_runs: int = Field(..., description="Number of runs persisted in storage")
    total_messages: int = Field(..., description="Number of messages persisted across all runs")


class TruncateMessagesRequest(BaseModel):
    """Request body for POST /sessions/{session_id}/messages/truncate."""
    message_id: str = Field(..., description="ID of the message to truncate from (inclusive)")
    # Truncate idempotency protocol
    request_id: Optional[str] = Field(
        default=None,
        description="Client-generated idempotency key; same id replay returns cached outcome",
    )


class TruncateMessagesResponse(BaseModel):
    """Response for POST /sessions/{session_id}/messages/truncate."""
    truncated_count: int = Field(..., description="Number of messages deleted")
    remaining_count: int = Field(..., description="Number of messages remaining after truncation")
    remaining_messages: List[MessageRecord] = Field(
        default_factory=list,
        description="Complete list of messages remaining after truncation",
    )
    request_id: Optional[str] = Field(default=None, description="Echo of the request_id, if provided")
    history_version: Optional[HistoryVersion] = Field(
        default=None,
        description="Post-truncation history version (None for legacy responses)",
    )


class ReviewResponse(BaseModel):
    session_id: str
    action: str
    resolved: bool


class RegenerateTurnRequest(BaseModel):
    """Request body for POST /sessions/{session_id}/turns/regenerate.

    轮锚三级解析，无内容匹配。
    """
    message_id: Optional[str] = Field(
        default=None,
        description="该轮用户消息的 backend message id（hydrated 卡片直传，零网络定位）",
    )
    turn_last: bool = Field(
        default=False,
        description="turn: last 锚 — 服务器侧解析最后一个 user 消息（最新 live 轮主路径）",
    )
    # 复用 D13 truncate 幂等槽约定（last_truncate_outcome 同款）
    request_id: Optional[str] = Field(
        default=None,
        description="Client-generated idempotency key; same id replay returns cached outcome",
    )


class RegenerateTurnResponse(BaseModel):
    """Response for POST /sessions/{session_id}/turns/regenerate."""
    request_id: Optional[str] = Field(default=None, description="Echo of the request_id, if provided")
    truncated_count: int = Field(..., description="Number of messages deleted by the turn cut")
    remaining_count: int = Field(..., description="Number of messages remaining after the cut (含该轮 U)")
    history_version: Optional[HistoryVersion] = Field(
        default=None,
        description="Post-cut history version (同 truncate 约定，供离线收敛)",
    )


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    milvus: str  # "connected" | "disconnected" | "disabled"
    storage: str  # "ok" | "error"
    version: str = ""


class ConfigResponse(BaseModel):
    llm_model: str
    llm_base_url: str
    storage_backend: str
    milvus_enabled: bool
    milvus_host: str
    default_max_turns: int
    default_permission_mode: str
    project_skills_dir: str
    mcp_config_path: str


class ModelInfo(BaseModel):
    """Info about one available model for UI selection.

    ``supports_vision`` is computed by the server from
    ``settings.llm_vision_models_set`` and is used by the frontend
    FileUploader to gate image file upload.
    """
    name: str
    supports_vision: bool


class AvailableModelsResponse(BaseModel):
    models: list[ModelInfo]
    default: str


class SkillInfo(BaseModel):
    name: str
    tool_name: str
    description: str
    when_to_use: str
    tools: List[str]
    args: List[str]
    effort: int
    agent: Optional[str] = None
    load_mode: str = "on-demand"
    summary: str = ""


class AgentInfo(BaseModel):
    id: str
    name: str
    description: str
    when_to_use: str
    tools: List[str]
    permission_mode: str
    agent_type: str
    tags: List[str]
    inject_history: bool


# ---------------------------------------------------------------------------
# Workspace models
# ---------------------------------------------------------------------------

class TreeEntry(BaseModel):
    """A single entry in a workspace directory tree."""
    name: str = Field(..., description="File or directory name")
    type: str = Field(..., description="Entry type: 'file' or 'dir'")
    path: str = Field(..., description="Path relative to workspace root")
    size: Optional[int] = Field(default=None, description="File size in bytes (files only)")
    has_children: Optional[bool] = Field(default=None, description="Has child entries (dirs only)")


class SetWorkspaceRequest(BaseModel):
    """Request body for PUT /sessions/{sid}/workspace."""
    root_path: str = Field(..., description="Absolute path to workspace root directory")


class WorkspaceResponse(BaseModel):
    """Response for workspace operations that return tree data."""
    root_path: Optional[str] = Field(default=None, description="Workspace root path, null if not set")
    tree: List[TreeEntry] = Field(default_factory=list)


class TreeResponse(BaseModel):
    """Response for lazy-loaded directory tree expansion."""
    entries: List[TreeEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# MCP management models
# ---------------------------------------------------------------------------

class MCPToolInfo(BaseModel):
    """Information about a single tool provided by an MCP server."""
    name: str
    description: str = ""
    permission_level: str = "L1"  # "L1" | "L2"


class MCPServerInfo(BaseModel):
    """Status and details of a single MCP server."""
    name: str
    transport: str = "stdio"  # "stdio" | "sse"
    enabled: bool = False
    status: str = "disconnected"  # connected | disconnected | error
    tool_count: int = 0
    tools: List[MCPToolInfo] = Field(default_factory=list)
    error: Optional[str] = None
    connected_at: Optional[float] = None
    config: Optional[Dict[str, Any]] = None


class MCPServersResponse(BaseModel):
    """Response for GET /mcp/servers."""
    servers: List[MCPServerInfo]


class AddMCPServerRequest(BaseModel):
    """Request body for POST /mcp/servers."""
    name: str = Field(..., description="Unique server name")
    transport: str = Field(default="stdio", description="Transport type: stdio or sse")
    command: Optional[str] = Field(default=None, description="Command for stdio transport")
    args: Optional[List[str]] = Field(default=None, description="Args for stdio transport")
    env: Optional[Dict[str, str]] = Field(default=None, description="Env vars for stdio transport")
    url: Optional[str] = Field(default=None, description="URL for SSE transport")
    enabled: bool = Field(default=True, description="Whether to auto-connect after adding")


class UpdateMCPServerRequest(BaseModel):
    """Request body for PUT /mcp/servers/{name}."""
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    url: Optional[str] = None


class MCPConfigResponse(BaseModel):
    """Response for GET /mcp/config — full mcp.json content."""
    servers: Dict[str, Any]


class UpdateMCPConfigRequest(BaseModel):
    """Request body for PUT /mcp/config — replace entire mcp.json."""
    servers: Dict[str, Any]
