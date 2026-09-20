"""
QA Agent System — FastAPI Server

Main application entry point combining:
- Custom REST endpoints (sessions, review, skills, agents, health)
- SSE streaming endpoint (primary event channel)
- WebSocket streaming endpoint (backward-compatible)
- AgentOS integration
- Startup initialization (Milvus collections, skill loading)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware

from api.schemas import (
    AgentInfo, AgentMode, AvailableModelsResponse, ConfigResponse, ContextUsageResponse,
    CreateSessionRequest, CreateSessionResponse, HealthResponse, HistoryVersion,
    IdleStatusResponse, LastSeqResponse, MessageRecord, ModelInfo, PageMeta,
    RegenerateTurnRequest, RegenerateTurnResponse, RestoreSessionResponse,
    ReviewRequest, ReviewResponse, SendMessageRequest,
    SendMessageResponse, SessionMeta, SessionMessagesResponse, SessionStatus,
    SessionStatusResponse, SkillInfo, TruncateMessagesRequest, TruncateMessagesResponse,
    UpdateSessionRequest, UpdateSessionVisibilityRequest, UserSessionsResponse,
)
from api.session_manager import session_manager
from auth.dependencies import get_current_user
logger = logging.getLogger(__name__)
event_logger = logging.getLogger("qa_agent.events")


# ---------------------------------------------------------------------------
# Application Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

def _connection_teardown_exception_handler(loop, context):
    """事件循环异常 handler：过滤连接拆除阶段的无害噪音。

    Windows ProactorEventLoop 下对端强制断开（RST）时，
    _ProactorBasePipeTransport._call_connection_lost 对已死 socket 调
    shutdown() 会抛 ConnectionResetError(WinError 10054)。连接本来就要断，
    属收尾噪音；其余异常仍走默认 handler。
    """
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        logger.debug("连接拆除噪音已忽略: %s", exc)
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize system on startup, clean up on shutdown.

    Startup order (optimized for fast readiness):
      0.  MongoDB init (Beanie ODM)
      0.1 Load LLM token from local config (if embedded mode)
      1.  KnowledgeHub (Milvus) — kicked off as background task, non-blocking
      2.  Load skills (file-only, fast)
      3.  Register builtin tools
      3.5 MCP Manager — connect enabled servers as fire-and-forget background task
      4.  interrupt_manager callback + load pending reviews
      5.  Cleanup background task
    """
    import time as _startup_time
    _t0 = _startup_time.monotonic()
    logger.info("QA Agent System starting up...")

    # 过滤 Windows 连接拆除噪音（WinError 10054/10053 的 traceback 刷屏）
    asyncio.get_running_loop().set_exception_handler(
        _connection_teardown_exception_handler
    )

    from core.config import settings as _cfg

    # ------------------------------------------------------------------
    # 0. MongoDB / Beanie init
    # ------------------------------------------------------------------
    try:
        from db.mongo import init_db
        await init_db()
    except Exception:
        logger.warning("⚠ MongoDB initialization failed", exc_info=True)

    # ------------------------------------------------------------------
    # 1. KnowledgeHub (Milvus) — fire-and-forget background task
    #    Does NOT block lifespan completion. Hub becomes available once
    #    the background init finishes. Calls to get_knowledge_hub() before
    #    init completes return None (graceful degradation).
    # ------------------------------------------------------------------
    async def _init_knowledge_hub_bg() -> None:
        try:
            from memory.knowledge_hub import get_knowledge_hub
            hub = get_knowledge_hub()
            if hub is not None:
                logger.info("✓ KnowledgeHub ready (Milvus + Agno Knowledge)")
            else:
                logger.warning("⚠ KnowledgeHub unavailable — running without long-term memory")
        except Exception:
            logger.warning("⚠ KnowledgeHub initialization failed", exc_info=True)

    asyncio.create_task(_init_knowledge_hub_bg(), name="knowledge-hub-init")

    # ------------------------------------------------------------------
    # 2. Load skills (file scan only — fast)
    # ------------------------------------------------------------------
    try:
        from skills.loader import load_skills
        from skills.skill_tool import build_skill_tools
        from tools.registry import tool_registry
        from tools.base import readonly_meta

        skill_loader = load_skills(
            project_skills_dir=_cfg.project_skills_dir,
            user_skills_dir=_cfg.user_skills_dir,
        )
        # Only register 'default' Skills as @tool; 'on-demand' stay as metadata-only
        default_skills = skill_loader.get_default_skills()
        if default_skills:
            skill_tools = build_skill_tools(default_skills)
            for fn in skill_tools:
                name = getattr(fn, "name", str(fn))
                tool_registry.register(name, fn, readonly_meta(category="skill"))

        app.state.skill_loader = skill_loader
        on_demand_count = len(skill_loader.get_on_demand_skills())
        logger.info(
            "✓ Loaded %d skills (%d default, %d on-demand)",
            len(skill_loader.all()), len(default_skills), on_demand_count,
        )
        if len(skill_loader.all()) == 0:
            logger.warning("⚠ No skills loaded — check PROJECT_SKILLS_DIR=%s", _cfg.project_skills_dir)

        # ── User skills startup sync ──────────────
        # Materialize DB-persisted user skills missing/expired in local dir.
        # Best-effort: failures logged but do not block startup.
        try:
            from pathlib import Path as _Path
            from skills.materializer import sync_user_skills_from_db
            from db.mongo import get_motor_db
            _mdb = get_motor_db()
            if _mdb is not None:
                _user_dir = _Path(_cfg.user_skills_dir).expanduser()
                _user_dir.mkdir(parents=True, exist_ok=True)
                _mat, _skip = await sync_user_skills_from_db(
                    _mdb, _cfg.current_user_email, _user_dir,
                )
                # Re-scan user dir to pick up newly materialized user skills
                skill_loader.load_from_dirs([_user_dir])
            else:
                logger.info("MongoDB 不可用，跳过 user_skills 启动同步")
        except Exception as _e:
            logger.warning("user_skills 启动同步失败（不阻塞）：%s", _e, exc_info=True)
    except Exception as e:
        logger.error("❌ Skill loading failed: %s", e, exc_info=True)
        app.state.skill_loader = None

    # ------------------------------------------------------------------
    # 3. Register builtin tools
    # ------------------------------------------------------------------
    _register_builtin_tools()

    # ------------------------------------------------------------------
    # 3.5. MCP Manager — fire-and-forget background task
    #      Connecting MCP servers (especially HTTP handshake + list_tools)
    #      can take 1-5s. Running in background lets lifespan finish ASAP.
    #      Tools from MCP servers become available once connection completes.
    # ------------------------------------------------------------------
    async def _start_mcp_bg() -> None:
        try:
            from mcp_service.manager import mcp_manager
            await mcp_manager.startup(_cfg.mcp_config_path)
        except Exception:
            logger.warning("⚠ MCP Manager startup failed (degraded mode)", exc_info=True)

    asyncio.create_task(_start_mcp_bg(), name="mcp-manager-startup")

    # ------------------------------------------------------------------
    # 4. interrupt_manager: WS push callback + load pending reviews
    # ------------------------------------------------------------------
    from hooks.interrupt_manager import interrupt_manager

    async def _ws_push_callback(session_id: str, event: dict) -> None:
        # Persist to EventStore so SSE/WS reconnect can replay interrupt events
        from api.event_store import event_store
        event_store.append(session_id, event)
        await session_manager.push_ws_event(session_id, event)
        await session_manager.set_interrupt_pending(session_id)

    interrupt_manager.set_ws_push(_ws_push_callback)

    try:
        await interrupt_manager.load_pending_from_db()
    except Exception:
        logger.warning("⚠ Failed to scan pending checkpoints on startup", exc_info=True)

    # ------------------------------------------------------------------
    # 5. Cleanup background task
    # ------------------------------------------------------------------
    cleanup_task = asyncio.create_task(_cleanup_loop())

    _elapsed = _startup_time.monotonic() - _t0
    logger.info("✓ QA Agent System ready (startup=%.2fs)", _elapsed)
    yield

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    cleanup_task.cancel()

    # Drain background session cascade cleanups:
    # best-effort ≤2s wait; leftovers are reconciled by startup_recovery.
    try:
        await _drain_pending_cleanups()
    except Exception:
        logger.warning("Failed to drain pending session cleanups", exc_info=True)

    # Flush pending tracing spans before closing DB connections
    try:
        from opentelemetry import trace as _trace_api
        _provider = _trace_api.get_tracer_provider()
        if hasattr(_provider, "shutdown"):
            _provider.shutdown()
            logger.debug("Tracing provider shutdown — pending spans flushed")
    except Exception:
        pass  # tracing may not be initialized

    # Shutdown MCP Manager — disconnect all MCP servers
    try:
        from mcp_service.manager import mcp_manager
        await mcp_manager.shutdown()
    except Exception:
        logger.warning("⚠ MCP Manager shutdown error", exc_info=True)

    from db.mongo import close_db
    await close_db()

    logger.info("QA Agent System shutting down")


def _register_builtin_tools() -> None:
    """Register all builtin tools in the tool registry."""
    from tools.registry import tool_registry
    from tools.base import readonly_meta, sideeffect_meta
    from tools.file_tools import file_read, file_edit, glob_search
    from tools.bash_tool import bash
    from tools.search_tools import grep
    from tools.agent_spawn_tool import agent_spawn
    from tools.task_tools import task_list, task_update
    from tools.send_message_tool import send_message
    from tools.task_stop_tool import task_stop
    from tools.worker_status_tool import get_worker_status
    # tasks 13.1–13.3: artifact and review tools
    from tools.artifact_tools import save_artifact, load_artifact
    from tools.review_tools import request_review
    from tools.wait_tool import wait

    # Worker-only tools (not visible to Coordinator)
    tool_registry.register_batch([
        ("file_read",    file_read,    readonly_meta("Read file content", "file")),
        ("glob_search",  glob_search,  readonly_meta("Glob pattern file search", "file")),
        ("grep",         grep,         readonly_meta("Regex search in files", "search")),
        ("task_list",    task_list,    readonly_meta("View task plan and progress", "planning")),
        ("task_update",  task_update,  readonly_meta("Create plan or update task status", "planning")),
        ("file_edit",    file_edit,    sideeffect_meta("Write file content", "file")),
        ("bash",         bash,         sideeffect_meta("Execute shell command", "execution")),
        # artifact sharing — available to all Workers
        ("save_artifact",  save_artifact, sideeffect_meta("Save phase artifact to MongoDB", "artifact")),
        # task 13.2: review gate — available to Workers that need human sign-off
        ("request_review", request_review, sideeffect_meta("Request human review of phase output", "review")),
        ("wait",        wait,        sideeffect_meta("Wait or poll until condition met", "execution")),
    ], coordinator_visible=False)

    # task 13.3: load_artifact available to both Workers and Coordinator
    tool_registry.register(
        "load_artifact", load_artifact,
        readonly_meta("Load phase artifact from MongoDB", "artifact"),
        coordinator_visible=True,
    )

    # Coordinator-visible orchestration tools
    # agent_spawn is registered only once here with coordinator_visible=True;
    # it is intentionally excluded from the worker batch above.
    tool_registry.register(
        "agent_spawn", agent_spawn,
        readonly_meta("Spawn a sub-agent", "orchestration"),
        coordinator_visible=True,
    )
    tool_registry.register(
        "send_message", send_message,
        readonly_meta("Continue an existing Worker", "orchestration"),
        coordinator_visible=True,
    )
    tool_registry.register(
        "task_stop", task_stop,
        readonly_meta("Stop a running Worker", "orchestration"),
        coordinator_visible=True,
    )
    tool_registry.register(
        "get_worker_status", get_worker_status,
        readonly_meta("Query Worker pool status", "orchestration"),
        coordinator_visible=True,
    )
    logger.info("✓ Registered %d builtin tools", len(tool_registry))


