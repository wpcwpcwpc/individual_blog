"""QA Agent System — Approvals Routes

Agent 无关的 HITL 审批与澄清路由（任何挂阻塞审批工具的 Agent 共用）：

  GET  /approvals                             — list（pending + 历史，审计）
  GET  /approvals/count                       — pending 计数（前端徽标）
  GET  /approvals/{approval_id}               — 单条详情
  POST /approvals/{approval_id}/resolve       — 批准/拒绝（+意见）
  POST /sessions/{sid}/runs/{run_id}/continue — resolve 后续跑（SSE）
  GET  /sessions/{sid}/pending-clarification  — 会话待答澄清（刷新自愈源）

审批数据存 agno approvals 表（Mongo: agno_approvals 集合 / Sqlite: agno_approvals），
由 agno 运行时在 run 暂停时自动落库。

路由不带 path prefix：审批面是会话级公共能力，URL 里 MUST NOT 绑定具体 Agent 名。
"""

from __future__ import annotations

import asyncio
import logging
from inspect import iscoroutinefunction
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agno.os.utils import format_sse_event
from agno.run.agent import RunCancelledEvent, RunErrorEvent

from api._shared import DEFAULT_EMAIL as _DEFAULT_EMAIL
from api.schemas import SessionStatus
from api.session_manager import session_manager
from auth.dependencies import get_current_user
from core.stream_adapter import serialize_clarification_payload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["approvals"])


# ── Section: db helpers ─────────────────────────────────────────────


async def _db_call(method: str, *args: Any, **kwargs: Any) -> Any:
    """统一同步/异步 db 方法调用（与 agno approvals router 同款形态）。"""
    from core.storage import get_storage

    db = get_storage()
    fn = getattr(db, method, None)
    if fn is None:
        raise HTTPException(status_code=503, detail="Approvals not supported by the configured database")
    try:
        if iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return fn(*args, **kwargs)
    except NotImplementedError:
        raise HTTPException(status_code=503, detail="Approvals not supported by the configured database")


# ── Section: schemas ────────────────────────────────────────────────


class ApprovalResolveRequest(BaseModel):
    """审批决议请求。"""

    status: str = Field(description="approved | rejected")
    resolved_by: str = Field(default="", description="决议人（默认取登录用户）")
    note: str = Field(default="", description="拒绝意见 / 批准备注（进 resolution_data，agent 回退依据）")


class ClarificationAnswer(BaseModel):
    """单条澄清作答。"""

    tool_call_id: str = Field(description="对应 clarification_request 的 tool_call_id")
    values: Optional[Dict[str, Any]] = Field(
        default=None, description="form（get_user_input）答案：字段名 → 值（全字段必填）",
    )
    selections: Optional[Dict[str, List[str]]] = Field(
        default=None, description="feedback（ask_user）答案：question 原文 → 选中 label 列表",
    )


class ContinueRunRequest(BaseModel):
    """续跑请求体（可选；无 body 兼容既有审批链路）。"""

    clarifications: Optional[List[ClarificationAnswer]] = None


class ApprovalResponse(BaseModel):
    """审批记录（对齐 agno approvals 表结构）。"""

    id: str
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    status: str
    approval_type: Optional[str] = None
    pause_type: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[int] = None
    resolution_data: Optional[Dict[str, Any]] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


# ── Section: approvals list/get/resolve ─────────────────────────────


