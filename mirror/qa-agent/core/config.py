"""
QA Agent System — Centralized Configuration

Uses pydantic-settings to load configuration from environment variables
and .env files. All settings have sensible defaults for local development.
"""

from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_file() -> str:
    """Resolve the .env file path, frozen-aware.

    Frozen (PyInstaller): .env is looked up NEXT TO the executable first — it is
    not bundled into the build, so credentials never end up inside the binary.
    The ``_MEIPASS`` directory is still checked as a second candidate, for
    bundles that were built with ``--add-data .env``.
    Development: resolve relative to this file's parent (qa-agent/).
    """
    if getattr(sys, "frozen", False):
        # Frozen: exe-adjacent first, then the extracted bundle dir
        candidates = [
            Path(sys.executable).resolve().parent / ".env",
            Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / ".env",
        ]
    else:
        # Dev: qa-agent/.env (this file is at qa-agent/core/config.py)
        candidates = [
            Path(__file__).resolve().parent.parent / ".env",
        ]

    for p in candidates:
        if p.exists():
            return str(p)

    # Fallback: let pydantic-settings try cwd (may not find it — that's OK,
    # env vars will still be used)
    return ".env"


class PermissionMode(str, Enum):
    """Permission mode for L2 tool execution."""
    DEFAULT = "default"   # Normal: pause and ask user
    PLAN = "plan"         # Show execution plan, require explicit confirm
    BYPASS = "bypass"     # Skip all L2 confirmations (fully automatic)


class StorageBackend(str, Enum):
    """Supported storage backends for Agent session persistence."""
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    MONGO = "mongo"