async def _cleanup_loop() -> None:
    """Background task: periodic housekeeping every 5 minutes.

    1. Clean up expired interrupt sessions (30-min timeout).
    2. Evict EventStore entries for sessions that have been in a terminal
       state (completed/failed/aborted) for more than 10 minutes, freeing
       memory from stale event logs.
    """
    import time as _time
    from api.event_store import event_store

    EVENTSTORE_TTL = 600  # 10 minutes after session finishes

    while True:
        await asyncio.sleep(300)
        try:
            # 1. Expired interrupts
            cleaned = await session_manager.cleanup_expired_interrupts()
            if cleaned > 0:
                logger.info("Cleaned up %d expired interrupt session(s)", cleaned)

            # 2. Stale EventStore entries for finished sessions
            now = _time.time()
            terminal_statuses = (
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.ABORTED,
            )
            stale_cleared = 0
            for record in list(session_manager._sessions.values()):
                if record.status in terminal_statuses:
                    if now - record.updated_at > EVENTSTORE_TTL:
                        count = event_store.clear(record.session_id)
                        if count:
                            stale_cleared += count
            if stale_cleared:
                logger.info("Evicted %d stale events from EventStore", stale_cleared)

        except Exception:
            logger.warning("Cleanup loop error", exc_info=True)


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="QA Agent System",
    description="Multi-agent orchestration API with session management and human-in-the-loop review",
    version="0.1.0",  # static placeholder — real version from settings.app_version
    lifespan=lifespan,
)

from core.config import settings as _settings  # noqa: E402 (needed here for middleware)

# Patch the OpenAPI-spec version so the docs page (e.g. /docs) shows the
# configured/packaged version rather than the static "0.1.0" placeholder.
app.version = _settings.app_version

app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret_key,
    https_only=False,
    same_site="lax",
)

# Build allowed origins from settings — wildcard "*" cannot be used together
# with allow_credentials=True (browsers reject it). We enumerate the known
# frontend origins so that the session cookie is forwarded on cross-origin
# XHR/fetch requests from the Vite dev-server and any production domain.
_cors_origins = list({
    _settings.frontend_url.rstrip("/"),   # e.g. http://localhost:5173
    "http://localhost:5173",
    "http://127.0.0.1:5173",
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,               # required for cookie forwarding
    allow_methods=["*"],
    allow_headers=["*"],
)

# All API routes mounted under /api prefix so nginx can cleanly distinguish
# API requests (proxy to qa-agent) from SPA routes (try_files → index.html).
# SPA fallback (/), OpenAPI docs (/docs, /redoc, /openapi.json) stay at root.
api_router = APIRouter(prefix="/api")


from core.errors import LLMNotConfiguredError  # noqa: E402


def require_llm_configured() -> None:
    """Refuse to start an LLM-backed request when no provider key is set (D15).

    The process deliberately still boots without a key — /health, the UI shell
    and non-LLM endpoints stay usable — so this check runs at request time on
    the endpoints that actually reach the model.
    """
    if not (_settings.llm_api_key or "").strip():
        raise LLMNotConfiguredError()


@app.exception_handler(LLMNotConfiguredError)
async def _llm_not_configured_handler(request: Request, exc: LLMNotConfiguredError):
    """409 body shape the frontend already implements (top-level ``code`` + ``error``)."""
    return JSONResponse(
        status_code=409,
        content={"code": 409, "error": "LLM_NOT_CONFIGURED", "message": str(exc)},
    )


# Auth router
from auth.router import router as auth_router  # noqa: E402
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])

# Workspace router
from api.workspace import router as workspace_router  # noqa: E402
api_router.include_router(workspace_router, tags=["workspace"])
from api.session_uploads import router as session_uploads_router  # noqa: E402
api_router.include_router(session_uploads_router, tags=["session-uploads"])

# Vision convert router — stateless image-to-text OCR-like service.
from api.vision import router as vision_router  # noqa: E402
api_router.include_router(vision_router, tags=["vision"])

# MCP management router
from api.mcp_router import router as mcp_router  # noqa: E402
api_router.include_router(mcp_router, tags=["mcp"])

# System utility router
from api.system import router as system_router  # noqa: E402
api_router.include_router(system_router, tags=["system"])

# AgentOS adapter router (traces, agent runs)
from api.agent_os_adapter import agent_os_router  # noqa: E402
api_router.include_router(agent_os_router, tags=["agent-os"])

# HITL approvals router (agent-agnostic: approval list / resolve / run continue)
from api.approvals_routes import router as approvals_router  # noqa: E402
api_router.include_router(approvals_router, tags=["approvals"])

# Skill upload routes (user-uploaded skill packages)
from api.skill_upload_routes import router as skill_upload_router  # noqa: E402
api_router.include_router(skill_upload_router)


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@api_router.post("/sessions", response_model=CreateSessionResponse, tags=["sessions"])
async def create_session(
    req: CreateSessionRequest,
    request: Request,
    current_user: str | None = Depends(get_current_user),
):
    """Create a new agent session."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    session_id = await session_manager.create_session(
        agent_name=req.agent_name,
        mode=req.mode.value,
        game_version=req.game_version,
        module=req.module,
        email=current_user,
    )

    # Parse model_slots into session_overrides for ModelSlotRegistry
    # Supports two formats:
    #   Shorthand: {"orchestrate": "deepseek-flash"}
    #   Full:      {"orchestrate": {"model": "deepseek-flash", "base_url": "..."}}
    session_overrides = None
    if req.model_slots:
        from core.model_slots import ModelSlot, SlotConfig
        valid_slot_values = {s.value for s in ModelSlot}
        invalid_keys = set(req.model_slots.keys()) - valid_slot_values
        if invalid_keys:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid model slot name(s): {sorted(invalid_keys)}. "
                    f"Valid slots: {sorted(valid_slot_values)}"
                ),
            )
        session_overrides = {}
        for k, v in req.model_slots.items():
            if isinstance(v, str):
                # Shorthand: just a model name string
                session_overrides[k] = SlotConfig(model=v)
            else:
                # Full ModelSlotOverride object
                session_overrides[k] = SlotConfig(
                    model=v.model, base_url=v.base_url, api_key=v.api_key
                )

    # Build the agent/team (but don't run it yet)
    try:
        if req.mode == AgentMode.COORDINATOR:
            from coordinator.team_builder import create_coordinator_team
            agent = await create_coordinator_team(
                task="",
                worker_agent_names=req.worker_agent_names,
                session_id=session_id,
                game_version=req.game_version,
                module=req.module,
                session_overrides=session_overrides,
            )
        else:
            from agents.base import create_agent_from_definition
            from agents.registry import agent_registry

            definition = agent_registry.get(req.agent_name)
            if definition is None:
                raise HTTPException(status_code=404, detail=f"Agent '{req.agent_name}' not found")

            agent = await create_agent_from_definition(
                definition,
                session_id=session_id,
                game_version=req.game_version,
                module=req.module,
                session_overrides=session_overrides,
                user_id=current_user or None,
            )

        await session_manager.attach_agent(session_id, agent)

    except HTTPException:
        await session_manager.delete_session(session_id)
        raise
    except Exception as e:
        await session_manager.delete_session(session_id)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {e}")

    # Async write user→session mapping (fire-and-forget, non-blocking)
    if current_user:
        import time as _time
        session_meta = {
            "session_id": session_id,
            "agent_name": req.agent_name,
            "mode": req.mode.value,
            "game_version": req.game_version,
            "module": req.module,
            "title": "",  # no task description at create time; updated on first message
            "created_at": _time.time(),
            "last_active_at": _time.time(),
        }
        asyncio.create_task(_write_user_session_mapping(current_user, session_meta))

    return CreateSessionResponse(
        session_id=session_id,
        status=SessionStatus.IDLE,
        agent_name=req.agent_name,
        mode=req.mode.value,
    )


async def _write_user_session_mapping(email: str, session_meta: dict) -> None:
    """Fire-and-forget: persist user→session mapping to MongoDB."""
    try:
        from db.user_sessions import user_sessions_dao
        await user_sessions_dao.upsert_session(email, session_meta)
    except Exception:
        logger.warning("Failed to write user-session mapping for %s", email, exc_info=True)


async def _update_session_activity(email: str, session_id: str, message_content: str) -> None:
    """Fire-and-forget: update session title (first time only) and last_active_at.

    Title is set from the first MAX_TITLE_LEN characters of the user's first message.
    last_active_at is always updated to now.
    """
    import time as _time
    MAX_TITLE_LEN = 50
    try:
        from db.user_sessions import user_sessions_dao
        # Strip newlines for a cleaner one-line title
        title_candidate = message_content.strip().replace("\n", " ")[:MAX_TITLE_LEN]
        await user_sessions_dao.update_session_meta(
            email,
            session_id,
            title=title_candidate,
            last_active_at=_time.time(),
        )
    except Exception:
        logger.warning(
            "Failed to update session activity for %s / %s", email, session_id, exc_info=True
        )


@api_router.get("/users/{email}/sessions", response_model=UserSessionsResponse, tags=["users"])
async def get_user_sessions(
    email: str,
    current_user: str | None = Depends(get_current_user),
):
    """Get historical session list for a user (requires authentication)."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user != email:
        raise HTTPException(status_code=403, detail="Cannot access another user's sessions")

    from db.user_sessions import user_sessions_dao
    sessions_raw = await user_sessions_dao.get_sessions(email)
    sessions = []
    for s in sessions_raw:
        meta = SessionMeta(**s)
        # Inject real-time status from in-memory SessionManager
        record = session_manager.get(meta.session_id)
        if record is not None:
            meta.status = record.status.value if hasattr(record.status, "value") else str(record.status)
        # else: keep default "inactive" (session not loaded in memory)
        sessions.append(meta)
    return UserSessionsResponse(sessions=sessions)


@api_router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse, tags=["sessions"])
async def get_session_messages(
    session_id: str,
    before: int | None = None,
    limit: int | None = None,
    anchor_run_id: str | None = None,
):
    """Get message history for a session (no auth required — caller holds session_id).

    agent_name is injected into assistant/tool messages that lack a ``name`` field,
    sourced from the in-memory SessionRecord (active sessions) or from the Agno
    agent instance name (restored sessions).

    分页参数（全部可选，缺省 = 全量，向后兼容）：
      - ``before``: 排他 run 下标上界（向前翻页游标）
      - ``limit``:   请求的 run 数（服务端 clamp [1,3]，items 预算 30）
      - ``anchor_run_id``: 窗口最老 run 锚标识；与 runs[before-1] 不一致
        时返回 ``resync=true``（ABA 防护）
    """
    from core.storage_reader import inject_message_ids, read_session_messages

    # Resolve agent_name for name injection:
    # 1. Try in-memory SessionRecord (fastest, available for active sessions)
    # 2. Try agent instance name (restored sessions)
    agent_name: str = ""
    record = session_manager.get(session_id)
    if record is not None:
        agent_name = record.agent_name or ""
        if not agent_name and record.agent is not None:
            agent_name = getattr(record.agent, "name", "") or ""

    # Pass session status so storage_reader can merge in-progress events
    session_status: str = ""
    if record is not None:
        session_status = record.status.value if hasattr(record.status, "value") else str(record.status)

    # ── 分页路径（任一分页参数出现即启用；无参 = 现行全量，响应结构不变）──
    if before is not None or limit is not None or anchor_run_id is not None:
        from core.storage_reader import read_session_page

        page = await read_session_page(
            session_id,
            before=before,
            limit=limit,
            anchor=anchor_run_id,
            agent_name=agent_name or None,
        )
        return SessionMessagesResponse(
            session_id=session_id,
            messages=[MessageRecord(**m) for m in page["messages"]],
            page_meta=PageMeta(**page["page_meta"]),
            resync=page["resync"],
        )

    raw = await read_session_messages(
        session_id,
        agent_name=agent_name or None,
        session_status=session_status or None,
    )
    # Inject stable message IDs (msg_0, msg_1, ...) for frontend reference
    inject_message_ids(raw)
    messages = [MessageRecord(**m) for m in raw]
    return SessionMessagesResponse(session_id=session_id, messages=messages)


