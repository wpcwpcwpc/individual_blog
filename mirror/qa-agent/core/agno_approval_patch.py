"""QA Agent System — Agno Approval Patch

修复 agno 2.6.22 审批 pause 的两个多轮审批缺陷：

缺陷 1（acreate_approval_from_pause 去重范围）：
  原实现扫描 run_response.tools，只要**任意**工具带 approval_type=required 且
  approval_id 已 stamp 就跳过创建。多轮审批 run（同一轮内多道审批门）中，
  上一道门批准后的 approval_id 永久留在 tools → 后续所有新审批门的审批记录
  被静默吞掉 → 前端拿不到 approval_id → 续跑断裂、会话停摆。
  2026-08-31 真机 + 本地
  scripts/repro_continue_break.py 复现：pause#2 发生但 create_approval 未调用。

缺陷 2（_build_approval_dict 工具选取）：
  _get_first_approval_tool 取「第一个」审批工具 —— 多轮场景取到已批准的旧
  工具，新审批记录的 tool_name/tool_args 全错。

修复（本 patch 完全自建不复刻 agno 的两处错误选取）：
  1. 目标工具 = 本次实际暂停（is_paused）且 approval_type=required 且
     尚未 stamp approval_id 的工具（即新审批门）。
  2. 无新审批工具但有已 stamp 的暂停工具 → 视为重入，返回已有 id（保留
     agno 原去重意图）。
  3. stamp 只写目标工具（不全局传播，避免污染历史工具/后续判定）。

安装：main.py lifespan 调 apply_agno_approval_patch()（幂等）。注意 agno.agent._run
顶部 from-import 已绑定旧引用，必须同时替换调用方命名空间。

缺陷 3（_apply_approval_to_tools rejected 分支丢拒绝意见）：
  resolve_approval 落库的 resolution_data.note 在 agno 原生 rejected 分支被
  整个丢弃（只置 confirmed=False）→ reject_tool_call 回退通用文案
  "Tool call was rejected" → LLM 从未见过用户意见 → 拒绝意见形同虚设。
  修复：rejected 分支后置补写 confirmation_note（wrap_reject_note 包装，
  Claude Code tool_result 范式：原文 verbatim + 归因 + 行为指引）。
  该函数仅被 agno.run.approval 模块内两处（sync/async resolver）引用，
  无跨模块 from-import — 单点 patch 模块属性即生效。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_APPLIED = False


def _pause_type(tool: Any) -> str:
    if getattr(tool, "requires_user_input", False):
        return "user_input"
    if getattr(tool, "external_execution_required", False):
        return "external_execution"
    return "confirmation"


def _pick_target_tools(tools: list) -> tuple[list, list]:
    """返回 (本次新审批门工具[未stamp], 已stamp的暂停工具)。"""
    new_gate: list = []
    stamped: list = []
    for t in tools or []:
        if getattr(t, "approval_type", None) != "required":
            continue
        if not getattr(t, "is_paused", False):
            continue
        if getattr(t, "approval_id", None) is None:
            new_gate.append(t)
        else:
            stamped.append(t)
    return new_gate, stamped


def _build_record(
    run_response: Any,
    tool: Any,
    agent_id: Optional[str],
    agent_name: Optional[str],
    user_id: Optional[str],
) -> dict:
    """构建审批记录（字段对齐 agno _build_approval_dict，tool 取本次暂停项）。"""
    try:
        from agno.run.base import RunStatus
        run_status = RunStatus.paused.value
    except Exception:
        run_status = "paused"

    tool_name = getattr(tool, "tool_name", None)
    return {
        "id": str(uuid4()),
        "run_id": getattr(run_response, "run_id", None) or str(uuid4()),
        "session_id": getattr(run_response, "session_id", None) or "",
        "status": "pending",
        "approval_type": "required",
        "pause_type": _pause_type(tool),
        "tool_name": tool_name,
        "tool_args": getattr(tool, "tool_args", None),
        "source_type": "agent",
        "agent_id": agent_id,
        "user_id": user_id,
        "source_name": agent_name,
        "requirements": None,
        "context": {"tool_names": [tool_name]} if tool_name else None,
        "resolved_by": None,
        "resolved_at": None,
        "created_at": int(time.time()),
        "updated_at": None,
        "run_status": run_status,
    }


def _patched_body(db: Any, run_response: Any, **create_kwargs: Any) -> Optional[str]:
    tools = getattr(run_response, "tools", None) or []
    new_gate, stamped = _pick_target_tools(tools)

    if not new_gate:
        if stamped:
            # 本次暂停的工具全部已 stamp —— 重入防护（agno 原去重意图）
            return stamped[0].approval_id
        return None  # 无审批需求（对齐 agno _has_approval_requirement 语义）

    tool = new_gate[0]
    try:
        record = _build_record(
            run_response, tool,
            agent_id=create_kwargs.get("agent_id"),
            agent_name=create_kwargs.get("agent_name"),
            user_id=create_kwargs.get("user_id"),
        )
        create_fn = getattr(db, "create_approval", None)
        if create_fn is None:
            return None
        create_fn(record)
        approval_id: str = record["id"]
        # stamp 只写本次工具（不全局传播 — 原实现全局 stamp 污染历史工具，
        # 正是多轮场景去重误判的根源）
        try:
            tool.approval_id = approval_id
        except Exception:
            pass
        logger.info(
            "[agno-approval-patch] created approval %s (tool=%s run=%s)",
            approval_id, record.get("tool_name"), record.get("run_id"),
        )
        return approval_id
    except NotImplementedError:
        return None
    except Exception:
        logger.exception("[agno-approval-patch] create approval failed")
        return None


async def _patched_acreate(db: Any, run_response: Any, **kwargs: Any) -> Optional[str]:
    if db is None:
        return None
    return _patched_body(db, run_response, **kwargs)


def _patched_sync(db: Any, run_response: Any, **kwargs: Any) -> Optional[str]:
    if db is None:
        return None
    return _patched_body(db, run_response, **kwargs)


def apply_agno_approval_patch() -> None:
    """幂等安装 patch（main.py lifespan 启动时调用一次）。

    注意：agno.agent._run 顶部 ``from agno.run.approval import
    acreate_approval_from_pause`` 已绑定旧引用 —— 必须同时替换
    调用方命名空间，仅 patch agno.run.approval 无效。
    """
    global _APPLIED
    if _APPLIED:
        return
    try:
        from agno.run import approval as _mod

        _mod.acreate_approval_from_pause = _patched_acreate
        if hasattr(_mod, "create_approval_from_pause"):
            _mod.create_approval_from_pause = _patched_sync

        import agno.agent._run as _run_mod

        _run_mod.acreate_approval_from_pause = _patched_acreate
        _run_mod.create_approval_from_pause = _patched_sync

        _APPLIED = True
        logger.info("[agno-approval-patch] installed (multi-approval dedup fix)")
    except Exception:
        logger.exception("[agno-approval-patch] install FAILED — multi-approval runs will break")


# ---------------------------------------------------------------------------
# 缺陷 3 patch：rejected 分支注入拒绝意见
# ---------------------------------------------------------------------------

_NOTE_PATCH_APPLIED = False


def _patched_apply_approval_to_tools(
    tools: Any,
    approval_status: str,
    resolution_data: Any,
    _orig: Any,
) -> None:
    """wrap agno ``_apply_approval_to_tools``：rejected + note → 补写 confirmation_note。

    approved 分支完全透传原函数（行为不变）；rejected 分支在原函数置
    ``confirmed=False`` 后，对 confirmation 型工具追加意见注入。fail-open：
    注入自身异常不外抛（spec「注入失败不阻断续跑」）。
    """
    _orig(tools, approval_status, resolution_data)
    if approval_status != "rejected":
        return
    if not resolution_data:
        return
    try:
        from core.reject_note import wrap_reject_note

        wrapped = wrap_reject_note((resolution_data or {}).get("note"))
        if not wrapped:
            return
        for tool in tools or []:
            if getattr(tool, "approval_type", None) != "required":
                continue
            if getattr(tool, "requires_confirmation", False):
                tool.confirmation_note = wrapped
                logger.info(
                    "[agno-approval-patch] injected reject note (len=%d)", len(wrapped),
                )
    except Exception:
        logger.warning(
            "[agno-approval-patch] reject note injection FAILED — "
            "falling back to agno generic reject text",
            exc_info=True,
        )


def apply_agno_reject_note_patch() -> None:
    """幂等安装缺陷 3 patch（main.py lifespan 启动时调用一次）。

    惰性校验：wrap 前探测 ``_apply_approval_to_tools`` 存在性与可调用性，
    失配时 warning + 不安装（fail-open，续跑退化为 agno 原生行为）。
    """
    global _NOTE_PATCH_APPLIED
    if _NOTE_PATCH_APPLIED:
        return
    try:
        from agno.run import approval as _mod
        from inspect import signature

        orig = getattr(_mod, "_apply_approval_to_tools", None)
        if not callable(orig):
            logger.warning(
                "[agno-reject-note-patch] _apply_approval_to_tools missing — skip install",
            )
            return
        sig = signature(orig)
        if len(sig.parameters) != 3:  # tools / approval_status / resolution_data
            logger.warning(
                "[agno-reject-note-patch] signature mismatch %r — skip install "
                "(agno upgraded? re-verify)", sig,
            )
            return

        def _wrapped(tools, approval_status, resolution_data):
            return _patched_apply_approval_to_tools(
                tools, approval_status, resolution_data, _orig=orig,
            )

        _mod._apply_approval_to_tools = _wrapped

        _NOTE_PATCH_APPLIED = True
        logger.info("[agno-reject-note-patch] installed (reject note injection fix)")
    except Exception:
        logger.exception("[agno-reject-note-patch] install FAILED — reject notes stay lost")


# ---------------------------------------------------------------------------
# continue 路径 tracing 补丁
# ---------------------------------------------------------------------------
# openinference-instrumentation-agno 只 wrap _run/_arun/_run_stream/_arun_stream，
# 未 wrap continue 路径（_acontinue_run_stream）→ 续跑期间的 model/tool span
# 无 run 级 root 挂靠，各自成为独立 trace（Spans=1、无 user/session/run 属性）
# → 执行追踪列表高度分裂（2026-08-31 真机截图）。
# 修复：用 instrumentor 自带的 _RunWrapper.arun_stream wrap _acontinue_run_stream，
# 补一个 agent 级 root span（agent_id/session_id/user_id 齐全），内部 span 自动挂靠。

_TRACE_PATCH_APPLIED = False


def apply_agno_continue_trace_patch() -> None:
    """幂等安装 continue 路径 tracing 补丁。

    MUST 在 AgnoInstrumentor().instrument() 之后调用（依赖其 tracer 与
    model/tool 层 wrap 已就位；root span 需挂住它们的输出）。
    """
    global _TRACE_PATCH_APPLIED
    if _TRACE_PATCH_APPLIED:
        return
    try:
        import agno.agent._run as _run_mod
        from openinference.instrumentation.agno._runs_wrapper import _RunWrapper

        if getattr(_run_mod._acontinue_run_stream, "_qa_traced", False):
            _TRACE_PATCH_APPLIED = True
            return

        # AgnoInstrumentor 的 tracer 在 _instrument 内创建；这里直接新建一个
        # 同参 OITracer 等价可用（同一 TracerProvider 下 trace id 一致）。
        from opentelemetry import trace as trace_api
        from openinference.instrumentation import OITracer, TraceConfig
        from openinference.instrumentation.agno._runs_wrapper import _RunWrapper
        from openinference.instrumentation.agno.version import __version__ as _oi_ver

        tracer = OITracer(
            trace_api.get_tracer("qa-agent.continue-trace-patch", _oi_ver),
            config=TraceConfig(),
        )
        wrapper = _RunWrapper(tracer=tracer)

        _orig = _run_mod._acontinue_run_stream

        async def _traced_acontinue_run_stream(*args: Any, **kwargs: Any):
            async for item in wrapper.arun_stream(_orig, None, args, kwargs):
                yield item

        setattr(_traced_acontinue_run_stream, "_qa_traced", True)
        _run_mod._acontinue_run_stream = _traced_acontinue_run_stream

        _TRACE_PATCH_APPLIED = True
        logger.info("[agno-continue-trace-patch] installed (continue root span fix)")
    except Exception:
        logger.exception("[agno-continue-trace-patch] install FAILED — continue spans stay fragmented")