@router.get("/approvals", response_model=List[Dict[str, Any]])
async def list_approvals(
    status: Optional[str] = Query(None, description="pending | approved | rejected"),
    session_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    page: int = Query(1, ge=1),
    _: str | None = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """审批列表（pending 待办 + 已决议历史，审计轨迹）。

    过滤参数对齐 agno get_approvals；session_id/run_id 便于前端按会话聚合。
    """
    approvals, total = await _db_call(
        "get_approvals",
        status=status,
        run_id=run_id,
        limit=limit,
        page=page,
    )
    if session_id:
        approvals = [a for a in approvals if a.get("session_id") == session_id]
    logger.info(
        "[approvals] list_approvals: status=%s session=%s → %d/%d",
        status, session_id, len(approvals), total,
    )
    return approvals


@router.get("/approvals/count")
async def get_pending_count(
    _: str | None = Depends(get_current_user),
) -> Dict[str, int]:
    """pending 审批计数（前端徽标轮询）。"""
    count = await _db_call("get_pending_approval_count")
    return {"count": count}


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: str,
    _: str | None = Depends(get_current_user),
) -> Dict[str, Any]:
    """单条审批详情（含 tool_args 载荷：kind/title/content_md 等）。"""
    approval = await _db_call("get_approval", approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval


@router.post("/approvals/{approval_id}/resolve")
async def resolve_approval(
    approval_id: str,
    body: ApprovalResolveRequest,
    current_user: str | None = Depends(get_current_user),
) -> Dict[str, Any]:
    """审批决议（批准/拒绝 + 意见）。

    与 agno approvals router 同款 db 语义：``expected_status="pending"`` 乐观锁，
    重复 resolve → 409。resolve 后前端调 continue 端点续跑（run 不自动重启）。
    意见写入 ``resolution_data.note``；agno 原生 reject 文本不含意见，
    qa-agent continue 层从 resolution_data 取意见注入（S3/Open Questions）。
    """
    if body.status not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="status must be approved | rejected")

    import time

    resolved_by = body.resolved_by or current_user or _DEFAULT_EMAIL
    update_kwargs: Dict[str, Any] = {
        "status": body.status,
        "resolved_by": resolved_by,
        "resolved_at": int(time.time()),
        "resolution_data": {"note": body.note} if body.note else None,
    }
    update_kwargs = {k: v for k, v in update_kwargs.items() if v is not None}
    if body.note:
        update_kwargs["resolution_data"] = {"note": body.note}

    result = await _db_call(
        "update_approval", approval_id, expected_status="pending", **update_kwargs,
    )
    if result is None:
        existing = await _db_call("get_approval", approval_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        raise HTTPException(
            status_code=409,
            detail=f"Approval is already '{existing.get('status')}' and cannot be resolved",
        )
    logger.info(
        "[approvals] approval %s resolved: %s by %s (note=%r)",
        approval_id, body.status, resolved_by, body.note[:60] if body.note else "",
    )
    return result


# ── Section: run continue（resolve 后续跑，SSE） ────────────────────


async def _load_run(agent: Any, session_id: str, run_id: str) -> Any:
    """从 DB 取回目标 run（RunOutput，含 requirements/tools）；取不到返回 None。"""
    db = getattr(agent, "db", None)
    get_session = getattr(db, "get_session", None)
    if get_session is None:
        return None
    session = (
        await get_session(session_id=session_id)
        if iscoroutinefunction(get_session)
        else get_session(session_id=session_id)
    )
    return next(
        (r for r in getattr(session, "runs", None) or [] if getattr(r, "run_id", None) == run_id),
        None,
    )


def _active_clarification_requirements(run: Any) -> List[Any]:
    """run 上活跃的澄清 requirement（user_input / user_feedback，未 resolved）。"""
    active: List[Any] = []
    for r in getattr(run, "requirements", None) or []:
        has_schema = bool(
            getattr(r, "user_input_schema", None) or getattr(r, "user_feedback_schema", None)
        )
        try:
            unresolved = not r.is_resolved()
        except Exception:  # noqa: BLE001 — 防御性：单条坏 requirement 不拖垮重建
            unresolved = False
        if has_schema and unresolved:
            active.append(r)
    # 老数据兜底：requirements 缺失时从 tools 重建（requires_user_input 且未 answered）
    if not active:
        from agno.run.requirement import RunRequirement

        for t in getattr(run, "tools", None) or []:
            flagged = bool(
                getattr(t, "user_input_schema", None) or getattr(t, "user_feedback_schema", None)
            )
            if (
                flagged
                and getattr(t, "requires_user_input", None)
                and getattr(t, "answered", None) is None
            ):
                active.append(RunRequirement(tool_execution=t))
    return active


def _resolve_clarification_requirements(
    run: Any,
    clarifications: Optional[List[ClarificationAnswer]],
) -> Tuple[List[Any], Optional[Tuple[int, str]]]:
    """澄清作答匹配 + 注入 + 防静默护栏。

    返回 (requirements, error)。error 非 None → (status_code, detail) 直接抛出：
    - 409：run 有活跃澄清 requirement 但请求未覆盖（无参续跑会静默把 null 注给
      LLM —— spike_hitl_clarify Scenario G 实测），必须预防式拦截；
    - 422：作答无法应用（tool_call_id 未命中 / 字段漏填 / requirement 不存在）。
    用户作答 MUST NOT 静默丢弃（区别于审批路径的 fail-open）。
    """
    active = _active_clarification_requirements(run) if run is not None else []

    if not active:
        if clarifications:
            return [], (
                422,
                "No active clarification requirement on this run (already answered "
                "or not a clarification pause)",
            )
        return [], None

    if not clarifications:
        pending_ids = [getattr(r.tool_execution, "tool_call_id", None) for r in active]
        return [], (
            409,
            f"Run has {len(active)} unanswered clarification(s) {pending_ids} — submit "
            "answers via body.clarifications (a no-arg continue would feed null values "
            "to the LLM)",
        )

    by_id = {getattr(r.tool_execution, "tool_call_id", None): r for r in active}
    used: set[int] = set()
    errors: List[str] = []
    for ans in clarifications:
        req = by_id.get(ans.tool_call_id)
        if req is None or id(req) in used:
            errors.append(
                f"clarification {ans.tool_call_id}: no active requirement (unknown or duplicate)"
            )
            continue
        used.add(id(req))
        try:
            if getattr(req, "user_feedback_schema", None):
                req.provide_user_feedback(ans.selections or {})
            else:
                req.provide_user_input(ans.values or {})
        except ValueError as exc:
            errors.append(f"clarification {ans.tool_call_id}: {exc}")
            continue
        if not req.is_resolved():
            errors.append(
                f"clarification {ans.tool_call_id}: incomplete answer — "
                "all fields/questions must be filled"
            )
    if errors:
        return [], (422, "; ".join(errors))
    remaining = [
        getattr(r.tool_execution, "tool_call_id", None) for r in active if id(r) not in used
    ]
    if remaining:
        return [], (
            409,
            f"Run still has unanswered clarification(s) {remaining} — all active "
            "requirements must be answered before continuing",
        )
    return [r for r in active if id(r) in used], None


async def _build_continue_requirements(
    agent: Any,
    session_id: str,
    run_id: str,
    run: Any = None,
) -> List[Any] | None:
    """α 路径：从 DB 重建已决议的 ``RunRequirement``（官方 API，fail-open）。

    对齐 agno Slack HITL 参考实现：resolved approval + 暂停工具 →
    ``RunRequirement.confirm()/reject(wrapped_note)`` → ``acontinue_run(requirements=...)``。
    拒绝意见经 ``wrap_reject_note`` 包装（Claude Code tool_result 范式：原文
    verbatim + 归因 + 行为指引）直达 ``confirmation_note`` → ``reject_tool_call``
    → ``function_call.error`` → LLM。

    返回 ``None`` = 重建不适用（无审批记录 / 无暂停工具 / 任何异常）→
    调用方回退无参续跑（β reject-note patch 在 fallback 路径兜底注入）。

    """
    try:
        approvals, _ = await _db_call(
            "get_approvals", run_id=run_id, approval_type="required", limit=20,
        )
        if not approvals:
            return None
        pending = [a for a in approvals if a.get("status") == "pending"]
        if pending:
            return None  # 端点门已挡 pending，防御性兜底

        run = run if run is not None else await _load_run(agent, session_id, run_id)
        if run is None:
            return None
        tools = getattr(run, "tools", None) or []

        # 对位顺序（多门同 run 场景 created_at 秒级平局不可靠）：
        # 1. stamp 对位：未决议工具（confirmed/result 均空）的 approval_id →
        #    精确指向本次审批门的记录；
        # 2. 启发式：暂停且未决议的确认型工具 → 该工具自身无 stamp 时，
        #    approval 记录取时间序最后一条（列表序兜底平局）。
        unresolved = [
            t for t in tools
            if getattr(t, "requires_confirmation", None)
            and getattr(t, "confirmed", None) is None
            and getattr(t, "result", None) is None
        ]
        stamped_ids = {getattr(t, "approval_id", None) for t in unresolved}
        approval = next(
            (a for a in reversed(approvals) if a.get("id") in stamped_ids),
            None,
        )
        if approval is not None:
            gate_tools = [
                t for t in unresolved
                if getattr(t, "approval_id", None) == approval.get("id")
            ]
        else:
            approval = next(reversed(approvals), None)
            gate_tools = unresolved
        if approval is None or not gate_tools:
            return None

        from agno.run.requirement import RunRequirement

        from core.reject_note import wrap_reject_note

        requirements: List[Any] = []
        for t in gate_tools:
            req = RunRequirement(tool_execution=t)
            if approval.get("status") == "rejected":
                req.reject(
                    wrap_reject_note((approval.get("resolution_data") or {}).get("note"))
                )
            else:
                req.confirm()
            requirements.append(req)
        logger.info(
            "[approvals] continue via requirements path (approval=%s status=%s tools=%d)",
            approval.get("id"), approval.get("status"), len(requirements),
        )
        return requirements
    except Exception:
        logger.warning(
            "[approvals] build continue requirements failed — fallback to "
            "no-arg continue (β reject-note patch still applies)",
            exc_info=True,
        )
        return None


@router.post("/sessions/{session_id}/runs/{run_id}/continue")
async def continue_agent_run(
    session_id: str,
    run_id: str,
    body: ContinueRunRequest | None = None,
    current_user: str | None = Depends(get_current_user),
):
    """续跑暂停中的 run（审批 resolve / 澄清作答后由前端调用）。

    审批仍 pending → 403（对齐 agno os router ``require_approval_resolved``）。
    澄清门：body.clarifications 携带作答（form=values / feedback=selections），
    有活跃澄清 requirement 而未携带 → 409 防静默护栏（无参续跑会把 null 注给 LLM，
    spike Scenario G 实测）。其余语义与 agent_os_adapter 的 runs 端点一致：
    并发守卫 + SSE 事件透传。续跑优先走官方 requirements 路径（拒绝意见直达 LLM，
    α）；重建失败回退无参调用（β patch 兜底注入）。
    """
    record = session_manager.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    agent = record.agent
    if agent is None:
        raise HTTPException(status_code=409, detail="Session has no attached agent")

    # 审批门：仍 pending → 403
    approvals, _ = await _db_call(
        "get_approvals", run_id=run_id, approval_type="required", limit=1,
    )
    if approvals and approvals[0].get("status") == "pending":
        raise HTTPException(
            status_code=403,
            detail="Run has a pending approval — resolve it before continuing",
        )

    # 澄清门：作答匹配 + 防静默护栏（必须在取并发锁前 fail-fast）
    run = await _load_run(agent, session_id, run_id)
    clar_reqs, clar_err = _resolve_clarification_requirements(
        run, body.clarifications if body is not None else None,
    )
    if clar_err is not None:
        status_code, detail = clar_err
        logger.info(
            "[approvals] continue guard rejected (session=%s run=%s): %s",
            session_id, run_id, detail,
        )
        raise HTTPException(status_code=status_code, detail=detail)

    acquired = await session_manager.try_set_running(session_id)
    if not acquired:
        raise HTTPException(status_code=409, detail="Session is already running")

    user_id = current_user or _DEFAULT_EMAIL

    async def _watch_abort(abort_evt: asyncio.Event, target: asyncio.Task) -> None:
        await abort_evt.wait()
        if not target.done():
            target.cancel()

    async def _stream() -> Any:
        # 任务级 abort watcher（对齐 /messages 路径）。
        # 此前续跑仅 tool 级 abort_pre_hook 生效，LLM 长生成期间中断无响应。
        abort_event = session_manager.get_abort_event(session_id)
        abort_watcher = asyncio.create_task(
            _watch_abort(abort_event, asyncio.current_task()),
            name=f"continue_abort_watcher_{session_id[:8]}",
        )
        was_aborted = False
        was_failed = False
        # α：官方 requirements 路径 —— 审批决议（fail-open 重建）+ 澄清作答
        # （端点层已护栏，此处合并）；None → 无参回退（agno fallback + β
        # reject-note patch 兜底注入）
        approval_reqs = await _build_continue_requirements(agent, session_id, run_id, run=run)
        requirements = [*(approval_reqs or []), *(clar_reqs or [])] or None
        logger.info(
            "[approvals] continue requirements: approval=%d clarification=%d (session=%s run=%s)",
            len(approval_reqs or []), len(clar_reqs or []), session_id, run_id,
        )
        try:
            agen = (
                agent.acontinue_run(
                    run_id=run_id,
                    session_id=session_id,
                    stream=True,
                    stream_events=True,
                    user_id=user_id,
                    requirements=requirements,
                )
                if requirements is not None
                else agent.acontinue_run(
                    run_id=run_id,
                    session_id=session_id,
                    stream=True,
                    stream_events=True,
                    user_id=user_id,
                )
            )
            from agno.run.agent import RunOutput as _RunOutput
            from agno.run.team import TeamRunOutput as _TeamRunOutput

            async for chunk in agen:
                # agno-continue-trace-patch 注入 run output 对象
                # （yield_run_output=True），非 SSE 事件 — 过滤
                if isinstance(chunk, (_RunOutput, _TeamRunOutput)):
                    continue
                formatted = format_sse_event(chunk)
                # 续跑终态（完成/再暂停）失效
                # context-usage 缓存 —— continue 通道不经 server.py /messages
                # 路径的失效点，审批轮次间不失效则徽章一直读到旧值。
                ev_name = formatted.get("event") if isinstance(formatted, dict) else None
                if ev_name in ("RunCompleted", "RunPaused"):
                    from core.context_usage import invalidate as _invalidate_context_usage

                    _invalidate_context_usage(session_id)
                yield formatted
        except asyncio.CancelledError:
            # best-effort 送达 aborted 帧（消费端可能已随任务取消停止读取；
            # 前端 onClose 兜底以 getSessionStatus 对账）
            was_aborted = True
            yield format_sse_event(RunCancelledEvent(content="Run aborted", reason="user_abort"))
            raise
        except Exception as e:  # noqa: BLE001 — SSE 流内错误以 RunErrorEvent 收口
            was_failed = True
            logger.error(
                "[approvals] continue error (session=%s run=%s): %s",
                session_id, run_id, e, exc_info=True,
            )
            yield format_sse_event(RunErrorEvent(content=str(e)))
        finally:
            if not abort_watcher.done():
                abort_watcher.cancel()
            record2 = session_manager.get(session_id)
            if record2 and record2.status == SessionStatus.RUNNING:
                if was_aborted or abort_event.is_set():
                    # aborted 终态 + 清残留信号（防毒化下一次 run，对齐 /messages）
                    await session_manager.set_aborted(session_id)
                    session_manager.clear_abort(session_id)
                    await session_manager.push_ws_event(session_id, {
                        "event_type": "run_aborted",
                        "session_id": session_id,
                    })
                elif was_failed:
                    # 续跑失败（如 agno RunNotFoundError — run 未落库，存储
                    # 断裂）→ 会话置 failed 而非 completed，前端状态可见
                    await session_manager.set_failed(session_id, "continue failed")
                else:
                    await session_manager.set_completed(session_id)

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── Section: pending clarification（澄清卡自愈源） ──────────────────


@router.get("/sessions/{session_id}/pending-clarification")
async def get_pending_clarification(
    session_id: str,
    _: str | None = Depends(get_current_user),
) -> Optional[Dict[str, Any]]:
    """会话当前待答澄清。

    澄清无 approvals 落库记录（无 approval_id），刷新回放只能从 message 历史
    推导卡片 —— 但历史不带 run_id，无法直接续跑。本端点扫会话 runs 取**最新**
    暂停中的澄清 requirement，供前端自愈对齐（模式同 ApprovalCard 的
    refreshApprovals 自愈）。无待答 → null。
    """
    record = session_manager.get(session_id)
    if record is None:
        return None
    agent = record.agent
    if agent is None:
        return None
    db = getattr(agent, "db", None)
    get_session = getattr(db, "get_session", None)
    if get_session is None:
        return None
    try:
        session = (
            await get_session(session_id=session_id)
            if iscoroutinefunction(get_session)
            else get_session(session_id=session_id)
        )
    except Exception:  # noqa: BLE001
        logger.warning("[approvals] pending-clarification session read failed", exc_info=True)
        return None
    runs = getattr(session, "runs", None) or []
    for run in reversed(runs):  # 最新 run 优先
        active = _active_clarification_requirements(run)
        if not active:
            continue
        req = active[-1]
        payload = serialize_clarification_payload(req.tool_execution)
        payload["run_id"] = getattr(run, "run_id", None)
        return payload
    return None