@api_router.get(
    "/sessions/{session_id}/messages/tail",
    response_model=SessionMessagesResponse,
    tags=["sessions"],
)
async def get_session_messages_tail(
    session_id: str,
    limit: int | None = None,
):
    """尾页原语：最近 N runs + 进行中
    run 的持久化事件合并 + page_meta。

    服务五路回灌：首次加载、断连回灌、streamPool 自愈、truncate 响应、
    truncate 回滚（前端 refreshTail 统一走此端点）。

    ``limit``: 请求的 run 数（服务端 clamp [1,3]，items 预算 30）。
    """
    from core.storage_reader import read_session_tail

    agent_name: str = ""
    record = session_manager.get(session_id)
    if record is not None:
        agent_name = record.agent_name or ""
        if not agent_name and record.agent is not None:
            agent_name = getattr(record.agent, "name", "") or ""

    session_status: str = ""
    if record is not None:
        session_status = record.status.value if hasattr(record.status, "value") else str(record.status)

    tail = await read_session_tail(
        session_id,
        limit=limit,
        agent_name=agent_name or None,
        session_status=session_status or None,
    )
    return SessionMessagesResponse(
        session_id=session_id,
        messages=[MessageRecord(**m) for m in tail["messages"]],
        page_meta=PageMeta(**tail["page_meta"]),
    )


@api_router.post(
    "/sessions/{session_id}/messages/truncate",
    response_model=TruncateMessagesResponse,
    tags=["sessions"],
)
async def truncate_session_messages(
    session_id: str,
    body: TruncateMessagesRequest,
):
    """Truncate messages from a specified point (inclusive) in a session.

    Deletes the target message and all messages after it.  Cleans up
    Agno Storage, EventStore, and Agent in-memory context to ensure
    consistency.  Only allowed on non-running, normal-mode sessions.
    """
    from api.event_store import event_store
    from core.storage_reader import inject_message_ids, invalidate_parse_cache, read_session_messages
    from core.storage_writer import TruncateOutcome
    from core.storage_writer import truncate_session_messages as _truncate_storage

    # --- 4.1 Session existence check (fast path; authoritative re-check below
    # inside the per-session lock — this pre-check just avoids creating a lock
    # entry for garbage ids) ---
    record = session_manager.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # --- 7.1 Same-session critical section (harden-truncate-session-lock) ---
    # The per-session lock covers validation → idempotency → storage IO →
    # record mutation → broadcast as ONE same-session critical section; two
    # concurrent truncates of one session serialize here (the old code only
    # validated under the global lock and then ran IO unlocked — concurrent
    # truncates interleaved). The global _lock is taken only for micro
    # sections (get_if_present / truncating flag), never across storage IO,
    # so other sessions' lifecycle ops are never stalled by a slow truncate.
    session_lock = session_manager.session_lock(session_id)
    async with session_lock:
        # Re-fetch under the global lock: the session may have been deleted
        # between the fast-path get and lock acquisition.
        record = await session_manager.get_if_present(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        # --- 4.2 Session status check ---
        if record.status in (SessionStatus.RUNNING, SessionStatus.INTERRUPT_PENDING):
            raise HTTPException(
                status_code=409,
                detail=f"Session is {record.status.value} — cannot truncate while busy",
            )

        # --- 4.3 Mode check: only normal sessions ---
        if record.mode == AgentMode.COORDINATOR.value or record.mode == AgentMode.COORDINATOR:
            raise HTTPException(
                status_code=400,
                detail="Coordinator sessions do not support message truncation",
            )

        # --- 4.0 Idempotent replay (D13): same request_id returns cached outcome ---
        # Moved INSIDE the session lock (was pre-lock, racy against a concurrent
        # truncate writing the slot). Must run before the 404 message lookup — a
        # replayed request whose target message was already deleted by the
        # original (successful) truncate would otherwise be misclassified as
        # "external modification".
        # Truncate idempotency: an identical request_id means the work is already done.
        if body.request_id:
            cached = record.last_truncate_outcome
            if cached and cached.get("request_id") == body.request_id:
                replay_agent_name = record.agent_name or ""
                replay_status = (
                    record.status.value if hasattr(record.status, "value") else str(record.status)
                )
                replay_raw = await read_session_messages(
                    session_id,
                    agent_name=replay_agent_name or None,
                    session_status=replay_status or None,
                )
                inject_message_ids(replay_raw)
                logger.info(
                    "Session %s: idempotent truncate replay for request_id=%s (cached outcome)",
                    session_id,
                    body.request_id,
                )
                return TruncateMessagesResponse(
                    request_id=body.request_id,
                    truncated_count=cached.get("truncated_count", 0),
                    remaining_count=cached.get("remaining_count", 0),
                    remaining_messages=[MessageRecord(**m) for m in replay_raw],
                    history_version=cached.get("history_version"),
                )

        # --- 7.2 Block concurrent run starts for the IO duration ---
        # try_set_running (send / adapter) checks this flag under
        # _lock, so no run can start between validation and IO end and append
        # messages that _truncate_storage would delete past keep_count.
        async with session_manager._lock:
            record.truncating = True

        try:
            # --- 4.4 Read messages & locate target ---
            agent_name = record.agent_name or ""
            session_status = record.status.value if hasattr(record.status, "value") else str(record.status)

            all_messages = await read_session_messages(
                session_id,
                agent_name=agent_name or None,
                session_status=session_status or None,
            )
            inject_message_ids(all_messages)

            # Find target message index
            target_index = None
            for i, msg in enumerate(all_messages):
                if msg.get("id") == body.message_id:
                    target_index = i
                    break

            if target_index is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Message {body.message_id} not found in session {session_id}",
                )

            # --- 4.5 Tool message truncation protection ---
            # If target is a tool message, auto-adjust to the preceding assistant
            # message that contains the associated tool_calls.
            if all_messages[target_index].get("role") == "tool":
                tool_call_id = all_messages[target_index].get("tool_call_id")
                adjusted = False
                for j in range(target_index - 1, -1, -1):
                    msg_j = all_messages[j]
                    if msg_j.get("role") == "assistant" and msg_j.get("tool_calls"):
                        # Check if this assistant message's tool_calls contains the matching ID
                        if tool_call_id:
                            tc_ids = [
                                tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                                for tc in (msg_j.get("tool_calls") or [])
                            ]
                            if tool_call_id in tc_ids:
                                target_index = j
                                adjusted = True
                                break
                        else:
                            # No tool_call_id — fall back to nearest assistant with tool_calls
                            target_index = j
                            adjusted = True
                            break
                if not adjusted:
                    # Could not find the parent assistant — truncate at tool msg as-is
                    logger.warning(
                        "Could not find parent assistant for tool message %s in session %s",
                        body.message_id,
                        session_id,
                    )

            keep_count = target_index  # keep [0, target_index)
            total_count = len(all_messages)
            truncated_count = total_count - keep_count

            # --- 4.6 Truncate Agno Storage ---
            outcome = TruncateOutcome(deleted_count=0, total_runs=0, total_messages=0)
            try:
                outcome = await _truncate_storage(session_id, keep_count)
            except ValueError:
                # Session not in storage — might be a brand-new session with only events
                logger.warning("Session %s not found in Agno storage during truncate", session_id)
            except RuntimeError as exc:
                logger.error("Agno storage truncation failed for session %s: %s", session_id, exc)
                raise HTTPException(status_code=500, detail=f"Storage truncation failed: {exc}")

            # D6 解析缓存主动失效：截断改变了存储前缀，翻页缓存不可再用
            invalidate_parse_cache(session_id)

            # --- 4.7 Clear EventStore ---
            try:
                await event_store.clear_persisted(session_id)
            except Exception:
                logger.warning("Failed to clear EventStore for session %s", session_id, exc_info=True)

            # --- 5.1 Agent memory cleanup: kill-and-rebuild ---
            if record.agent is not None:
                record.agent = None
                logger.info("Session %s: agent instance cleared for post-truncation rebuild", session_id)

            # --- 5.2 Reset session status to IDLE ---
            if record.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.ABORTED):
                record.status = SessionStatus.IDLE
                record.touch()

            # --- 6.1 WebSocket notification ---
            # D12/D13: payload carries request_id (self/external discrimination) and
            # history_version (offline-tab convergence). Legacy count fields preserved.
            history_version_dict = {
                "total_runs": outcome.total_runs,
                "total_messages": outcome.total_messages,
            }
            broadcast_payload = {
                "event_type": "messages_truncated",
                "truncated_count": truncated_count,
                "remaining_count": keep_count,
                "history_version": history_version_dict,
            }
            if body.request_id is not None:
                broadcast_payload["request_id"] = body.request_id
            await session_manager.push_ws_event(session_id, broadcast_payload)

            # --- 6.2 Persist idempotency slot (D13) ---
            if body.request_id is not None:
                record.last_truncate_outcome = {
                    "request_id": body.request_id,
                    "truncated_count": truncated_count,
                    "remaining_count": keep_count,
                    "history_version": history_version_dict,
                }

            # --- Read remaining messages after truncation (same path as GET /messages) ---
            post_truncate_status = record.status.value if hasattr(record.status, "value") else str(record.status)
            remaining_raw = await read_session_messages(
                session_id,
                agent_name=agent_name or None,
                session_status=post_truncate_status or None,
            )
            inject_message_ids(remaining_raw)
            remaining_messages = [MessageRecord(**m) for m in remaining_raw]

            logger.info(
                "Session %s: truncated %d messages (kept %d) from message %s",
                session_id,
                truncated_count,
                keep_count,
                body.message_id,
            )

            response = TruncateMessagesResponse(
                request_id=body.request_id,
                truncated_count=truncated_count,
                remaining_count=keep_count,
                remaining_messages=remaining_messages,
                history_version=HistoryVersion(**history_version_dict),
            )
        finally:
            # Unconditionally release the run-start gate, even on error paths —
            # a stuck flag would 409 every future send until process restart.
            async with session_manager._lock:
                record.truncating = False

    return response