class QAAgentSettings(BaseSettings):
    """Master configuration for the QA Agent system."""

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- LLM Provider ----
    # Any endpoint speaking the OpenAI chat-completions protocol works.
    # Shipped default: the DeepSeek official API.
    llm_base_url: str = Field(
        default="https://api.deepseek.com",
        description="OpenAI-compatible API base URL",
    )
    llm_api_key: str = Field(
        default="",
        description="API key for the LLM provider",
    )
    llm_model: str = Field(
        default="deepseek-flash",
        description="Model identifier",
    )

    # ---- Embedding ----
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_base_url: Optional[str] = Field(default=None)
    embedding_api_key: Optional[str] = Field(default=None)

    # ---- Milvus ----
    milvus_host: str = Field(default="localhost")
    milvus_port: int = Field(default=19530)
    # Off by default so a fresh clone runs with no external services: without
    # Milvus the system degrades to "no long-term memory" and everything else
    # still works. Turn it on once a Milvus instance is reachable.
    milvus_enabled: bool = Field(default=False)

    # ---- Storage ----
    storage_backend: StorageBackend = Field(default=StorageBackend.MONGO)
    sqlite_path: str = Field(default="./data/qa_agent.db")
    storage_dsn: str = Field(
        default="",
        description=(
            "PostgreSQL DSN (used when storage_backend=postgres). Deliberately "
            "has no default: shipping a password literal — even a dev one — "
            "invites reuse in real deployments. The storage factory refuses to "
            "build a Postgres backend while this is empty."
        ),
    )
    agno_mongo_session_collection: str = Field(
        default="agno_sessions",
        description="MongoDB collection name for Agno session persistence",
    )
    agno_mongo_memory_collection: str = Field(
        default="agno_memories",
        description="MongoDB collection name for Agno user memories",
    )

    # ---- Memory / Embedding payload caps ----
    # Character cap on text_content passed to the embedder in memory.writer.
    # Prevents a single large tool result (e.g. a 100 KB read_file) from
    # exhausting the embedding provider's TPM budget. Payload truncation is
    # mandatory: the cap must be > 0.
    memory_max_embed_chars: int = Field(
        default=8000,
        description=(
            "Character cap on text_content passed to the embedder in "
            "memory.writer; must be > 0"
        ),
    )

    @field_validator("memory_max_embed_chars")
    @classmethod
    def _validate_memory_max_embed_chars(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(
                "memory_max_embed_chars must be > 0 — embedding-payload "
                "truncation is mandatory per memory-write-reliability spec. "
                "Set a large number (e.g. 1_000_000) to effectively disable "
                "rather than 0/negative."
            )
        return v

    @field_validator("debug", mode="before")
    @classmethod
    def _coerce_debug(cls, v):
        """容忍非布尔输入: 系统 env DEBUG 可能被外部工具注入
        'release'/'production' 等 Node.js 惯例字符串。pydantic 严格 bool
        解析会让整个 QAAgentSettings 单例在 import 阶段崩,连带所有依赖
        settings 的下游模块全炸。
        release/production/false/0/空/no/off → False;
        debug/true/1/yes/on/dev → True; 其他无法判断的 → False。
        """
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "on", "debug", "dev"):
            return True
        return False

    # ---- Context Thresholds ----
    # Calibrated for 1M-token-context models following the "below 40% usage =
    # best model quality" principle. soft fires at the sweet-zone boundary
    # (400k) to archive state for the next session; hard fires in the degraded
    # zone (600k) for aggressive distillation.
    # NOTE: L4 distiller archives to Milvus but does NOT truncate the current
    # session. In-session size is bounded by L1 (num_history_runs) + L2
    # (CompressionManager) + L3 (SessionSummary), not by these thresholds.
    context_soft_threshold: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Soft compression threshold — archive at sweet-zone boundary (40% of max context)",
    )
    context_hard_threshold: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Hard compression threshold — aggressive archive in degraded zone (60% of max context)",
    )
    max_context_tokens: int = Field(
        default=1_000_000,
        description="Maximum context window size in tokens (matches the 1M-token window of current flagship models)",
    )

    # ---- Agent Defaults ----
    default_max_turns: int = Field(default=25)
    # Number of prior runs (each = one arun()) whose messages Agno injects
    # into context. Distinct from default_max_turns, which caps agentic loop
    # iterations WITHIN a single arun(). Low value prevents context bloat
    # for long sessions; older runs are replaced by <summary_of_previous_interactions>.
    num_history_runs: int = Field(
        default=5,
        ge=0,
        description="Number of prior runs to inject into context per arun()",
    )

    # ---- Context Compression (Agno CompressionManager — in-run) ----
    # Distinct from cross-session Milvus distiller (hooks/context_threshold_hook),
    # which fires post-run. CompressionManager compresses verbose tool results
    # DURING arun() between tool calls, preventing single-run context explosion.
    compress_tool_results: bool = Field(
        default=True,
        description="Enable Agno native in-run tool result compression",
    )
    compress_token_limit: int = Field(
        default=500_000,
        ge=1000,
        description=(
            "Token threshold triggering in-run compression (counts messages + tool defs "
            "+ output schema). Safety net at ~50% of the 1M-token context window now "
            "standard on mainstream models; "
            "L4 distiller soft threshold (40% of max_context_tokens) fires first, this "
            "only catches runaway contexts that slipped past L2's primary trigger."
        ),
    )
    compress_tool_results_token_limit: int = Field(
        default=100_000,
        ge=500,
        description=(
            "Token-sum threshold for in-run compression — fires when the "
            "cumulative token count of currently-uncompressed tool RESULT "
            "messages crosses this limit. Uses ToolResultTokenCompressionManager "
            "(core/compression.py) which overrides Agno's should_compress to "
            "count ONLY role='tool' messages (not total context). Small tool "
            "results (file_edit 'OK' ≈ 50 tokens) contribute negligible tokens "
            "and never trip the threshold alone. Tuned for 1M-token context "
            "windows: 100_000 ≈ 10% of the window, "
            "≈ 60 typical large tool results (~1700 tok each) or 4 large file_read "
            "(~25K tok each) — compression now fires only on genuinely massive "
            "accumulation instead of normal usage."
        ),
    )
    compression_model_slot: str = Field(
        default="slot:default",
        description="Model slot for compression LLM; ORCHESTRATE = cheap & instruction-following. Falls back to global default if env slot not configured.",
    )

    default_permission_mode: PermissionMode = Field(default=PermissionMode.DEFAULT)

    # ---- API Server ----
    # Env keys are API_HOST / API_PORT: with an explicit alias, that alias is
    # the name pydantic-settings reads from the environment and the field name
    # is NOT consulted. Duplicate fields for the same key would be a trap.
    server_host: str = Field(default="0.0.0.0", alias="api_host")
    server_port: int = Field(default=8000, alias="api_port")
    interrupt_timeout_minutes: int = Field(default=30)
    
    # ---- Logging ----
    log_level: str = Field(default="info")
    # Tolerant bool: 系统 env DEBUG 可能被外部工具注入非布尔字符串
    # (如 Node.js 惯例 DEBUG=release/production)。pydantic 严格 bool 解析
    # 会在此炸掉整个 QAAgentSettings 单例 → 所有依赖 settings 的代码全崩。
    # release/production/false/0/空/no/off → False；debug/true/1/yes/on/dev → True。
    debug: bool = Field(default=False)

    # ---- Tracing (OpenTelemetry) ----
    tracing_enabled: bool = Field(
        default=True,
        description="Enable Agno OpenTelemetry tracing",
    )
    tracing_max_queue_size: int = Field(
        default=2048,
        description="SpanProcessor max queue size",
    )
    tracing_max_batch_size: int = Field(
        default=512,
        description="BatchSpanProcessor max export batch size",
    )
    tracing_flush_interval_ms: int = Field(
        default=5000,
        description="BatchSpanProcessor schedule delay in milliseconds",
    )

    # ---- Skill Directories ----
    project_skills_dir: str = Field(default=".claude/skills")
    user_skills_dir: str = Field(default="~/.qa-agent/skills")

    # ---- User Skill Startup Sync ----
    # Used by lifespan to filter user_skills DB records for materialization on startup.
    # None → skip sync (rely on upload-time materialization only).
    # Set via env var QA_AGENT_CURRENT_USER_EMAIL for packaged deployments.
    current_user_email: Optional[str] = Field(
        default=None,
        description="当前用户 email，用于启动期 user_skills DB 同步过滤（None 跳过同步）",
    )

    # ---- MCP ----
    # Path to an MCP server config JSON. Empty (default) → no MCP servers are
    # discovered at startup and tools come only from the builtin registry. A
    # configured-but-missing file degrades the same way (logged, no servers).
    mcp_config_path: str = Field(
        default="",
        description=(
            "Path to an MCP server config JSON ('servers' or 'mcpServers' key). "
            "Empty → MCP disabled (builtin tools only)."
        ),
    )

    # ---- Version ----
    app_version: str = Field(
        default="0.1.0",
        description="Version string shown in /health and frontend system status",
    )

    # ---- Model Slots ----
    # Named model slots for role-based model assignment.
    # Only override the fields that differ from the global LLM defaults above.
    # Example: LLM_SLOT_ORCHESTRATE_MODEL=deepseek-flash
    llm_slot_orchestrate_model: Optional[str] = Field(
        default=None, description="Model for orchestrate slot (Coordinator Leader)")
    llm_slot_orchestrate_base_url: Optional[str] = Field(
        default=None, description="Base URL for orchestrate slot")
    llm_slot_orchestrate_api_key: Optional[str] = Field(
        default=None, description="API key for orchestrate slot")

    llm_slot_reason_model: Optional[str] = Field(
        default=None, description="Model for reason slot (deep-reasoning workers)")
    llm_slot_reason_base_url: Optional[str] = Field(
        default=None, description="Base URL for reason slot")
    llm_slot_reason_api_key: Optional[str] = Field(
        default=None, description="API key for reason slot")

    # Multimodal vision model for image-to-text conversion.
    # Should point to a vision-capable model reachable on the configured
    # endpoint. Used by core.vision_client when user uploads images via session
    # uploads.
    llm_slot_vision_model: Optional[str] = Field(
        default=None, description="Model for vision slot (image-to-text, multimodal)")
    llm_slot_vision_base_url: Optional[str] = Field(
        default=None, description="Base URL for vision slot")
    llm_slot_vision_api_key: Optional[str] = Field(
        default=None, description="API key for vision slot")

    # ---- Available Models (UI selector) ----
    # Comma-separated list of model IDs available for user selection in the UI.
    # If empty, falls back to [llm_model] (the global default only).
    # Example: LLM_AVAILABLE_MODELS=deepseek-flash,deepseek-v4-pro
    llm_available_models: str = Field(
        default="",
        description=(
            "Comma-separated list of model IDs available for user selection. "
            "Falls back to [llm_model] if empty."
        ),
    )

    # ---- Available Models (UI selector) ----
    # Models that support image input (vision/multimodal). Comma-separated IDs.
    # Frontend gates image upload on this whitelist: a model not in this set
    # will refuse image file selection in FileUploader.
    # Empty by default — the operator opts in with their own vision model IDs.
    # Example: LLM_VISION_MODELS=<vision-model-a>,<vision-model-b>
    llm_vision_models: str = Field(
        default="",
        description=(
            "Comma-separated list of model IDs that support image input "
            "(vision/multimodal). Used by GET /config/available-models to mark "
            "supports_vision, and by the frontend FileUploader to gate image upload."
        ),
    )

    # ---- File upload limits ----
    # Max single-file size in MB for POST /sessions/{sid}/uploads.
    upload_max_size_mb: float = Field(
        default=1.0,
        description="Max single-file upload size in MB for session uploads.",
    )

    # ---- MongoDB ----
    mongo_uri: str = Field(
        default="mongodb://localhost:27017/",
        description="MongoDB connection URI",
    )

    # ---- Auth ----
    # Session signing key — no default on purpose. A predictable key would make
    # every session cookie forgeable, and the login callback is the only gate.
    session_secret_key: str = Field(
        default="",
        description=(
            "Secret key for Starlette SessionMiddleware (cookie signing). "
            "Required — the process refuses to start when empty."
        ),
    )
    # ---- Login provider (OAuth 2.0 / OIDC authorization code flow) ----
    # Provider-agnostic: point these at any standards-compliant provider
    # (GitHub, Google, Keycloak, Auth0, ...). While any of the three endpoint
    # URLs or the client id is empty, the login routes answer 503 and only
    # pre-authenticated sessions can be used.
    oauth_authorize_url: str = Field(
        default="",
        description=(
            "Authorization endpoint the browser is redirected to "
            "(e.g. https://accounts.google.com/o/oauth2/v2/auth)."
        ),
    )
    oauth_token_url: str = Field(
        default="",
        description=(
            "Token endpoint used server-side to exchange the authorization "
            "code (e.g. https://oauth2.googleapis.com/token)."
        ),
    )
    oauth_userinfo_url: str = Field(
        default="",
        description=(
            "Userinfo endpoint returning the authenticated user's profile as "
            "JSON (e.g. https://openidconnect.googleapis.com/v1/userinfo)."
        ),
    )
    oauth_client_id: str = Field(
        default="",
        description="OAuth client id issued by the provider.",
    )
    oauth_client_secret: str = Field(
        default="",
        description=(
            "OAuth client secret. Used only for the server-side code exchange "
            "and never persisted in the session cookie."
        ),
    )
    oauth_scope: str = Field(
        default="openid email profile",
        description="Space-separated scopes requested at the authorize endpoint.",
    )
    oauth_pkce_enabled: bool = Field(
        default=True,
        description=(
            "Send a PKCE code_challenge (S256) with the authorization request. "
            "Providers that do not support PKCE (e.g. GitHub OAuth apps) need "
            "this set to false."
        ),
    )
    frontend_url: str = Field(
        default="/",
        description="Frontend URL for post-login redirect (use '/' when the UI is served by this process)",
    )

    # ---- AgentOS adapter ----
    # AgentOS is Agno's own agent runtime protocol (``agno.os``), so the adapter
    # speaks it verbatim. Some callers deliver user_id as "<prefix><id>"; that
    # prefix is caller-side configuration. Empty → pass user_id through.
    agent_os_user_id_prefix: str = Field(
        default="",
        description=(
            "Prefix the calling platform prepends to user_id "
            "(e.g. '<tenant>_'). Empty → no stripping."
        ),
    )

    @field_validator("session_secret_key")
    @classmethod
    def _require_session_secret_key(cls, v: str) -> str:
        """Fail fast when the session signing key is absent (D15)."""
        if not (v or "").strip():
            raise ValueError(
                "SESSION_SECRET_KEY is not set. The service refuses to start "
                "without a session signing key. Generate one with "
                '`python -c "import secrets; print(secrets.token_urlsafe(32))"` '
                "and put it in your .env file."
            )
        return v

    # ---- Execution Safety ----
    # Maximum time (seconds) a blocking operation is allowed before the system
    # considers it a risk for WebSocket heartbeat timeout and session hang.
    # Used by:
    #   - mcp_safety_hook:  warn on bash timeout > this value
    #   - bash_tool:        default timeout for WRITE commands (TIMEOUT_WRITE)
    #   - bash_tool:        default timeout for background tasks (TIMEOUT_BACKGROUND)
    #   - background_tasks: default timeout for BackgroundTaskManager
    max_blocking_seconds: int = Field(
        default=180,
        ge=5,
        le=3600,
        description="Max blocking time before safety intervention (5–3600s)",
    )

    # ---- Rate Limiting ----
    rate_limit_initial_backoff: float = Field(
        default=10.0,
        description="Initial backoff delay in seconds after first 429 error",
    )
    rate_limit_max_backoff: float = Field(
        default=40.0,
        description="Maximum backoff delay in seconds",
    )
    rate_limit_backoff_multiplier: float = Field(
        default=2.0,
        description="Multiplier for backoff delay on consecutive 429 errors",
    )
    rate_limit_recovery_threshold: int = Field(
        default=2,
        description="Number of consecutive successes before starting recovery",
    )
    rate_limit_recovery_divisor: float = Field(
        default=2.0,
        description="Divisor for backoff delay during recovery",
    )

    # ---- Derived helpers ----

    @property
    def llm_available_models_list(self) -> list[str]:
        """Return parsed list of available model IDs for UI selection.

        Parses the comma-separated ``llm_available_models`` string.
        Falls back to ``[llm_model]`` if the field is empty.
        """
        raw = self.llm_available_models.strip()
        if not raw:
            return [self.llm_model]
        return [m.strip() for m in raw.split(",") if m.strip()]

    @property
    def llm_vision_models_set(self) -> set[str]:
        """Return parsed set of model IDs that support image input (vision).

        Parses the comma-separated ``llm_vision_models`` string.
        Returns an empty set if the field is empty or whitespace-only.
        """
        raw = self.llm_vision_models.strip()
        if not raw:
            return set()
        return {m.strip() for m in raw.split(",") if m.strip()}

    @property
    def sqlite_path_resolved(self) -> Path:
        """Return resolved SQLite path, creating parent dirs if needed."""
        p = Path(self.sqlite_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def embedding_api_key_resolved(self) -> str:
        """Fall back to llm_api_key if embedding-specific key is not set."""
        return self.embedding_api_key or self.llm_api_key

    @property
    def embedding_base_url_resolved(self) -> str:
        """Fall back to llm_base_url if embedding-specific URL is not set."""
        return self.embedding_base_url or self.llm_base_url


# Singleton — import `settings` anywhere to get the loaded config.
settings = QAAgentSettings()
