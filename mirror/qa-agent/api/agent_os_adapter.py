"""
QA Agent System — AgentOS-Compatible Adapter

Thin adapter layer that exposes AgentOS-compatible routes on the existing
FastAPI app, bridging to the current SessionManager / AgentRegistry /
create_agent_from_definition infrastructure.

Routes:
  GET  /agents/{agent_id}                                — Agent detail (id, name, description, tools, ...)
  POST /agents/{agent_id}/runs                          — Core dialog interface (SSE streaming or JSON)
  POST /agents/{agent_id}/runs/{run_id}/continue        — HITL: continue a paused run (tools payload)

SSE format is 100% compatible with agno AgentOS (RunStarted / RunResponse /
RunCompleted / RunErrorEvent / RunPaused).  The digital-worker platform can
call these endpoints exactly as it would call a standard AgentOS instance.

Guarantees covered here: "Continue paused run endpoint" / "暂停帧前置落态时序" /
"续跑断连的状态分流".
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from inspect import iscoroutinefunction
from typing import Any, AsyncGenerator, Dict, List, Optional, Union
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from agno.exceptions import RunNotContinuableError, RunNotFoundError
from agno.models.response import ToolExecution
from agno.os.utils import format_sse_event
from agno.run.agent import RunErrorEvent, RunOutput, RunPausedEvent
from agno.run.base import RunStatus
from agno.run.team import TeamRunOutput

from agents.base import AgentDefinition, create_agent_from_definition
from agents.registry import agent_registry
from api._shared import DEFAULT_EMAIL as _DEFAULT_EMAIL
from api.schemas import SessionStatus
from api.session_manager import session_manager
from auth.dependencies import get_current_user
from core.config import settings
from core.partial_run_persist import persist_partial_run
from core.run_lifecycle import (
    read_original_message,
    read_paused_run_id,
    read_run_status,
    write_run_lifecycle,
)
from core.stream_adapter import AbortMessagesCollector, feed_collector_from_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
agent_os_router = APIRouter(tags=["agent-os"])


# ── Section:login email 透传 helper ─────────────────────────────────


def _strip_user_id_prefix(user_id: Optional[str]) -> Optional[str]:
    """Drop the calling platform's user-id prefix when one is configured.

    Some callers deliver ``user_id`` as ``<prefix><id>``. The prefix is caller
    configuration (``AGENT_OS_USER_ID_PREFIX``); empty → the value is passed
    through unchanged.
    """
    prefix = (settings.agent_os_user_id_prefix or "").strip()
    if prefix and user_id and user_id.startswith(prefix):
        return user_id[len(prefix):]
    return user_id


def _inject_login_email(extra: Optional[dict], email: Optional[str]) -> dict:
    """Merge 登录 email 到 _session_extra_data,不覆盖已存在的 real_user_id。

    Args:
        extra: 原 _session_extra_data dict,None 视为 {}。
        email: 当前登录用户 email(Cookie get_current_user 解析),None/空跳过。

    Returns:
        处理后的 dict(含 real_user_id 若 email 非空且原 extra 未含该 key)。
        若 extra 为 None 返回新 dict,保证调用方拿到非 None。
    """
    if extra is None:
        extra = {}
    if not isinstance(extra, dict):
        logger.warning(
            "Adapter: _session_extra_data is not dict (type=%s), "
            "skip login email inject", type(extra).__name__,
        )
        return extra if isinstance(extra, dict) else {}
    if email and not extra.get("real_user_id"):
        extra["real_user_id"] = email
        logger.info("Adapter: injected login email into real_user_id (email=%s)", email)
    return extra


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}  — agent detail
# ---------------------------------------------------------------------------

@agent_os_router.get("/agents/{agent_id}")
async def get_agent_detail(agent_id: str):
    """Return metadata for a single agent by its registered name/id."""
    definition: Optional[AgentDefinition] = agent_registry.get(agent_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    return {
        "id": definition.agent_id or definition.name,
        "name": definition.name,
        "description": definition.description,
        "tools": definition.tool_names,
        "agent_type": getattr(definition, 'agent_type', None),
        "tags": getattr(definition, 'tags', []),
        "when_to_use": getattr(definition, 'when_to_use', None),
        "permission_mode": getattr(definition, 'permission_mode', None),
    }


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/runs  — core dialog interface
# ---------------------------------------------------------------------------

@agent_os_router.post("/agents/{agent_id}/runs")
async def create_agent_run(
    agent_id: str,
    message: str = Form(..., description="The input message or prompt to send to the agent"),
    stream: bool = Form(True, description="Enable streaming responses via SSE"),
    session_id: Optional[str] = Form(None, description="Session ID for multi-turn continuity"),
    user_id: Optional[str] = Form(None, description="User identifier"),
    session_extra_data: Optional[str] = Form(
        None,
        description=(
            "JSON object injected as session_state._session_extra_data "
            "(platform-delivered connector info, e.g. sandbox connector "
            "{name:'sandbox', project_metadata:{url:...}} plus sandbox_id / "
            "group_sandbox_id)."
        ),
    ),
    session_state: Optional[str] = Form(
        None,
        description=(
            "Full session state JSON sent by remote AgentOS. Contains "
            "_session_extra_data (sandbox_id, group_sandbox_id, connectors) "
            "nested inside."
        ),
    ),
    current_user: str | None = Depends(get_current_user),
):
    """Execute an agent with a message.  Fully compatible with AgentOS protocol.

    - ``stream=true`` (default): returns SSE event stream (text/event-stream)
    - ``stream=false``: returns JSON with content, session_id, run_id
    """
    # D15: refuse an LLM-backed run when no provider key is configured.
    # Function-local import — api.server imports this module, so a module-level
    # import would be circular.
    from api.server import require_llm_configured
    require_llm_configured()

    # 0. Strip the upstream platform's user-id prefix when one is configured.
    user_id = _strip_user_id_prefix(user_id)

    # 1. Validate agent_id
    definition: Optional[AgentDefinition] = agent_registry.get(agent_id)
    if definition is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found",
        )

    # 2. Session management  -----------------------------------------------
    # 2a. No session_id → create new session
    if not session_id:
        session_id = await session_manager.create_session(
            agent_name=agent_id,
            mode="normal",
            email=_DEFAULT_EMAIL,
        )
        logger.info("Adapter: auto-created session %s for agent '%s'", session_id, agent_id)

    # 2b. session_id provided but not in SessionManager → register it
    record = session_manager.get(session_id)
    if record is None:
        record = await session_manager.register_existing(
            session_id=session_id,
            agent=None,
            meta={
                "agent_name": agent_id,
                "mode": "normal",
                "email": _DEFAULT_EMAIL,
            },
        )
        logger.info("Adapter: registered external session %s", session_id)

    # 2c. Concurrency guard
    if record.status == SessionStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Session is already running",
        )

    # 2d. Attach agent if missing
    if record.agent is None:
        agent = await create_agent_from_definition(
            definition,
            session_id=session_id,
            user_id=user_id,
        )
        await session_manager.attach_agent(session_id, agent)
        logger.info("Adapter: attached agent '%s' to session %s", agent_id, session_id)
    else:
        agent = record.agent

    # ── Parse connector info from request ──
    # Priority: session_state._session_extra_data (remote AgentOS) >
    #           session_extra_data (platform-compat payload)
    _session_extra: Optional[dict] = None

    if session_state:
        try:
            parsed_state = _json.loads(session_state)
        except (ValueError, TypeError) as exc:
            logger.warning("Adapter: session_state is not valid JSON (%s)", exc)
        else:
            if isinstance(parsed_state, dict):
                inner = parsed_state.get("_session_extra_data")
                if isinstance(inner, dict):
                    _session_extra = inner
                    logger.info(
                        "Adapter: extracted _session_extra_data from session_state "
                        "(agent=%s, sandbox_id=%s, connectors=%s)",
                        agent_id,
                        inner.get("sandbox_id", "MISSING"),
                        [c.get("name") for c in (inner.get("connectors") or [])
                         if isinstance(c, dict)],
                    )

    if _session_extra is None and session_extra_data:
        try:
            parsed = _json.loads(session_extra_data)
        except (ValueError, TypeError) as exc:
            logger.warning("Adapter: session_extra_data is not valid JSON (%s)", exc)
        else:
            if isinstance(parsed, dict):
                _session_extra = parsed
                logger.info(
                    "Adapter: parsed session_extra_data (agent=%s, keys=%s)",
                    agent_id, list(parsed.keys()),
                )
            else:
                logger.warning(
                    "Adapter: session_extra_data is not a dict (type=%s)",
                    type(parsed).__name__,
                )

    if _session_extra is None:
        logger.info(
            "Adapter: no session_state / session_extra_data for agent=%s",
            agent_id,
        )

    # ── Merge 登录 email(Cookie current_user)到 _session_extra_data ──
    # 不覆盖平台已注入的 real_user_id;direct chat / AgentOS 直连路径无平台
    # 注入时用 Cookie email 填充。
    _session_extra = _inject_login_email(_session_extra, current_user)

    # ── Run-level session_state payload ─────────────────────────────────
    # Single dict passed as arun(session_state=...): _session_extra_data.
    # Lands in run_context.session_state and persists to
    # agno_sessions.session_data.session_state.
    _run_state: dict = {}
    if _session_extra:
        _run_state["_session_extra_data"] = _session_extra

    # ── Resume detection (agent-os-run-resume) ──────────────────────────
    # Read the persisted run_status from the agno agent session_state. If the
    # previous run was interrupted by a client disconnect, forward the stored
    # original_message (not the incoming message) so agno reloads history and
    # the LLM continues from the breakpoint. A2 strategy: no [resume] sentinel,
    # no agent INSTRUCTIONS change — original message replay drives continuation.
    run_status = await read_run_status(session_id)
    # HITL pause guard: a session parked at a
    # paused run MUST go through the continue endpoint — a plain new message
    # would start a fresh turn and leave the paused run + pending approval
    # dangling forever. Checked BEFORE resume detection and BEFORE the
    # try_set_running lock (no lock side effects on rejection).
    if run_status == "paused":
        paused_run_id = await read_paused_run_id(session_id)
        detail = (
            f"Session is paused waiting for HITL resolution"
            + (f" (run {paused_run_id})" if paused_run_id else "")
            + f" — continue it via POST /agents/{agent_id}/runs/"
            + (paused_run_id or "<run_id>") + "/continue"
        )
        logger.info(
            "Adapter: paused session rejected new message (session=%s run=%s)",
            session_id, paused_run_id,
        )
        raise HTTPException(status_code=409, detail=detail)
    is_resume = run_status == "interrupted"
    if is_resume:
        forward_message = await read_original_message(session_id) or message
        logger.info(
            "Adapter: resume detected (session=%s) — forwarding original_message",
            session_id,
        )
    else:
        forward_message = message

    # 3. Acquire run lock ---------------------------------------------------
    acquired = await session_manager.try_set_running(session_id)
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail="Session is already running",
        )

    # Mark run lifecycle: "running" + capture original_message on fresh turns.
    # On resume we do NOT overwrite original_message (preserve prior turn's).
    await write_run_lifecycle(
        session_id,
        "running",
        original_message=forward_message if not is_resume else None,
    )

    # 4. Execute  -----------------------------------------------------------
    if stream:
        return StreamingResponse(
            _stream_sse(agent, forward_message, session_id, user_id, _run_state),
            media_type="text/event-stream",
        )
    else:
        return await _run_sync(agent, forward_message, session_id, user_id, _run_state)


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/runs/{run_id}/continue  — HITL continue
# ---------------------------------------------------------------------------

@agent_os_router.post("/agents/{agent_id}/runs/{run_id}/continue")
async def continue_agent_run(
    agent_id: str,
    run_id: str,
    tools: str = Form(
        "",
        description=(
            "JSON string of ToolExecution[] dicts carrying the HITL resolution "
            "(confirmed / confirmation_note / user_input_schema[].value / "
            "user_feedback_schema[].selected_options / answered / result) — "
            "same wire format agno 2.6.22 RemoteAgent.acontinue_run sends."
        ),
    ),
    input: Optional[str] = Form(
        None,
        description="Optional new user-message text appended before resuming (experimental).",
    ),
    continue_from: str = Form(
        "end",
        description="Continuation boundary: 'end', 'last_user', or a numeric message index.",
    ),
    session_id: str = Form(..., description="Session ID of the paused run (required)"),
    user_id: Optional[str] = Form(None, description="User identifier"),
    stream: bool = Form(True, description="Enable streaming responses via SSE"),
    current_user: str | None = Depends(get_current_user),
):
    """Continue a paused run with the platform's HITL resolution.

    Contract mirrors the vendored agno 2.6.22 agents router
    (``agno/os/routers/agents/router.py`` continue_agent_run) minus the
    admin-approval / fork / regenerate faces this deployment does not expose:

    - ``tools`` parsed into ``ToolExecution[]`` → ``agent.acontinue_run(
      run_id, updated_tools, continue_from, ...)`` (multi-gate re-pause
      supported: the resumed stream may itself end with a new RunPaused)
    - error mapping: RunNotFoundError→404, RunNotContinuableError→409
      (repeat-continue idempotency), ValueError/parse failure→400
    - session MUST already exist (404 otherwise — never auto-registered);
      restart recovery re-registers from storage with a fresh agent whose
      history loads from DB.
    """
    # D15: same guard as /runs (local import avoids the circular dependency).
    from api.server import require_llm_configured
    require_llm_configured()

    # 0. Strip the upstream platform's user-id prefix (same as /runs)
    user_id = _strip_user_id_prefix(user_id)

    # 1. Parse tools JSON — fail fast, before any lock side effect
    try:
        tools_data = _json.loads(tools) if tools else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON in tools field")
    if tools_data is not None and not isinstance(tools_data, list):
        raise HTTPException(
            status_code=400,
            detail="Invalid structure or content for tools: tools must be a JSON array",
        )

    # 2. Validate agent_id — 404 (same as /runs)
    definition: Optional[AgentDefinition] = agent_registry.get(agent_id)
    if definition is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found",
        )

    # 3. Session MUST exist — 404, MUST NOT auto-register a new session.
    #    Restart recovery: memory miss but present in storage → register the
    #    existing session (paused-run history loads from DB, not memory).
    record = session_manager.get(session_id)
    if record is None:
        if not await session_manager.exists_in_storage(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        record = await session_manager.register_existing(
            session_id=session_id,
            agent=None,
            meta={
                "agent_name": agent_id,
                "mode": "normal",
                "email": _DEFAULT_EMAIL,
            },
        )
        logger.info(
            "Adapter: re-registered external session %s for continue (restart recovery)",
            session_id,
        )

    # 4. In-memory concurrency guard (pre-lock, mirrors /runs step 2c)
    if record.status == SessionStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Session is already running")

    # 5. ToolExecution.from_dict — structure errors → 400 (router :1055-1063)
    updated_tools: Optional[List[ToolExecution]] = None
    if tools_data:
        try:
            updated_tools = [ToolExecution.from_dict(tool) for tool in tools_data]
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid structure or content for tools: {e}",
            )

    # 6. continue_from — 'end' | 'last_user' | numeric index (router :1067-1079)
    stripped_continue_from = continue_from.strip()
    if stripped_continue_from.lstrip("-").isdigit():
        continue_from_value: Union[int, str] = int(stripped_continue_from)
    elif stripped_continue_from == "end":
        continue_from_value = "end"
    elif stripped_continue_from == "last_user":
        continue_from_value = "last_user"
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid continue_from. Use 'end', 'last_user', or a numeric message index.",
        )

    # 7. Attach agent if missing (cold record / restart recovery)
    if record.agent is None:
        agent = await create_agent_from_definition(
            definition,
            session_id=session_id,
            user_id=user_id,
        )
        await session_manager.attach_agent(session_id, agent)
        logger.info("Adapter: attached agent '%s' to session %s for continue", agent_id, session_id)
    else:
        agent = record.agent

    # 7.5 Run existence pre-check → 404. The async acontinue_run path converts
    # RunNotFoundError into an error RunOutput (HTTP 200) instead of raising,
    # so the router-style except-mapping never fires there — pre-check keeps
    # the "run 不存在 → 404" contract honest (agent-os-hitl-continue spec).
    _db = getattr(agent, "db", None)
    if _db is not None:
        from agno.db.base import SessionType

        _get_session = _db.get_session
        if iscoroutinefunction(_get_session):
            _sess_doc = await _get_session(session_id=session_id, session_type=SessionType.AGENT)
        else:
            _sess_doc = _get_session(session_id=session_id, session_type=SessionType.AGENT)
        _run_ids = {getattr(_r, "run_id", None) for _r in (getattr(_sess_doc, "runs", None) or [])}
        if run_id not in _run_ids:
            raise HTTPException(status_code=404, detail=f"No runs found for run ID {run_id}")

    # 8. Acquire run lock (continue vs new-message / continue vs continue → 409).
    #    NOTE: no "running" run_status write here — the persisted "paused"
    #    marker stays authoritative mid-continue (crash → continue-retry
    #    resumes from last persisted state, per official semantics).
    acquired = await session_manager.try_set_running(session_id)
    if not acquired:
        raise HTTPException(status_code=409, detail="Session is already running")

    # 9. Execute
    if stream:
        return StreamingResponse(
            _stream_continue_sse(
                agent, run_id, session_id, user_id,
                updated_tools=updated_tools,
                input=input,
                continue_from=continue_from_value,
            ),
            media_type="text/event-stream",
        )

    # stream=false — sync continue, full RunOutput JSON + router error mapping
    try:
        run_output: RunOutput = await agent.acontinue_run(  # type: ignore[assignment]
            run_id=run_id,
            updated_tools=updated_tools,
            input=input,
            continue_from=continue_from_value,
            session_id=session_id,
            user_id=user_id,
            stream=False,
        )
        await session_manager.set_completed(session_id)
        try:
            if getattr(run_output, "status", None) == RunStatus.paused:
                await write_run_lifecycle(
                    session_id, "paused", paused_run_id=run_output.run_id,
                )
            else:
                await write_run_lifecycle(session_id, "idle")
        except Exception:
            logger.warning(
                "Adapter: write_run_lifecycle failed after sync continue (session=%s)",
                session_id, exc_info=True,
            )
        return run_output.to_dict()
    except RunNotFoundError as e:
        await _release_after_continue_error(session_id)
        raise HTTPException(status_code=404, detail=str(e))
    except RunNotContinuableError as e:
        await _release_after_continue_error(session_id)
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        await _release_after_continue_error(session_id)
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _release_after_continue_error(session_id: str) -> None:
    """Release the in-memory run lock after a mapped sync-continue error.

    HTTP-level rejections (404/409/400) happen after ``try_set_running``;
    without this the session would stay RUNNING in memory forever. The
    persisted run_status is intentionally left untouched (a 409 run is
    usually already advanced — its own terminal marker governs).
    """
    try:
        record = session_manager.get(session_id)
        if record and record.status == SessionStatus.RUNNING:
            await session_manager.set_completed(session_id)
    except Exception:
        logger.warning(
            "Adapter: release_after_continue_error failed (session=%s)",
            session_id, exc_info=True,
        )


async def _has_pending_approval(run_id: str) -> bool:
    """Whether this run still has a pending approval record (D3 分流判定面).

    Queries ``agno_approvals`` via the storage factory (``get_approvals``),
    the same surface the approvals routes use. Used to branch a
    mid-continue client disconnect: pending → keep the session "paused"
    (platform re-sends the same tools payload and acontinue_run resumes from
    the last persisted state); none → interrupted (original-message replay
    domain).

    On query failure this conservatively returns True (fail-safe to paused):
    a continued run started out paused, and re-continue is the officially
    supported recovery for an unknown intermediate state.
    """
    try:
        from core.storage import get_storage

        db = get_storage()
        fn = getattr(db, "get_approvals", None)
        if fn is None:
            # DB backend without approvals support — no gate can be pending.
            return False
        if iscoroutinefunction(fn):
            approvals, _ = await fn(status="pending", run_id=run_id, limit=1)
        else:
            approvals, _ = fn(status="pending", run_id=run_id, limit=1)
        return bool(approvals)
    except NotImplementedError:
        # ignore: backend without approvals support — same as fn missing
        return False
    except Exception:
        logger.warning(
            "Adapter: pending-approval query failed (run=%s) — assuming paused",
            run_id, exc_info=True,
        )
        return True


async def _stream_continue_sse(
    agent: Any,
    run_id: str,
    session_id: str,
    user_id: Optional[str],
    updated_tools: Optional[List[ToolExecution]] = None,
    input: Optional[str] = None,
    continue_from: Union[int, str] = "end",
) -> AsyncGenerator[str, None]:
    """Stream the continued run as agno-compatible SSE events.

    Mirrors ``_stream_sse`` semantics on the continue channel:

    - multi-gate re-pause: a new ``RunPausedEvent`` gets the same D2
      前置落态 (lock release + ``run_status="paused"`` before the frame)
    - client disconnect branches on pending-approval existence (D3):
      pending → ``paused`` (re-continue recovers); none → partial-run
      persist + ``interrupted`` (replay domain)
    - normal completion → ``idle`` + in-memory completed
    - ``RunOutput`` / ``TeamRunOutput`` objects mixed in by the continue
      trace patch are filtered out (they are not SSE events)
    """
    agent_name: str = getattr(agent, "name", "") or ""
    collector = AbortMessagesCollector()
    active_tools: dict = {}
    _was_interrupted = False
    _saw_pause = False
    _paused_run_id: Optional[str] = None

    try:
        agen = agent.acontinue_run(
            run_id=run_id,
            updated_tools=updated_tools,
            input=input,
            continue_from=continue_from,
            session_id=session_id,
            user_id=user_id,
            stream=True,
            stream_events=True,
        )
        async for chunk in agen:
            if isinstance(chunk, (RunOutput, TeamRunOutput)):
                # agno-continue-trace-patch may inject run-output objects
                # (yield_run_output semantics) — not SSE events, filter.
                continue
            feed_collector_from_event(collector, chunk, session_id, agent_name, active_tools)
            if isinstance(chunk, RunPausedEvent):
                _saw_pause = True
                _paused_run_id = chunk.run_id
                rec = session_manager.get(session_id)
                if rec and rec.status == SessionStatus.RUNNING:
                    await session_manager.set_completed(session_id)
                await write_run_lifecycle(
                    session_id, "paused", paused_run_id=chunk.run_id,
                )
            yield format_sse_event(chunk)

    except asyncio.CancelledError:
        # Client disconnected mid-continue — branch in `finally` (shielded).
        _was_interrupted = True
    except (RunNotFoundError, RunNotContinuableError) as e:
        logger.warning(
            "Adapter continue SSE rejected (session=%s run=%s): %s",
            session_id, run_id, e,
        )
        yield format_sse_event(RunErrorEvent(content=str(e)))
    except Exception as e:
        logger.error(
            "Adapter continue SSE error (session=%s run=%s): %s",
            session_id, run_id, e, exc_info=True,
        )
        yield format_sse_event(RunErrorEvent(content=str(e)))
    finally:
        record = session_manager.get(session_id)
        if _saw_pause:
            # D2/D3：再暂停幂等落 paused（前置落态已写），不 persist、不改状态。
            logger.info(
                "Adapter: continued run paused again (session=%s run=%s) — awaiting next HITL continue",
                session_id, _paused_run_id,
            )
        elif _was_interrupted:
            try:
                has_pending = await _has_pending_approval(run_id)
            except Exception:
                logger.warning(
                    "Adapter: pending check failed in disconnect branch (session=%s run=%s)",
                    session_id, run_id, exc_info=True,
                )
                has_pending = True
            try:
                if has_pending:
                    # 审批仍挂起 → paused：平台重发同 tools 载荷即恢复
                    await asyncio.shield(write_run_lifecycle(
                        session_id, "paused", paused_run_id=run_id,
                    ))
                else:
                    # 门已过、LLM 半路断 → 断线重放域
                    await asyncio.shield(persist_partial_run(
                        session_id, collector.to_messages(), run_id,
                        agent_name=agent_name or None,
                    ))
                    await asyncio.shield(write_run_lifecycle(session_id, "interrupted"))
            except Exception:
                logger.warning(
                    "Adapter: continue-disconnect persistence failed (session=%s run=%s)",
                    session_id, run_id, exc_info=True,
                )
            if record and record.status == SessionStatus.RUNNING:
                await session_manager.set_completed(session_id)
        else:
            # Normal completion (or error) — back to idle
            try:
                await write_run_lifecycle(session_id, "idle")
            except Exception:
                logger.warning(
                    "Adapter: write_run_lifecycle(idle) failed after continue (session=%s)",
                    session_id, exc_info=True,
                )
            if record and record.status == SessionStatus.RUNNING:
                await session_manager.set_completed(session_id)




async def _stream_sse(
    agent: Any,
    message: str,
    session_id: str,
    user_id: Optional[str],
    run_state: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    """Stream agent response as agno-compatible SSE events.

    On client disconnect (``CancelledError``) the partial conversation is
    persisted: ``AbortMessagesCollector.to_messages()`` (with synthetic
    ``[interrupted]`` tool results for in-flight tool calls) is ``$push``-ed
    onto ``agno_sessions.runs[]`` via ``persist_partial_run``, and
    ``run_status="interrupted"`` is written so the next request resumes.

    HITL pause: when a
    ``RunPausedEvent`` is detected, the session lock is released and
    ``run_status="paused"`` (+ ``paused_run_id``) persisted BEFORE the frame
    is yielded — 帧即屏障: the platform may POST continue the moment it
    receives the frame, so the frame must imply "state is settled". If the
    client disconnects mid-frame anyway, ``_saw_pause`` wins over the
    interrupted semantics (agno already stored the paused run + pending
    approval in DB before yielding — replaying the original message would
    be wrong).
    """
    agent_name: str = getattr(agent, "name", "") or ""
    collector = AbortMessagesCollector()
    collector.add_user_message(message)
    active_tools: dict = {}
    _was_interrupted = False
    _saw_pause = False
    _paused_run_id: Optional[str] = None

    try:
        arun_kwargs: dict = {
            "input": message,
            "session_id": session_id,
            "user_id": user_id,
            "stream": True,
            "stream_events": True,
        }
        if run_state:
            arun_kwargs["session_state"] = run_state
        run_response = agent.arun(**arun_kwargs)
        async for chunk in run_response:
            feed_collector_from_event(collector, chunk, session_id, agent_name, active_tools)
            if isinstance(chunk, RunPausedEvent):
                # D2 前置落态：先释放在内存锁，再落暂停标记，最后才发帧。
                _saw_pause = True
                _paused_run_id = chunk.run_id
                rec = session_manager.get(session_id)
                if rec and rec.status == SessionStatus.RUNNING:
                    await session_manager.set_completed(session_id)
                await write_run_lifecycle(
                    session_id, "paused", paused_run_id=chunk.run_id,
                )
            yield format_sse_event(chunk)

    except asyncio.CancelledError:
        # Client disconnected — persist the breakpoint in `finally` (shielded).
        # NOTE: if a RunPaused frame was already settled (_saw_pause), the
        # paused semantics below wins — interrupted replay would corrupt it.
        _was_interrupted = True
    except Exception as e:
        logger.error(
            "Adapter SSE error (session=%s): %s", session_id, e, exc_info=True,
        )
        error_event = RunErrorEvent(content=str(e))
        yield format_sse_event(error_event)
    finally:
        record = session_manager.get(session_id)
        if _saw_pause:
            # D2/D3：pause 语义胜出。agno 已在暂停处理器里落库 paused run +
            # pending approval，这里 MUST NOT persist_partial、MUST NOT 改写
            # run_status（幂等跳过）。in-memory 锁已在前置落态释放 —— 此处
            # 不再 set_completed：流可能长时间挂着等用户裁决，期间 continue
            # 已重新持锁，按当次 status 快照再释放会误伤并发续跑。
            logger.info(
                "Adapter: run paused (session=%s run=%s) — awaiting HITL continue",
                session_id, _paused_run_id,
            )
        elif _was_interrupted:
            # Persist partial run + mark interrupted so the next request resumes.
            # persist_partial_run runs first — it guarantees the agno_sessions
            # document exists (insert branch) so the subsequent run_status
            # write lands on a real document. asyncio.shield protects the DB
            # writes from the propagating CancelledError.
            try:
                run_id = collector.run_id or str(uuid4())
                await asyncio.shield(persist_partial_run(
                    session_id, collector.to_messages(), run_id,
                    agent_name=agent_name or None,
                ))
                await asyncio.shield(write_run_lifecycle(session_id, "interrupted"))
            except Exception:
                logger.warning(
                    "Adapter: interrupt persistence failed (session=%s)",
                    session_id, exc_info=True,
                )
            # In-memory status → completed so a resume request's try_set_running
            # succeeds. The "interrupted" semantic lives in the persisted
            # run_status, not in the in-memory SessionStatus.
            if record and record.status == SessionStatus.RUNNING:
                await session_manager.set_completed(session_id)
        else:
            # Normal completion (or error) — run is not a resumable breakpoint.
            try:
                await write_run_lifecycle(session_id, "idle")
            except Exception:
                logger.warning(
                    "Adapter: write_run_lifecycle(idle) failed (session=%s)",
                    session_id, exc_info=True,
                )
            if record and record.status == SessionStatus.RUNNING:
                await session_manager.set_completed(session_id)


async def _run_sync(
    agent: Any,
    message: str,
    session_id: str,
    user_id: Optional[str],
    run_state: Optional[dict] = None,
) -> JSONResponse:
    """Run agent synchronously and return the full RunOutput JSON.

    Returns ``run_output.to_dict()`` (field superset of the old
    ``{content, session_id, run_id}`` triple): ``status`` / ``requirements``
    MUST reach the platform so a non-streaming RemoteAgent can detect a HITL
    pause (``RunOutput.from_dict`` fills ``status=None`` without them and
    paused detection breaks). See agent-os-adapter spec — "Non-streaming run".
    """
    try:
        # Use awaitable overload — arun returns RunOutput when stream=False
        arun_kwargs: dict = {
            "input": message,
            "session_id": session_id,
            "user_id": user_id,
            "stream": False,
        }
        if run_state:
            arun_kwargs["session_state"] = run_state
        run_output: RunOutput = await agent.arun(**arun_kwargs)  # type: ignore[assignment]
        await session_manager.set_completed(session_id)
        try:
            if getattr(run_output, "status", None) == RunStatus.paused:
                # 同步路径的暂停：落 paused 标记（新消息 409 引导 continue），
                # paused_run_id 供 409 detail 定位。
                await write_run_lifecycle(
                    session_id, "paused", paused_run_id=run_output.run_id,
                )
            else:
                await write_run_lifecycle(session_id, "idle")
        except Exception:
            logger.warning(
                "Adapter: write_run_lifecycle failed after sync run (session=%s)",
                session_id, exc_info=True,
            )
        return JSONResponse(content=run_output.to_dict())
    except Exception as e:
        logger.error(
            "Adapter sync error (session=%s): %s", session_id, e, exc_info=True,
        )
        await session_manager.set_failed(session_id, str(e))
        try:
            await write_run_lifecycle(session_id, "idle")
        except Exception:
            logger.warning(
                "Adapter: write_run_lifecycle(idle) failed after sync error (session=%s)",
                session_id, exc_info=True,
            )
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /traces  — list traces (tracing query API)
# ---------------------------------------------------------------------------


@agent_os_router.get("/traces")
async def list_traces(
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    agent_type: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 20,
    page: int = 1,
    current_user: str | None = Depends(get_current_user),
):
    """Return paginated trace records from the tracing database.

    Every authenticated user is force-scoped to their own traces
    (user_id overridden to current_user) regardless of the request param.
    """
    from datetime import datetime

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    # force-scope to own traces, ignore any user_id from request
    user_id = current_user

    # Validate status parameter
    if status is not None:
        valid_statuses = ("OK", "ERROR", "UNSET")
        if status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}",
            )

    # Validate agent_type parameter
    if agent_type is not None:
        valid_agent_types = ("agent", "workflow")
        if agent_type not in valid_agent_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid agent_type '{agent_type}'. Must be one of: {', '.join(valid_agent_types)}",
            )

    # Parse time parameters
    parsed_start: Optional[datetime] = None
    parsed_end: Optional[datetime] = None
    if start_time is not None:
        try:
            parsed_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail=f"Invalid start_time format: '{start_time}'")
    if end_time is not None:
        try:
            parsed_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail=f"Invalid end_time format: '{end_time}'")

    try:
        # Query MongoDB directly to preserve custom fields (agent_type, token_*)
        # that agno's Trace.from_dict() would strip.
        from pymongo import MongoClient
        from core.config import settings as app_settings
        raw = MongoClient(app_settings.mongo_uri)
        col = raw["qa_agent_db"]["agno_traces"]

        query: dict = {}
        if agent_id:
            query["agent_id"] = agent_id
        if session_id:
            query["session_id"] = session_id
        if user_id:
            query["user_id"] = user_id
        if workflow_id:
            query["workflow_id"] = workflow_id
        if agent_type is not None:
            query["agent_type"] = agent_type
        if status:
            query["status"] = status
        if parsed_start:
            query["start_time"] = {"$gte": parsed_start.isoformat()}
        if parsed_end:
            query["end_time"] = {"$lte": parsed_end.isoformat()}

        total = col.count_documents(query)
        skip = ((page or 1) - 1) * (limit or 20)
        cursor = col.find(query).sort("start_time", -1).skip(skip).limit(limit or 20)
        traces = []
        for r in cursor:
            r.pop("_id", None)
            traces.append(r)

        return {
            "traces": traces,
            "total": total,
            "page": page,
            "limit": limit,
        }
    except Exception as e:
        logger.debug("Trace query failed (tracing may not be initialized): %s", e)
        return {"traces": [], "total": 0, "page": page, "limit": limit}


# ---------------------------------------------------------------------------
# GET /traces/{trace_id}/spans  — spans for a trace
# ---------------------------------------------------------------------------


@agent_os_router.get("/traces/{trace_id}/spans")
async def get_trace_spans(trace_id: str):
    """Return all spans belonging to a specific trace.

    No role gate on this endpoint — known trade-off ("spans 端点不加 role gate"):
    relies on normal users being unable to
    enumerate others' trace_id. If a trace_id leaks via logs/URL, normal
    users could still fetch its spans. Not mitigated in this change.
    """
    try:
        from core.storage import get_storage
        db = get_storage()
        spans = db.get_spans(trace_id=trace_id)
        return {
            "spans": [s.to_dict() if hasattr(s, "to_dict") else s for s in spans],
            "trace_id": trace_id,
        }
    except Exception as e:
        logger.debug("Span query failed (tracing may not be initialized): %s", e)
        return {"spans": [], "trace_id": trace_id}


# ---------------------------------------------------------------------------
# GET /traces/daily-tokens  — per-day token aggregation (last N days)
# ---------------------------------------------------------------------------
# Fixes "token 趋势图分页漏柱": the UI chart previously consumed the paginated /traces
# list (limit=20) and missed any day whose traces fell past page 1. This
# endpoint returns pre-aggregated per-day totals so the chart is unaffected
# by list pagination.


@agent_os_router.get("/traces/daily-tokens")
async def list_daily_tokens(
    days: int = 7,
    user_id: Optional[str] = None,
    agent_type: Optional[str] = None,
    status: Optional[str] = None,
    current_user: str | None = Depends(get_current_user),
):
    """Return per-day token totals over the last `days` days.

    Scoping mirrors list_traces: every authenticated user is force-scoped to
    their own traces regardless of the request param. Returns one entry per
    day in the range, filling 0 for days with no traces, so the UI never
    needs to infer missing days.
    """
    from datetime import datetime, timedelta, timezone

    from core.config import settings as app_settings
    from pymongo import MongoClient

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    # force-scope to own traces, ignore any user_id from request
    effective_user_id: Optional[str] = current_user

    if status is not None:
        valid_statuses = ("OK", "ERROR", "UNSET")
        if status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}",
            )

    if agent_type is not None:
        valid_agent_types = ("agent", "workflow")
        if agent_type not in valid_agent_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid agent_type '{agent_type}'. Must be one of: {', '.join(valid_agent_types)}",
            )

    days = max(1, min(days, 90))
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        client = MongoClient(app_settings.mongo_uri)
        col = client["qa_agent_db"]["agno_traces"]

        match_stage: dict = {
            "start_time": {"$gte": start.isoformat()},
        }
        if effective_user_id:
            match_stage["user_id"] = effective_user_id
        if agent_type:
            match_stage["agent_type"] = agent_type
        if status:
            match_stage["status"] = status

        pipeline = [
            {"$match": match_stage},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "date": {"$dateFromString": {"dateString": "$start_time"}},
                            "format": "%Y-%m-%d",
                        }
                    },
                    "token_total": {"$sum": {"$ifNull": ["$token_total", 0]}},
                    # 缓存命中的 prompt 部分单列
                    # 汇总；历史 trace 无字段按 0。MUST NOT 改变 token_total 口径。
                    "token_cache_read": {"$sum": {"$ifNull": ["$token_cache_read", 0]}},
                    "trace_count": {"$sum": 1},
                }
            },
        ]
        agg = list(col.aggregate(pipeline))
        by_day: dict[str, dict] = {}
        for r in agg:
            day_key = r.get("_id")
            if not day_key:
                continue
            by_day[day_key] = {
                "day": day_key,
                "token_total": int(r.get("token_total") or 0),
                "token_cache_read": int(r.get("token_cache_read") or 0),
                "trace_count": int(r.get("trace_count") or 0),
            }

        days_list = []
        for i in range(days):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            days_list.append(by_day.get(d, {"day": d, "token_total": 0, "token_cache_read": 0, "trace_count": 0}))

        return {"days": days_list, "days_count": days}
    except Exception as e:
        logger.debug("Daily token aggregation failed (tracing may not be initialized): %s", e)
        return {"days": [], "days_count": days}