@api_router.post(
    "/sessions/{session_id}/turns/regenerate",
    response_model=RegenerateTurnResponse,
    tags=["sessions"],
)
async def regenerate_turn(
    session_id: str,
    body: RegenerateTurnRequest,
    current_user: str | None = Depends(get_current_user),
):
    """Regenerate a turn: truncate through the turn's user message, then rerun.

    截断保留 `[0, 该轮 U 下标 + 1)`——U 原位
    保留在存储与视图；随后 kill-and-rebuild agent，后台以
    ``acontinue_run(run_id=边界run, continue_from="last_user")`` 续跑——不追加
    用户消息，新回复经 WS 流式渲染。定位全链路按位号/轮锚，MUST NOT 内容匹配。

    锚（D5 三级解析的服务端面）：
    - ``message_id``：hydrated 卡片的 `msg_{i}`（位号，storage flatten 同构）；
    - ``turn_last``：服务器侧解析最后一个 user 消息（最新 live 轮零网络主路径）。

    状态校验/幂等/广播与 truncate 端点同构（running/中断中 409、coordinator
    400、request_id 幂等重放、messages_truncated 广播含 request_id +
    history_version）。
    """
    from api.event_store import event_store
    from core.storage_reader import (
        inject_message_ids, invalidate_parse_cache, parse_session_runs, read_session_messages,
    )
    from core.storage_writer import TruncateOutcome
    from core.storage_writer import truncate_session_messages as _truncate_storage

    if bool(body.message_id) == bool(body.turn_last):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one anchor: message_id (msg_{i}) or turn_last=true",
        )

    require_llm_configured()

    # Fast-path existence check (authoritative re-check inside the session lock)
    record = session_manager.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    session_lock = session_manager.session_lock(session_id)
    async with session_lock:
        # Re-fetch under the lock (session may have been deleted in between)
        record = await session_manager.get_if_present(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        # Idempotent replay (D13 slot, same convention as truncate) — MUST precede
        # the status check：重答把会话置 RUNNING，响应丢失后的客户端重试会撞上
        # busy 门；截断既已生效（槽已写），重放直接回缓存结果。
        if body.request_id:
            cached = record.last_truncate_outcome
            if cached and cached.get("request_id") == body.request_id:
                logger.info(
                    "Session %s: idempotent regenerate replay for request_id=%s (cached outcome)",
                    session_id, body.request_id,
                )
                return RegenerateTurnResponse(
                    request_id=body.request_id,
                    truncated_count=cached.get("truncated_count", 0),
                    remaining_count=cached.get("remaining_count", 0),
                    history_version=cached.get("history_version"),
                )

        # Status check: same gate as truncate
        if record.status in (SessionStatus.RUNNING, SessionStatus.INTERRUPT_PENDING):
            raise HTTPException(
                status_code=409,
                detail=f"Session is {record.status.value} — cannot regenerate while busy",
            )

        # Mode check: only normal sessions
        if record.mode == AgentMode.COORDINATOR.value or record.mode == AgentMode.COORDINATOR:
            raise HTTPException(
                status_code=400,
                detail="Coordinator sessions do not support turn regeneration",
            )

        # Block concurrent run starts for the IO duration (same as truncate)
        async with session_manager._lock:
            record.truncating = True

        try:
            # --- Storage-only view: flatten messages + run boundaries (D5) ---
            parsed = await parse_session_runs(session_id)
            if parsed is None or not parsed.messages:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session {session_id} has no persisted messages",
                )
            all_messages = [dict(m) for m in parsed.messages]
            inject_message_ids(all_messages)

            # --- Locate the turn's user message (U) ---
            u_index: int | None = None
            if body.turn_last:
                for i in range(len(all_messages) - 1, -1, -1):
                    if all_messages[i].get("role") == "user":
                        u_index = i
                        break
                if u_index is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Session {session_id} has no user messages in storage",
                    )
                # 防漂移守卫（aborted/failed）：EventStore pending 聚合出比 storage
                # 更新的 user 消息（硬崩溃未落库）→ turn: last 会错定位到上一轮，
                # 拒绝让前端刷新。正常 abort（collector 已落库）双份同内容 → 放行。
                if record.status in (SessionStatus.ABORTED, SessionStatus.FAILED):
                    agent_name = record.agent_name or ""
                    session_status = (
                        record.status.value if hasattr(record.status, "value") else str(record.status)
                    )
                    merged = await read_session_messages(
                        session_id,
                        agent_name=agent_name or None,
                        session_status=session_status or None,
                    )
                    last_user_merged = next(
                        (m for m in reversed(merged) if m.get("role") == "user"), None,
                    )
                    if last_user_merged is not None and (
                        last_user_merged.get("content") != all_messages[u_index].get("content")
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail="最新用户消息尚未落库（会话状态与存储不一致），请刷新后重试",
                        )
            else:
                for i, msg in enumerate(all_messages):
                    if msg.get("id") == body.message_id:
                        u_index = i
                        break
                if u_index is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Message {body.message_id} not found in session {session_id}",
                    )
                if all_messages[u_index].get("role") != "user":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Message {body.message_id} is not a user message — regenerate anchors on a turn's user message",
                    )

            # --- Boundary run owning U (auto-fork / in-place continue target) ---
            boundary = next(
                (r for r in parsed.runs if r.start <= u_index < r.start + r.count),
                None,
            )
            if boundary is None or not boundary.run_id:
                raise HTTPException(
                    status_code=409,
                    detail="无法定位该轮所属的 run 边界（run_id 缺失），无法重答",
                )

            keep_count = u_index + 1  # keep [0, U] — D3：U 原位保留
            total_count = len(all_messages)
            truncated_count = total_count - keep_count

            # --- Truncate the turn body and everything after) ---
            outcome = TruncateOutcome(deleted_count=0, total_runs=0, total_messages=0)
            try:
                outcome = await _truncate_storage(session_id, keep_count)
            except ValueError:
                raise HTTPException(
                    status_code=404, detail=f"Session {session_id} not found in Agno storage",
                )
            except RuntimeError as exc:
                logger.error("Agno storage truncation failed for session %s: %s", session_id, exc)
                raise HTTPException(status_code=500, detail=f"Storage truncation failed: {exc}")

            invalidate_parse_cache(session_id)

            # --- Clear EventStore (turn body events + stale pending) ---
            try:
                await event_store.clear_persisted(session_id)
            except Exception:
                logger.warning("Failed to clear EventStore for session %s", session_id, exc_info=True)

            # --- Kill-and-rebuild the agent (stale _cached_session must not
            # resurrect the truncated runs when acontinue_run persists) ---
            email = record.email or current_user
            if not email:
                raise HTTPException(status_code=409, detail="Session has no bound user — cannot rebuild agent")

            from db.user_sessions import user_sessions_dao
            meta = await user_sessions_dao.get_session_meta(email, session_id)
            if meta is None:
                raise HTTPException(status_code=404, detail=f"Session not found for user in metadata store")

            record.agent = None
            agent = await _reconstruct_agent_for_session(session_id, email, meta)

            # --- Broadcast messages_truncated (cross-tab convergence, D12/D13) ---
            history_version_dict = {
                "total_runs": outcome.total_runs,
                "total_messages": outcome.total_messages,
            }
            broadcast_payload = {
                "event_type": "messages_truncated",
                "truncated_count": truncated_count,
                "remaining_count": keep_count,
                "history_version": history_version_dict,
            }
            if body.request_id is not None:
                broadcast_payload["request_id"] = body.request_id
            await session_manager.push_ws_event(session_id, broadcast_payload)

            # --- Persist idempotency slot (same slot as truncate) ---
            if body.request_id is not None:
                record.last_truncate_outcome = {
                    "request_id": body.request_id,
                    "truncated_count": truncated_count,
                    "remaining_count": keep_count,
                    "history_version": history_version_dict,
                }

            response = RegenerateTurnResponse(
                request_id=body.request_id,
                truncated_count=truncated_count,
                remaining_count=keep_count,
                history_version=HistoryVersion(**history_version_dict),
            )
        finally:
            # Release the run-start gate before launching the rerun task —
            # try_set_running checks this flag and would reject our own task.
            async with session_manager._lock:
                record.truncating = False

        # --- Launch the rerun (still inside the session lock) ---
        # aborted 会话主用例（中断后重答）：清残留 abort 信号，防续跑任务被
        # 旧事件立刻取消（镜像 send_message 的 clear_abort）。
        if record.status == SessionStatus.ABORTED:
            session_manager.clear_abort(session_id)
        acquired = await session_manager.try_set_running(session_id)
        if not acquired:
            raise HTTPException(
                status_code=409,
                detail="Session is busy — storage cut applied but rerun not started, retry",
            )

        _session_extra: dict | None = None
        if current_user:
            _session_extra = {"real_user_id": current_user}
        task = asyncio.create_task(
            _run_regenerate_task(
                session_id, agent, boundary.run_id,
                user_id=email or None, session_extra=_session_extra,
            ),
            name=f"regen_task_{session_id[:8]}",
        )
        session_manager.set_task(session_id, task)

        logger.info(
            "Session %s: regenerate turn (U at msg_%d, boundary run %s) — truncated %d, rerun launched",
            session_id, u_index, boundary.run_id, truncated_count,
        )
        return response


# ── Abort watcher ────────────────────────────────────────────────────

async def _watch_abort(
    session_id: str,
    agent_name: str,
    abort_evt: asyncio.Event,
    target: asyncio.Task,
) -> None:
    """Wait for the session abort signal, notify clients, then cancel the run task.

    "abort watcher 触发时推送 run_aborting 瞬态事件": ``run_aborting`` is
    pushed in the same tick as
    ``cancel()`` so the UI can render 「中断中」immediately — the terminal
    ``run_aborted`` only arrives after the shielded session save completes
    (seconds on remote storage).  The transient event bypasses the EventStore
    on purpose: no seq_id, no replay, live consumers only.

    Args:
        session_id: Target session.
        agent_name: Agent owning the run (surfaced in the transient event).
        abort_evt: Session abort signal, set by POST /sessions/{id}/abort.
        target: Run task to cancel at its next await point.
    """
    await abort_evt.wait()
    if target.done():
        # Run already finished: stay silent so no 「中断中」marker outlives it.
        return

    t0 = time.monotonic()
    try:
        await session_manager.push_ws_event(
            session_id,
            {
                "event_type": "run_aborting",
                "session_id": session_id,
                "agent_name": agent_name,
            },
        )
    except Exception:
        logger.warning(
            "abort watcher: run_aborting push failed (session=%s)", session_id,
            exc_info=True,
        )

    logger.info(
        "abort watcher: run_aborting pushed in %.0fms, cancelling run task (session=%s)",
        (time.monotonic() - t0) * 1000.0,
        session_id,
    )
    target.cancel()


async def _run_regenerate_task(
    session_id: str,
    agent: Any,
    boundary_run_id: str,
    *,
    user_id: str | None = None,
    session_extra: dict | None = None,
) -> None:
    """Background task: rerun a turn via acontinue_run and stream events.

    与 ``_run_agent_task`` 同构（EventStore 先落再推 WS / abort watcher / 终态
    收口 / finally 清 task 句柄），差异：

    - 原语为 ``stream_continue_agent_events``（不 yield user_message——U 已在
      存储，重答 MUST NOT 重复渲染/落库）；
    - EventStore 已被 regenerate 端点清空 → 首个事件前 ``ensure_counter_seeded``
      （对齐 send_message：重启进程后计数器从持久水位恢复，陈旧客户端高水位
      不会吞掉新 run 事件）；
    - 成功后 D4 去重收尾：fork 路径（新 run_id ≠ 边界 run_id）drop 原边界
      run——否则 flatten 视图出现重复 U。
    """
    from api.event_store import event_store

    _agent_task = asyncio.current_task()
    _abort_watcher: asyncio.Task | None = None

    try:
        from core.stream_adapter import stream_continue_agent_events

        try:
            await event_store.ensure_counter_seeded(session_id)
        except Exception:
            logger.warning(
                "regenerate: counter seed failed (session=%s)", session_id, exc_info=True,
            )

        _abort_event = session_manager.get_abort_event(session_id)
        _was_aborted = False
        run_info: dict = {}

        if _abort_event is not None:
            _abort_watcher = asyncio.create_task(
                _watch_abort(
                    session_id, getattr(agent, "name", "") or "", _abort_event, _agent_task,
                ),
                name=f"abort_watcher_{session_id[:8]}",
            )

        async for event in stream_continue_agent_events(
            agent, boundary_run_id, session_id,
            run_info=run_info, user_id=user_id, session_extra=session_extra,
            # 边界 run 若停在 HITL 暂停态：清被丢弃轮的挂起门（fix-regenerate-
            # on-paused-run）——否则续跑会注入伪造 null 作答并在工具批次后 break。
            close_pending_hitl=True,
        ):
            # Persist to EventStore first (assigns seq_id in-place)
            event_store.append(session_id, event)

            # Then push to live SSE/WS consumers
            await session_manager.push_ws_event(session_id, event)

            _log_event(event)

            if event.get("event_type") == "run_aborted":
                _was_aborted = True

            if event.get("event_type") in ("run_complete", "approval_pending", "clarification_request"):
                from core.context_usage import invalidate as _invalidate_context_usage

                _invalidate_context_usage(session_id)

        if _abort_watcher is not None and not _abort_watcher.done():
            _abort_watcher.cancel()

        if _was_aborted:
            await session_manager.set_aborted(session_id)
            # Clear the abort signal so the session can be resumed later
            session_manager.clear_abort(session_id)
        else:
            await session_manager.set_completed(session_id)
            # Clean up persisted events — Agno has saved the full session
            await event_store.clear_persisted(session_id)

            # D4 去重收尾：completed 边界 run 续跑时 agno auto-fork 追加了克隆
            # run（[前缀…U…新回复]）；原边界 run（截断后仍含 U）必须删除，
            # 否则 flatten 视图 U 重复。原地续跑路径 run_id 未变，无需处理。
            new_run_id = run_info.get("run_id")
            if new_run_id and new_run_id != boundary_run_id:
                try:
                    from core.storage_writer import drop_session_run
                    await drop_session_run(session_id, boundary_run_id)
                except Exception:
                    logger.warning(
                        "regenerate: drop boundary run %s failed (session=%s) — "
                        "storage holds a duplicated user message until next truncate",
                        boundary_run_id, session_id, exc_info=True,
                    )
            else:
                logger.warning(
                    "regenerate: new run_id not captured (session=%s boundary=%s) — "
                    "boundary run dedup skipped defensively",
                    session_id, boundary_run_id,
                )

    except asyncio.CancelledError:
        await session_manager.set_aborted(session_id)
        session_manager.clear_abort(session_id)
        raise

    except Exception as e:
        logger.exception("Regenerate task failed (session=%s)", session_id)
        await session_manager.set_failed(session_id, str(e))
        error_event = {
            "event_type": "run_error",
            "session_id": session_id,
            "error": str(e),
        }
        event_store.append(session_id, error_event)
        await session_manager.push_ws_event(session_id, error_event)
    finally:
        # Cancel watcher if still running (normal completion path)
        if _abort_watcher is not None and not _abort_watcher.done():
            try:
                _abort_watcher.cancel()
            except Exception:
                pass
        # Clear the task handle so the session knows no task is running
        record = session_manager.get(session_id)
        if record:
            record._task = None


@api_router.post(
    "/users/{email}/sessions/{session_id}/restore",
    response_model=RestoreSessionResponse,
    tags=["users"],
)
async def restore_session(
    email: str,
    session_id: str,
    current_user: str | None = Depends(get_current_user),
):
    """Restore a historical session: reconstruct the Agent and register it in SessionManager.

    After this call, the session is active (status=idle) and ready to receive messages.
    The frontend should call GET /sessions/{session_id}/messages to display history,
    then POST /sessions/{session_id}/messages to continue the conversation.
    """
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user != email:
        raise HTTPException(status_code=403, detail="Cannot restore another user's session")

    # If session is already active in memory with a valid agent, return immediately (idempotent)
    existing = session_manager.get(session_id)
    if existing is not None and existing.agent is not None:
        return RestoreSessionResponse(
            session_id=session_id,
            status=existing.status.value,
            already_active=True,
        )

    # If session record exists but agent is None (orphaned from a failed create),
    # clean it up so we can reconstruct properly below.
    if existing is not None:
        logger.warning(
            "Session %s found in memory with agent=None — cleaning up orphaned record",
            session_id,
        )
        await session_manager.delete_session(session_id)

    # Look up session metadata from MongoDB
    from db.user_sessions import user_sessions_dao
    meta = await user_sessions_dao.get_session_meta(email, session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found for this user")

    # Coordinator restore not yet supported
    if meta.get("mode") == "coordinator":
        raise HTTPException(status_code=501, detail="Coordinator session restore not yet supported")

    agent = await _reconstruct_agent_for_session(session_id, email, meta)

    # Check how many historical messages are available
    message_count = 0
    try:
        from core.storage_reader import read_session_messages
        messages = await read_session_messages(session_id, agent_name=meta.get("agent_name", "") or None)
        message_count = len(messages)
        if message_count == 0:
            logger.warning("Restored session %s has 0 historical messages", session_id)
    except Exception:
        logger.warning("Failed to count messages for restored session %s", session_id, exc_info=True)

    return RestoreSessionResponse(
        session_id=session_id,
        status="idle",
        already_active=False,
        message_count=message_count,
    )


async def _reconstruct_agent_for_session(session_id: str, email: str, meta: dict) -> Any:
    """Rebuild an Agent for an existing session and register it into SessionManager.

    Shared by POST /users/{email}/sessions/{session_id}/restore and
    POST /sessions/{session_id}/turns/regenerate（截断后
    agent 实例 kill-and-rebuild 与续跑必须在服务端一次完成，不能依赖前端
    再调 /restore）. Raises HTTPException on failure.
    """
    # Look up agent definition
    from agents.registry import agent_registry
    agent_name = meta.get("agent_name", "")
    definition = agent_registry.get(agent_name)
    if definition is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_name}' not found in registry",
        )

    # Load workspace binding from DB (if any) — done BEFORE agent creation
    # so that workspace_root is available during system prompt construction.
    from db.workspace import get_workspace as dao_get_workspace
    restored_workspace = await dao_get_workspace(session_id)
    if restored_workspace:
        logger.info("Restored workspace_root for session %s: %s", session_id, restored_workspace)

    # Reconstruct Agent — Agno will load history from SQLite automatically
    try:
        from agents.base import create_agent_from_definition
        agent = await create_agent_from_definition(
            definition,
            session_id=session_id,          # ← same session_id triggers history load
            game_version=meta.get("game_version", ""),
            module=meta.get("module", ""),
            workspace_root=restored_workspace,
            user_id=email or None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reconstruct agent: {e}")

    meta["email"] = email  # 注入当前用户 email
    # Register into SessionManager (idempotent, no MongoDB write)
    await session_manager.register_existing(session_id, agent, meta)
    return agent


def _expand_activated_skills(
    skill_names: list,
    skill_loader,
    session_state,
) -> str:
    """Expand on-demand Skill prompts and return formatted injection text.

    For each valid skill name:
    1. Look up the SkillDefinition from skill_loader
    2. Expand its prompt template
    3. If Phase markers exist, inject only Phase 1 and cache phases
    4. Update session_state.activated_skills (append+dedup) and skill_summaries

    Returns:
        Formatted string to prepend to user_content, or empty string.
    """
    from skills.expander import expand_skill_prompt
    from skills.phase_cache import parse_skill_phases, build_phase_cache

    sections = []
    any_activated = False

    for skill_name in skill_names:
        skill_def = skill_loader.get(skill_name)
        if skill_def is None:
            logger.warning("activate_skills: unknown skill '%s' — skipping", skill_name)
            continue

        # Expand the skill prompt template
        try:
            expanded = expand_skill_prompt(
                template=skill_def.prompt_template,
                base_dir=skill_def.base_dir,
                args=skill_def.args,
                arguments="",
            )
        except Exception:
            logger.warning("activate_skills: failed to expand skill '%s'", skill_name, exc_info=True)
            continue

        # Phase-aware handling
        content = expanded
        try:
            parse_result = parse_skill_phases(expanded)
            if parse_result and parse_result.phases:
                # Inject only Phase 1
                phase_1 = parse_result.phases[0]
                parts = []
                if parse_result.global_preamble:
                    parts.append(parse_result.global_preamble)
                parts.append(phase_1.content)
                if parse_result.global_constraints:
                    parts.append(parse_result.global_constraints)
                # Include Phase 1 required/suggested read hints
                phase_reads = parse_result.required_reads.get(0, []) + parse_result.suggested_reads.get(0, [])
                if phase_reads:
                    read_lines = "\n".join(f"- `{r}`" for r in phase_reads)
                    parts.append(f"**Phase 1 参考文件:**\n{read_lines}")
                content = "\n\n".join(parts)

                # Cache phases in session state — warn if overwriting existing phase tracking
                prev_skill = getattr(session_state, "active_skill_name", None)
                if prev_skill and prev_skill != skill_name:
                    logger.warning(
                        "Phase state for '%s' will be overwritten by '%s'",
                        prev_skill, skill_name,
                    )
                cache = build_phase_cache(skill_name, str(skill_def.base_dir), parse_result)
                session_state.active_skill_name = skill_name
                session_state.active_skill_phase = 0
                session_state.skill_phase_cache = cache.to_dict()
        except Exception as exc:
            logger.warning("Phase parsing failed for skill '%s': %s", skill_name, exc)

        sections.append(f"## Skill: {skill_name}\n=== Skill Instructions ===\n{content}\n=== End Skill Instructions ===")
        any_activated = True

        # Update session state: append+dedup activated_skills
        if skill_name not in session_state.activated_skills:
            session_state.activated_skills.append(skill_name)

        # Store summary (fallback to description if empty)
        summary = skill_def.summary or skill_def.description
        session_state.skill_summaries[skill_name] = summary

    if not sections:
        return ""

    # Set transient flag for reminder_pre_hook coordination
    if any_activated:
        session_state._skill_just_activated = True

    return "[已激活 Skills]\n\n" + "\n\n".join(sections)


@api_router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse, tags=["sessions"])
async def send_message(
    session_id: str,
    req: SendMessageRequest,
    current_user: str | None = Depends(get_current_user),
):
    """Send a message to an agent session (starts async execution)."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    require_llm_configured()
    record = session_manager.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")

    agent = session_manager.get_agent(session_id)
    if agent is None:
        raise HTTPException(status_code=400, detail="No agent attached to session")

    message_id = str(uuid.uuid4())

    # ── Dynamic Skill activation ────────────────────────────────────────
    skill_prefix = ""
    if req.activate_skills:
        skill_loader = getattr(app.state, "skill_loader", None)
        state = getattr(agent, "session_state", None)
        if skill_loader and state:
            # DB fallback: 用户上传的 skill 可能尚未进入 loader 内存缓存（上传后不重启服务）。
            # 用它作为真相源逐个补齐缓存，避免 _expand_activated_skills 因 miss 静默跳过
            # → 无 [已激活 Skills] 前缀 → agent 不直驱选中 skill。
            try:
                from core.config import settings as _cfg
                from db.mongo import get_motor_db
                from pathlib import Path as _Path
                from skills.materializer import load_skill_from_db
                _mdb = get_motor_db()
                if _mdb is not None:
                    _target = _Path(_cfg.user_skills_dir).expanduser()
                    _target.mkdir(parents=True, exist_ok=True)
                    for _name in req.activate_skills:
                        if skill_loader.get(_name) is None:
                            await load_skill_from_db(_mdb, current_user, _name, skill_loader, _target)
            except Exception as _e:
                logger.warning(
                    "send_message: activate_skills DB fallback failed: %s", _e, exc_info=True,
                )
            skill_prefix = _expand_activated_skills(req.activate_skills, skill_loader, state)

    # ── Workspace context injection ─────────────────────────────────────
    user_content = req.content
    if req.workspace_context and req.workspace_context.selected_paths:
        # Only inject if session has a workspace_root
        ws_root = None
        state = getattr(agent, "session_state", None)
        if state:
            ws_root = getattr(state, "workspace_root", None)
        if ws_root:
            import os
            lines = ["[工作区上下文]", "用户指定了以下文件/目录:"]
            for sel_path in req.workspace_context.selected_paths:
                abs_p = os.path.join(ws_root, sel_path)
                entry_type = "目录" if os.path.isdir(abs_p) else "文件"
                lines.append(f"- {abs_p} ({entry_type})")
            lines.append("")
            lines.append("[用户消息]")
            lines.append(req.content)
            user_content = "\n".join(lines)

    # ── Uploaded files context injection ─────────────────────────────────
    # Inject [用户上传文件] block (abs_path list) before [用户消息] marker.
    # Block order w/ workspace_context: [工作区上下文] → [用户上传文件] → [用户消息].
    # Entries whose abs_path is NOT in session_state.uploads are skipped + warned
    # (stale ref, deleted file, or fabricated client payload) — never block send.
    if req.uploaded_files:
        from api.upload_inject import inject_uploaded_files_block
        state = getattr(agent, "session_state", None)
        session_uploads = getattr(state, "uploads", None) if state else []
        user_content = inject_uploaded_files_block(
            user_content, req.uploaded_files, session_uploads or [], session_id,
        )

    # ── Prepend Skill instructions to user_content ────────────────────
    if skill_prefix:
        user_content = skill_prefix + "\n\n[用户消息]\n" + user_content

    # If session was ABORTED, clear the abort signal so the new run can proceed.
    # This handles the case where POST /messages is called directly without
    # POST /resume (or if /resume already cleared it, this is a safe no-op).
    current_status = session_manager.get_status(session_id)
    if current_status == SessionStatus.ABORTED:
        session_manager.clear_abort(session_id)

    # Atomically check-and-set to RUNNING to prevent duplicate concurrent runs.
    # This closes the race window between status check and set_running.
    acquired = await session_manager.try_set_running(session_id)
    if not acquired:
        raise HTTPException(status_code=409, detail="Session is already running")

    # seed the seq counter before the run's first
    # append so a restarted process resumes numbering above every previously
    # issued seq_id (stale client high-water marks stay behind new events).
    try:
        from api.event_store import event_store

        await event_store.ensure_counter_seeded(session_id)
    except Exception:
        logger.warning(
            "send_message: counter seed failed (session=%s)", session_id, exc_info=True
        )

    # Fire-and-forget: update session title (first message only) and last_active_at
    if current_user:
        asyncio.create_task(
            _update_session_activity(
                email=current_user,
                session_id=session_id,
                message_content=req.content,
            )
        )

    # Run agent in background (already marked RUNNING above)
    # ── 透传登录身份到 session_state._session_extra_data.real_user_id ──
    # direct chat 路径 stream_agent_events 不经 agent_os_adapter，需在此显式注入。
    _session_extra: dict | None = None
    if current_user:
        _session_extra = {"real_user_id": current_user}
    task = asyncio.create_task(
        _run_agent_task(session_id, agent, user_content, message_id, stream=req.stream, session_extra=_session_extra),
        name=f"agent_task_{session_id[:8]}",
    )
    session_manager.set_task(session_id, task)

    return SendMessageResponse(
        message_id=message_id,
        session_id=session_id,
        status="accepted",
    )


async def _run_agent_task(session_id: str, agent: Any, message: str, message_id: str, *, stream: bool = True, session_extra: dict | None = None) -> None:
    """Background task: run agent and stream events to WebSocket queues.

    Note: session is already set to RUNNING by try_set_running() in send_message
    before this task is created, so no set_running() call is needed here.

    Every event is persisted to the EventStore (with a sequential ``seq_id``)
    *before* being pushed to live SSE/WS consumers, so that reconnecting
    clients can replay missed events.
    """
    from api.event_store import event_store

    _agent_task = asyncio.current_task()
    _abort_watcher: asyncio.Task | None = None

    try:
        from core.stream_adapter import stream_agent_events

        _abort_event = session_manager.get_abort_event(session_id)
        _was_aborted = False

        _record = session_manager.get(session_id)
        _user_id = _record.email if _record and _record.email else None

        # Abort monitoring via task cancellation (not event-level race).
        # A watcher task waits on abort_event; when fired, it cancels
        # the whole agent task.  CancelledError propagates through
        # stream_agent_events → _save_agent_session_on_abort → cleanup.
        if _abort_event is not None:
            _abort_watcher = asyncio.create_task(
                _watch_abort(
                    session_id, getattr(agent, "name", "") or "", _abort_event, _agent_task,
                ),
                name=f"abort_watcher_{session_id[:8]}",
            )

        async for event in stream_agent_events(
            agent, message, session_id, stream=stream, user_id=_user_id, session_extra=session_extra
        ):
            # Persist to EventStore first (assigns seq_id in-place)
            event_store.append(session_id, event)

            # Then push to live SSE/WS consumers
            await session_manager.push_ws_event(session_id, event)

            # ── Log event to file ──
            _log_event(event)

            # Detect abort event from stream_adapter
            if event.get("event_type") == "run_aborted":
                _was_aborted = True

            # Invalidate cached context-usage after a completed run — the
            # breakdown is stale until recomputed against the new messages.
            # approval_pending 同理（暂停即终态、不发 run_complete，
            # 上下文已增长半轮）。
            if event.get("event_type") in ("run_complete", "approval_pending", "clarification_request"):
                from core.context_usage import invalidate as _invalidate_context_usage

                _invalidate_context_usage(session_id)

        if _abort_watcher is not None and not _abort_watcher.done():
            _abort_watcher.cancel()

        if _was_aborted:
            await session_manager.set_aborted(session_id)
            # Clear the abort signal so the session can be resumed later
            session_manager.clear_abort(session_id)
        else:
            await session_manager.set_completed(session_id)
            # Clean up persisted events — Agno has saved the full session
            await event_store.clear_persisted(session_id)

    except asyncio.CancelledError:
        await session_manager.set_aborted(session_id)
        session_manager.clear_abort(session_id)
        # CancelledError handler: stream_agent_events already yielded
        # run_aborted (with session save), so we only need to set
        # session state.  Re-raise so outer context knows the task
        # was cancelled.
        raise

    except Exception as e:
        logger.exception("Agent task failed (session=%s)", session_id)
        await session_manager.set_failed(session_id, str(e))
        error_event = {
            "event_type": "run_error",
            "session_id": session_id,
            "error": str(e),
        }
        event_store.append(session_id, error_event)
        await session_manager.push_ws_event(session_id, error_event)
    finally:
        # Cancel watcher if still running (normal completion path)
        if _abort_watcher is not None and not _abort_watcher.done():
            try:
                _abort_watcher.cancel()
            except Exception:
                pass
        # Clear the task handle so the session knows no task is running
        record = session_manager.get(session_id)
        if record:
            record._task = None


@api_router.get("/sessions/{session_id}/status", response_model=SessionStatusResponse, tags=["sessions"])
async def get_session_status(session_id: str):
    """Get current session status and any pending interrupt payload.

    If the session is not currently active in memory but exists in persistent
    storage (SQLite), returns status='inactive' with restorable=True instead of 404.
    """
    record = session_manager.get(session_id)
    if record is None:
        # Fallback: check persistent storage
        if await session_manager.exists_in_storage(session_id):
            return SessionStatusResponse(
                session_id=session_id,
                status=SessionStatus.INACTIVE,
                agent_name="",
                mode="",
                game_version="",
                module="",
                restorable=True,
            )
        raise HTTPException(status_code=404, detail="Session not found")

    from hooks.interrupt_manager import interrupt_manager
    interrupt_payload = interrupt_manager.pending_payload(session_id)

    return SessionStatusResponse(**record.to_status_dict(interrupt_payload=interrupt_payload))


@api_router.get("/sessions/{session_id}/context-usage", response_model=ContextUsageResponse, tags=["sessions"])
async def get_session_context_usage(session_id: str, force: bool = False):
    """Context window breakdown + cumulative token consumption for a session.

    Active sessions get the full breakdown (system_prompt / tools / mcp_tools /
    messages / free_space + segments). Inactive sessions degrade: agent-side
    fields are null, messages + usage_totals come from persisted AgentSession
    runs. 404 when the session exists neither in memory nor in storage.

    Token breakdown for the context-usage display.
    """
    from core.context_usage import compute_context_usage

    record = session_manager.get(session_id)
    agent = getattr(record, "agent", None) if record is not None else None

    response = await compute_context_usage(session_id, agent=agent, force=force)
    if response is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return response


@api_router.get("/sessions/{session_id}/stream/last-seq", response_model=LastSeqResponse, tags=["sessions"])
async def get_session_last_seq(session_id: str):
    """Lightweight probe for the frontend staleness detector.

    Returns the backend's current ``last_seq_id`` and ``status`` so the
    frontend can compare against its local ``lastSeqId`` without disturbing
    the existing WS connection. Used by ``StreamPoolManager.probeConnection``
    to distinguish "LLM thinking" from "WS silently dropped".

    Implements the "backend last-seq probe endpoint".
    """
    from api.event_store import event_store
    from core.storage_reader import read_history_stats

    record = session_manager.get(session_id)
    if record is None:
        # Session not in memory — check persistent storage. Return seq_id=0
        # for inactive sessions; the frontend reconnect flow will restore the
        # session and reload /messages (authoritative source).
        if await session_manager.exists_in_storage(session_id):
            logger.info(
                "last-seq probe session=%s status=inactive last_seq_id=0",
                session_id,
            )
            return LastSeqResponse(
                session_id=session_id,
                last_seq_id=0,
                status=SessionStatus.INACTIVE,
            )
        raise HTTPException(status_code=404, detail="Session not found")

    last_seq = event_store.latest_seq(session_id)
    status = record.status if record.status is not None else SessionStatus.IDLE
    # D14 history_version probe fields — omitted (None) when storage is
    # unavailable so old clients / degraded storage stay unaffected.
    history_stats = await read_history_stats(session_id)
    logger.info(
        "last-seq probe session=%s status=%s last_seq_id=%d",
        session_id,
        status.value,
        last_seq,
    )
    return LastSeqResponse(
        session_id=session_id,
        last_seq_id=last_seq,
        status=status,
        total_runs=history_stats["total_runs"] if history_stats else None,
        total_messages=history_stats["total_messages"] if history_stats else None,
    )


@api_router.post("/sessions/{session_id}/review", response_model=ReviewResponse, tags=["sessions"])
async def review_interrupt(session_id: str, req: ReviewRequest):
    """Submit human review decision for a pending interrupt."""
    record = session_manager.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from hooks.interrupt_manager import interrupt_manager

    resolved = interrupt_manager.resolve_interrupt(
        session_id=session_id,
        action=req.action,
        modified_content=req.modified_content,
        notes=req.notes,
    )

    if not resolved:
        # Distinguish between "timed out" and "never had an interrupt"
        session_status = record.status.value if record.status else ""
        if session_status == "completed":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "interrupt_already_expired",
                    "message": "验收等待已超时，任务已自动完成，无需再次操作。",
                    "session_status": session_status,
                },
            )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "no_pending_interrupt",
                "message": "当前 session 没有待处理的验收请求。",
                "session_status": session_status,
            },
        )

    # pass / fail → session is done; no further agent turn needed.
    # rerun / investigate → agent needs to continue, resume running state.
    if req.action in ("pass", "fail"):
        await session_manager.set_completed(session_id)
    else:
        await session_manager.set_running(session_id)

    return ReviewResponse(session_id=session_id, action=req.action, resolved=True)


@api_router.post("/sessions/{session_id}/abort", tags=["sessions"])
async def abort_session(session_id: str):
    """Immediately signal a running session to abort (task 5.4).

    Sets the abort_event so the stream_adapter loop and abort_pre_hook
    stop execution at the next safe check point.
    """
    record = session_manager.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")

    session_manager.set_abort(session_id)
    return {"status": "aborting", "session_id": session_id}


@api_router.post("/sessions/{session_id}/resume", tags=["sessions"])
async def resume_session(session_id: str, req: dict):
    """Resume a paused/interrupted session (task 5.5).

    Handles two types:
    - type="review_approved": reviewer has submitted a decision; resolves
      the pending interrupt_manager record to unblock request_review.
    - type="user_resume": user sends a follow-up message; stores context
      in session_manager for the next arun() call.

    Body schema:
      {
        "type": "review_approved" | "user_resume",
        "message": "<optional human note>",
        "approved": true | false          # only for review_approved
      }
    """
    record = session_manager.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Clear any lingering abort signal so the resumed run can proceed
    session_manager.clear_abort(session_id)

    resume_type = req.get("type", "user_resume")
    message = req.get("message", "")
    approved = req.get("approved", True)

    if resume_type == "review_approved":
        from hooks.interrupt_manager import interrupt_manager

        action = "pass" if approved else "fail"
        resolved = interrupt_manager.resolve_interrupt(
            session_id=session_id,
            action=action,
            notes=message,
        )
        if not resolved:
            logger.warning("resume_session: no pending interrupt for session %s", session_id)

        return {"status": "resumed", "session_id": session_id, "resume_type": "review_approved"}

    else:
        # user_resume: abort signal already cleared above.
        # The user's follow-up message will be sent via POST /messages,
        # which triggers agent.arun() with full history from DB.
        return {"status": "resumed", "session_id": session_id, "resume_type": "user_resume"}



@api_router.patch("/users/{email}/sessions/{session_id}", tags=["users"])
async def update_user_session(
    email: str,
    session_id: str,
    req: UpdateSessionRequest,
    current_user: str | None = Depends(get_current_user),
):
    """Update session metadata (e.g. title). Requires authentication."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user != email:
        raise HTTPException(status_code=403, detail="Cannot modify another user's sessions")

    from db.user_sessions import user_sessions_dao
    ok = await user_sessions_dao.update_session_meta(
        email, session_id, title=req.title, force=True,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found in user mapping")
    return {"session_id": session_id, "title": req.title}


@api_router.put("/users/{email}/sessions/{session_id}/visibility", tags=["users"])
async def update_session_visibility(
    email: str,
    session_id: str,
    req: UpdateSessionVisibilityRequest,
    current_user: str | None = Depends(get_current_user),
):
    """Toggle session visibility (hide/unhide). Requires authentication.

    Persists the ``hidden`` flag on the SessionEntry so the sidebar can
    collapse hidden sessions into a separate section while search still
    finds them. Idempotent (PUT semantics).
    """
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user != email:
        raise HTTPException(status_code=403, detail="Cannot modify another user's sessions")

    from db.user_sessions import user_sessions_dao
    ok = await user_sessions_dao.update_session_meta(
        email, session_id, hidden=req.hidden,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found in user mapping")
    return {"session_id": session_id, "hidden": req.hidden}


# ── Background cascade cleanup ──────────────
# Registry keeps cleanup tasks referenced so they are not GC'd mid-flight;
# drained with a short timeout on app shutdown (startup_recovery reconciles
# leftovers that did not finish).
_pending_cleanups: set[asyncio.Task] = set()


async def _drain_pending_cleanups(timeout: float = 2.0) -> None:
    """Best-effort wait for in-flight background cascade cleanups on shutdown."""
    pending = [t for t in _pending_cleanups if not t.done()]
    if not pending:
        return
    logger.info(
        "Shutdown: waiting for %d background session cleanup(s) (timeout=%.1fs)",
        len(pending), timeout,
    )
    _done, still_pending = await asyncio.wait(pending, timeout=timeout)
    for t in _done:
        exc = t.exception()
        if exc is not None:
            logger.warning("Background cleanup task ended with exception: %s", exc)
    if still_pending:
        logger.warning(
            "Shutdown: %d background cleanup task(s) still pending "
            "(startup_recovery will reconcile leftovers)",
            len(still_pending),
        )


async def _delete_agno_session(session_id: str) -> None:
    """Delete the Agno storage session (sync pymongo — offloaded to a thread)."""
    from core.storage import get_storage

    storage = get_storage()
    delete_fn = getattr(storage, "delete_session", None)
    if delete_fn is None:
        logger.warning(
            "cascade cleanup: storage has no delete_session method, "
            "skipping agno_sessions cleanup"
        )
        return
    await asyncio.to_thread(delete_fn, session_id=session_id)


async def _cascade_cleanup_session(session_id: str) -> None:
    """Background cascade delete across all persistent storage layers.

    Parallel best-effort: every branch is independently try/except-logged so
    a single failure never stops the rest. The seq watermark delete stays
    sequenced AFTER ``event_store.clear_persisted`` (which re-writes the
    watermark).

    "持久化级联清理 MUST 后台执行且并行化".
    """
    from api.event_store import event_store
    from api.session_uploads import clean_session_upload_dir
    from db.models import QaArtifactVersion, QaPhaseCheckpoint, SessionSeqWatermark
    from db.workspace import delete_workspace
    from memory.knowledge_hub import delete_session_vectors

    async def _events_branch() -> None:
        try:
            await event_store.clear_persisted(session_id)
        except Exception:
            logger.warning(
                "cascade cleanup: failed to clear persisted events for %s",
                session_id, exc_info=True,
            )
            return
        try:
            await SessionSeqWatermark.find(
                SessionSeqWatermark.session_id == session_id
            ).delete()
        except Exception:
            logger.warning(
                "cascade cleanup: failed to delete seq watermark for %s",
                session_id, exc_info=True,
            )

    async def _guarded(name: str, aw) -> None:
        try:
            await aw
        except Exception:
            logger.warning(
                "cascade cleanup: %s failed for %s",
                name, session_id, exc_info=True,
            )

    await asyncio.gather(
        _guarded("session_events+seq_watermark", _events_branch()),
        _guarded("workspaces", delete_workspace(session_id)),
        _guarded(
            "qa_phase_checkpoints",
            QaPhaseCheckpoint.find(QaPhaseCheckpoint.session_id == session_id).delete(),
        ),
        _guarded(
            "qa_artifact_versions",
            QaArtifactVersion.find(QaArtifactVersion.session_id == session_id).delete(),
        ),
        _guarded("agno_session", _delete_agno_session(session_id)),
        _guarded("milvus_vectors", delete_session_vectors(session_id)),
        _guarded(
            "upload_dir",
            asyncio.to_thread(clean_session_upload_dir, session_id),
        ),
    )
    logger.info("cascade delete complete for %s", session_id)


@api_router.delete("/users/{email}/sessions/{session_id}", tags=["users"])
async def delete_user_session(email: str, session_id: str):
    """Delete a session: fast in-memory removal now, cascade cleanup in background.

    Request path (fast, no remote round trips beyond the user_sessions $pull):
      1. In-memory session removal + running task cancellation (no grace wait).
         Sessions missing from memory (backend restart / failed restore) fall
         back to the user_sessions DB record instead of 404-ing — otherwise
         they could never be deleted.
      2. MongoDB user_sessions SessionEntry $pull — closes the
         delete-vs-restore race (a restore for the same id 404s right after)
      3. Agno storage session delete (sync) — closes the delete-vs-status-probe
         race that produced ghost "Agent" list entries via GET /status's
         inactive fallback

    Background (best-effort, failures logged, parallel): session_events +
    seq watermark, workspaces, qa_phase_checkpoints, qa_artifact_versions,
    Agno session (safety net for teardown writes), Milvus vectors, upload
    temp dir.

    "删除会话接口 MUST 快速返回".
    """
    from db.user_sessions import user_sessions_dao

    record = session_manager.get(session_id)
    if record is None:
        # Not in memory (backend restart, failed/coordinator restore, etc.).
        # A bare 404 here made any session that exists only in user_sessions
        # undeletable — every request 404'd before the DB $pull ever ran.
        # Fall back to the DB record: delete when user_sessions still lists
        # it, 404 only when it truly does not exist.
        meta = await user_sessions_dao.get_session_meta(email, session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        was_running = False
    else:
        was_running = record.status == SessionStatus.RUNNING
        # Fast path: in-memory cleanup + task cancellation (no grace wait, no
        # persisted cleanup — the cancelled task's teardown runs in background).
        await session_manager.remove_from_memory(session_id)

    # user_sessions $pull stays in the request path: once removed, a racing
    # restore_session for the same id fails its meta lookup with 404 instead
    # of reading half-deleted data.
    try:
        await user_sessions_dao.remove_session(email, session_id)
    except Exception:
        logger.warning(
            "delete_user_session: failed to remove user_sessions entry for "
            "%s/%s (background cleanup will still run)",
            email, session_id, exc_info=True,
        )

    # Agno storage session is deleted SYNCHRONOUSLY in the request path.
    # GET /sessions/{id}/status falls back to a 200 'inactive' response while
    # the agno row still exists; if that row survived past this request (the
    # old background-only cascade), SessionPage's on-demand status probe turns
    # it into a ghost "Agent" list entry (agent_name='' → 'Agent' fallback,
    # created_at=now, unopenable because user_sessions was already pulled).
    # The cascade below keeps the agno delete as a safety net for the
    # cancelled task's teardown writes.
    try:
        await _delete_agno_session(session_id)
    except Exception:
        logger.warning(
            "delete_user_session: sync agno session delete failed for %s "
            "(cascade safety net will retry)",
            session_id, exc_info=True,
        )

    # Everything else → background cascade, registered against GC.
    task = asyncio.create_task(_cascade_cleanup_session(session_id))
    _pending_cleanups.add(task)
    task.add_done_callback(_pending_cleanups.discard)

    return {
        "deleted": True,
        "session_id": session_id,
        "was_running": was_running,
        "cleanup": "background",
    }


# ---------------------------------------------------------------------------
# WebSocket streaming endpoint (sole real-time event channel)
# ---------------------------------------------------------------------------

@api_router.websocket("/sessions/{session_id}/stream/ws")
async def websocket_stream_ws(websocket: WebSocket, session_id: str):
    """Stream agent execution events in real-time via WebSocket.

    Supports reconnection: the client may send a JSON message with
    ``{"last_event_id": <int>}`` immediately after connection to replay
    missed events.  Alternatively, pass ``last_event_id`` as a query
    parameter: ``/stream/ws?last_event_id=42``.
    """
    from api.event_store import event_store

    if session_manager.get(session_id) is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()

    # Determine last_event_id from query parameter
    last_event_id = 0
    try:
        qp = websocket.query_params.get("last_event_id")
        if qp is not None:
            last_event_id = int(qp)
    except (ValueError, TypeError):
        pass

    # Register queue FIRST to avoid race between replay and new events
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    session_manager.add_ws_queue(session_id, queue)

    try:
        # ── Phase 1: Replay missed events from EventStore ──
        replayed_max_seq = last_event_id
        missed = event_store.replay(session_id, after_seq=last_event_id)
        for ev in missed:
            seq = ev.get("seq_id", 0)
            if seq > replayed_max_seq:
                replayed_max_seq = seq
            await websocket.send_text(json.dumps(ev, ensure_ascii=False))

        # ── Phase 2: Live event consumption (dedup via replayed_max_seq) ──
        # Connection persists for the lifetime of the session — does NOT
        # close on terminal events (run_complete / run_error / run_aborted).
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                seq = event.get("seq_id", 0)

                # Skip events already sent during replay
                if seq and seq <= replayed_max_seq:
                    continue

                await websocket.send_text(json.dumps(event, ensure_ascii=False))
            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                await websocket.send_text(json.dumps({"event_type": "heartbeat"}))

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected (session=%s)", session_id)
    except Exception:
        logger.exception("WebSocket error (session=%s)", session_id)
    finally:
        session_manager.remove_ws_queue(session_id, queue)
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Skills, Agents, Health, Config endpoints
# ---------------------------------------------------------------------------

@api_router.get("/skills", response_model=list[SkillInfo], tags=["discovery"])
async def list_skills():
    """List all available skills."""
    skill_loader = getattr(app.state, "skill_loader", None)
    if skill_loader is None:
        return []
    return skill_loader.to_api_list()


@api_router.get("/agents", response_model=list[AgentInfo], tags=["discovery"])
async def list_agents():
    """List all available agents (builtin + custom)."""
    from agents.registry import agent_registry
    return agent_registry.to_api_list()


@api_router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check():
    """System health check.

    Milvus 三态:
    - ``connected``  → milvus 启用且 hub 初始化成功 → overall=ok
    - ``disconnected`` → milvus 启用但 hub 初始化失败 → overall=degraded
    - ``disabled``    → milvus 主动关闭 → overall=ok (非故障)

    milvus_enabled 判定来源: settings.milvus_enabled (.env / 环境变量)
    """
    from memory.knowledge_hub import get_knowledge_hub
    from core.config import settings

    # 主动关闭 (运维/网络故障临时降级) 不算 degraded。
    if not settings.milvus_enabled:
        return HealthResponse(
            status="ok",
            milvus="disabled",
            storage="ok",
            version=settings.app_version,
        )

    hub = get_knowledge_hub()
    milvus_status = "connected" if hub is not None else "disconnected"

    overall = "ok" if milvus_status == "connected" else "degraded"
    return HealthResponse(
        status=overall,
        milvus=milvus_status,
        storage="ok",
        version=settings.app_version,
    )


@api_router.get("/idle", response_model=IdleStatusResponse, tags=["system"])
async def get_idle_status():
    """System-level idle probe for update coordination.

    Aggregates in-memory SessionManager status: returns whether any session is
    RUNNING or INTERRUPT_PENDING. Intended to be polled by a supervisor (e.g. a
    deploy/update script or desktop shell) before restarting the process, so it
    does not force-kill active agent tasks.
    No authentication (same trust level as /health), intended for local callers.
    """
    running = session_manager.count_running()
    running_ids = session_manager.get_running_session_ids()
    return IdleStatusResponse(
        idle=(running == 0),
        running_sessions=running,
        running_session_ids=running_ids,
    )


@api_router.get("/config/model-slots", tags=["system"])
async def get_model_slots():
    """Return current model slot configuration and agent-slot mappings."""
    from core.model_slots import get_model_slot_registry, parse_model_id
    from agents.registry import agent_registry

    registry = get_model_slot_registry()
    slot_info = registry.get_slot_info()

    # Build agent→slot mapping from raw definitions
    agent_slots = {}
    for name, definition in agent_registry._definitions.items():
        slot = parse_model_id(definition.model_id)
        agent_slots[name] = {"slot": slot.value}

    return {"slots": slot_info, "agent_slots": agent_slots}


@api_router.get("/config/available-models", response_model=AvailableModelsResponse, tags=["system"])
async def get_available_models():
    """Return model IDs available for UI model selector.

    Each model is returned as a ``ModelInfo`` object with ``supports_vision``
    flag computed from ``settings.llm_vision_models_set`` — the frontend
    FileUploader uses this flag to gate image file upload.
    """
    from core.config import settings

    vision_set = settings.llm_vision_models_set
    models = [
        ModelInfo(name=m, supports_vision=m in vision_set)
        for m in settings.llm_available_models_list
    ]
    return AvailableModelsResponse(models=models, default=settings.llm_model)


@api_router.get("/config", response_model=ConfigResponse, tags=["system"])
async def get_config():
    """Return current configuration (no sensitive values)."""
    from core.config import settings

    return ConfigResponse(
        llm_model=settings.llm_model,
        llm_base_url=settings.llm_base_url,
        storage_backend=settings.storage_backend.value,
        milvus_enabled=settings.milvus_enabled,
        milvus_host=settings.milvus_host,
        default_max_turns=settings.default_max_turns,
        default_permission_mode=settings.default_permission_mode.value,
        project_skills_dir=settings.project_skills_dir,
        mcp_config_path=settings.mcp_config_path,
    )


# ---------------------------------------------------------------------------
# Event logging helper (plain-text events to agent_events.log)
# ---------------------------------------------------------------------------

# Token buffers: per-session accumulation of streaming tokens.
# Keyed by session_id so concurrent sessions don't mix their output.
_token_buffers: dict[str, list[str]] = {}


def _flush_token_buffer(session_id: str) -> None:
    """Flush accumulated token content for *one* session as a single log line."""
    buf = _token_buffers.get(session_id)
    if buf:
        event_logger.info("".join(buf))
        del _token_buffers[session_id]


def _log_event(event: dict) -> None:
    """Log agent events to file in plain text (no ANSI codes).

    Icons:
      token        — (buffered, flushed on next non-token event)
      tool_start   — 🔧
      tool_end     — ✅
      tool_error   — 💥
      run_started  — 🚀
      run_complete — 🏁
      run_error    — ❌
      run_aborted  — 🛑
      interrupt    — ⏸️
      other        — 🔹
    """
    etype = event.get("event_type", "")
    session_id = event.get("session_id", "")

    # Flush buffered tokens before any non-token event
    if etype != "token":
        _flush_token_buffer(session_id)

    if etype == "token":
        content = event.get("content", "")
        if content:
            _token_buffers.setdefault(session_id, []).append(content)

    elif etype == "tool_start":
        tool_name = event.get("tool_name", "?")
        call_id = event.get("tool_call_id", "")
        inputs = event.get("inputs", {})
        inputs_str = json.dumps(inputs, ensure_ascii=False, indent=2) if inputs else "(no inputs)"
        if len(inputs_str) > 1000:
            inputs_str = inputs_str[:1000] + f"\n  ... ({len(inputs_str)} chars total)"
        lines = [
            f"\n{'='*60}",
            f"🔧 TOOL CALL: {tool_name}  [{call_id[:8]}]",
            f"   ┌─ Inputs:",
        ]
        for line in inputs_str.split("\n"):
            lines.append(f"   │ {line}")
        lines.append(f"   └─────────")
        event_logger.info("\n".join(lines))

    elif etype == "tool_end":
        tool_name = event.get("tool_name", "?")
        call_id = event.get("tool_call_id", "")
        outputs = event.get("outputs")
        elapsed = event.get("elapsed_ms", 0)
        outputs_str = str(outputs) if outputs is not None else "(no output)"
        # Always emit a one-line size summary so operators can scan
        # agent_events.log for per-tool response sizes (e.g. file_read,
        # grep) without opening the full preview. Approximate
        # token count uses the standard 4-chars-per-token heuristic
        # (close enough for sizing decisions; Agno's native
        # model.count_tokens is the authoritative count, invoked only
        # by CompressionManager.should_compress).
        total_chars = len(outputs_str)
        approx_tokens = max(1, total_chars // 4)
        preview_str = outputs_str
        if total_chars > 2000:
            preview_str = outputs_str[:2000] + f"\n  ... ({total_chars} chars total)"
        lines = [
            f"\n✅ TOOL DONE: {tool_name} ({elapsed:.0f}ms)  [{call_id[:8]}]",
            f"   📏 size: {total_chars} chars ≈ {approx_tokens} tokens",
            f"   ┌─ Output:",
        ]
        for line in preview_str.split("\n")[:30]:
            lines.append(f"   │ {line}")
        if preview_str.count("\n") > 30:
            lines.append(f"   │ ... (output truncated, {preview_str.count(chr(10))} lines total)")
        lines.append(f"   └─────────")
        lines.append(f"{'='*60}")
        event_logger.info("\n".join(lines))

    elif etype == "tool_error":
        tool_name = event.get("tool_name", "?")
        call_id = event.get("tool_call_id", "")
        error = event.get("error", "unknown")
        lines = [
            f"\n{'='*60}",
            f"💥 TOOL ERROR: {tool_name}  [{call_id[:8]}]",
            f"   Error: {error}",
            f"{'='*60}",
        ]
        event_logger.info("\n".join(lines))

    elif etype == "run_started":
        agent_name = event.get("agent_name", "")
        event_logger.info(f"\n🚀 AGENT RUN STARTED: {agent_name}")

    elif etype == "run_complete":
        final = event.get("final_response", "")
        event_logger.info(f"\n🏁 RUN COMPLETE\n   Response length: {len(final)} chars")

    elif etype == "run_error":
        error = event.get("error", "unknown")
        event_logger.info(f"\n❌ RUN ERROR: {error}")

    elif etype == "interrupt_request":
        payload = event.get("payload", {})
        event_logger.info(f"\n⏸️  INTERRUPT REQUEST\n   {json.dumps(payload, ensure_ascii=False)}")

    elif etype == "compression":
        # L2 CompressionManager in-run compression event. Emitted by Agno
        # when ``compression_manager`` (configured via
        # ``core/memory_setup.get_compression_agent_kwargs``) collapses
        # accumulated tool results DURING arun(). This is the authoritative
        # proof that L2 actually fired — the previous delta<0 detection
        # in ``context_threshold_hook._update_token_count`` was unreliable
        # because compression happens between tool calls, not between
        # post_hook measurements (delta is usually still positive after a
        # turn that produced new content).
        stage = event.get("stage", "?")
        agent_name = event.get("agent_name", "?")
        session_id = event.get("session_id", "") or ""
        if stage == "started":
            event_logger.info(
                "📦 [压缩] L2 开始: agent=%s session=%s",
                agent_name, session_id,
            )
        else:
            trc = event.get("tool_results_compressed")
            orig = event.get("original_size")
            comp = event.get("compressed_size")
            saved = (orig - comp) if (orig is not None and comp is not None) else None
            event_logger.info(
                "📦 [压缩] L2 已触发: agent=%s session=%s "
                "tool_results=%s original=%s compressed=%s saved=%s",
                agent_name, session_id,
                trc if trc is not None else "?",
                orig if orig is not None else "?",
                comp if comp is not None else "?",
                saved if saved is not None else "?",
            )

    else:
        content_preview = str(event.get("content", ""))[:200]
        event_logger.info(f"\n🔹 EVENT [{etype}]: {content_preview}")


# ---------------------------------------------------------------------------
# Frontend SPA (embedded mode)
# ---------------------------------------------------------------------------
# Serve the built Vite bundle from qa-agent/web/ so a single process can serve
# both the API and the UI — a desktop WebView shell or a browser can load
# http://127.0.0.1:8000/ directly (no separate dev server required).
# Mounted LAST so all API routes above take precedence; unmatched paths fall
# through to the SPA (html=True → index.html at "/").

# Register the /api-prefixed API router before the SPA mount so API routes
# take precedence over the catch-all StaticFiles at "/".
app.include_router(api_router)

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    # SPA 入口 HTML 禁缓存：hash 命名的 assets 可长缓存，但 index.html 引用
    # 的 bundle 文件名随构建变化——入口被浏览器/WebView 启发式缓存后，打包
    # 更新界面仍停留在旧版（实测反馈三连）。HTML 每次拉新即可解决。
    @app.middleware("http")
    async def _no_cache_spa_html(request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET" and (
            request.url.path == "/" or request.url.path.endswith(".html")
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="frontend-spa")

    # SPA fallback: client-side routes (e.g. /login, /chat, /sessions/<id>)
    # don't exist as files, so StaticFiles raises 404. Serve index.html for
    # browser navigation (Accept: text/html) so the SPA router can take over.
    # API/XHR calls (Accept: application/json) still get a JSON 404.
    @app.exception_handler(404)
    async def _spa_fallback(request: Request, exc: HTTPException):
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            index = _WEB_DIR / "index.html"
            if index.is_file():
                return FileResponse(str(index), media_type="text/html")
        return JSONResponse({"detail": "Not Found"}, status_code=404)
