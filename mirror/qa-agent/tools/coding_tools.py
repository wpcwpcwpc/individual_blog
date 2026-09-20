"""QA Agent System — Coding Tools

通用编码 Agent 的审批工具层（add-ai-coding-agent）：

  - ``request_approval``  方案 / 选型确认（kind ∈ plan / design）
  - ``write_file``        整文件写入（阻塞审批 + Python 语法闸门）
  - ``edit_file``         精确替换（阻塞审批 + 委托既有编辑工具校验链）
  - ``run_command``       破坏性命令出口（阻塞审批）

全部副作用工具以 ``@approval`` 装饰：run 暂停 → 审批记录落库 → 用户决议 →
``acontinue_run`` 续跑。写盘只有 ``write_file`` / ``edit_file`` 两条路，
两者都要求 ``purpose``（审批卡信息充分性）。

本模块不注册进 ``api/server.py`` 的内置工具表 —— 由
``agents.builtin.coding_agent.build_coding_agent_definition`` 经
``AgentDefinition.extra_tools`` 装配，避免把通用审批工具暴露给其它 Agent。
"""

from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path
from typing import Any, Optional

from agno.approval import approval
from agno.tools import tool

from hooks.coding_guard_hook import detect_dangerous_command
from tools.workspace_utils import resolve_workspace_path

logger = logging.getLogger(__name__)

#: 审批工具允许的 kind（add-ai-coding-agent D2/spec coding-hitl-gates）
APPROVAL_KINDS = ("plan", "design")

#: 已废除的业务 kind → 定向提示（旧会话历史里可能出现）
_RETIRED_KINDS = {
    "new_block_design": "新积木方案确认属脚本工坊语义，通用编码 Agent 用 kind='design'",
    "knowledge_entry": "知识条目入库属脚本工坊语义，通用编码 Agent 不提供",
}


# ── Section: request_approval ───────────────────────────────────────


def request_approval_impl(kind: str, title: str, content_md: str) -> str:
    """审批载荷校验（无副作用）。

    Args:
        kind: 审批类型，MUST ∈ plan / design。
        title: 一句话标题（审批卡展示）。
        content_md: 方案全文 markdown（审批卡评审依据）。

    Returns:
        批准后的确认文本（拒绝时 agno 不执行本函数，直接回拒绝消息）。

    Raises:
        ValueError: kind 非法或载荷为空。
    """
    retired = _RETIRED_KINDS.get(kind)
    if retired is not None:
        raise ValueError(f"request_approval: kind={kind!r} 已废除 — {retired}")
    if kind not in APPROVAL_KINDS:
        raise ValueError(
            f"request_approval: kind={kind!r} 非法，MUST ∈ {APPROVAL_KINDS}"
        )
    if not title.strip():
        raise ValueError("request_approval: title 不能为空")
    if not content_md.strip():
        raise ValueError(
            "request_approval: content_md 不能为空（审批卡必须携带完整评审信息）"
        )
    return (
        f"审批通过（kind={kind}）：{title}。"
        "可以按方案继续；方案变更须重新提交审批。"
    )


@approval
@tool(
    name="request_approval",
    description=(
        "Request user approval for an implementation plan or a design decision. "
        "BLOCKING — the run pauses until the user resolves the approval card. "
        "kind MUST be one of: plan (实现方案，动手前确认), design "
        "(技术选型 / 架构取舍). content_md MUST carry the FULL review payload "
        "(方案全文) — the user decides based on this alone."
    ),
)
def request_approval(kind: str, title: str, content_md: str) -> str:
    """请求阻塞式人工审批（方案 / 选型）。

    Args:
        kind: Approval kind — plan | design.
        title: One-line title shown on the approval card.
        content_md: Full markdown payload for review.

    Returns:
        Confirmation string (approval granted path).

    Raises:
        ValueError: Invalid kind or empty payload.
    """
    return request_approval_impl(kind, title, content_md)


# ── Section: write_file ─────────────────────────────────────────────


def _require_purpose(tool_name: str, purpose: str) -> None:
    """审批卡信息充分性：purpose 必填。"""
    if not purpose or not purpose.strip():
        raise ValueError(
            f"{tool_name}: purpose 必填（审批卡信息充分性，spec coding-hitl-gates）"
        )


def _resolve_agent_path(agent: Any, tool_name: str, path: str) -> Path:
    """工作区路径解析（相对路径基于 session_state.workspace_root）。"""
    if not path or not path.strip():
        raise ValueError(f"{tool_name}: path 不能为空")
    if agent is None:
        raise RuntimeError(f"{tool_name}: agent 未注入，相对路径无法解析")
    try:
        return Path(resolve_workspace_path(agent, path.strip())).resolve()
    except OSError as exc:
        raise ValueError(f"{tool_name}: 路径解析失败 {path!r}: {exc}") from exc


def _syntax_gate(content: str, filename: str) -> None:
    """落盘前语法闸门（仅 Python 源码）。

    Raises:
        ValueError: 语法错误（含行号，agent 据此修复后重提审批）。
    """
    try:
        ast.parse(content, filename=filename)
    except SyntaxError as exc:
        raise ValueError(
            f"write_file: 语法校验失败（ast.parse）— {filename}:"
            f"line {exc.lineno}: {exc.msg}"
        ) from exc


def write_file_impl(agent: Any, path: str, content: str, purpose: str) -> str:
    """整文件写入（纯实现，可单测）。

    Args:
        agent: Agent 实例（路径解析用）。
        path: 目标文件路径（相对 workspace_root 或绝对路径）。
        content: 文件全文。
        purpose: 本次写入要达成什么（已由调用方校验非空）。

    Returns:
        确认文本（新建/覆盖、字符数、路径）。

    Raises:
        ValueError: 载荷为空 / 路径非法 / Python 语法错误。
    """
    if not content:
        raise ValueError("write_file: content 不能为空")
    target = _resolve_agent_path(agent, "write_file", path)
    if content.strip() and target.suffix.lower() == ".py":
        _syntax_gate(content, target.name)

    existed = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")

    # 记入文件状态缓存：后续 edit_file 的改前必读校验据此判定
    try:
        from tools.file_state_cache import get_file_state_cache

        get_file_state_cache().record_read(str(target), content.encode("utf-8"))
    except Exception:  # noqa: BLE001 — 缓存缺失只是降级，不影响落盘结果
        logger.debug("write_file: file state cache update skipped", exc_info=True)

    action = "覆盖" if existed else "新建"
    logger.info("[write_file] %s %s (%d chars) — %s", action, target, len(content), purpose)
    return f"{action}成功：{target}（{len(content)} 字符）。purpose={purpose}"


@approval
@tool(
    name="write_file",
    description=(
        "Write a whole file (create or overwrite) — BLOCKING approval. "
        "Use for new files or full rewrites; prefer edit_file for targeted "
        "changes. Python content is syntax-checked before hitting disk; "
        "invalid code never gets written. purpose is REQUIRED and shown on "
        "the approval card."
    ),
)
def write_file(agent, path: str, content: str, purpose: str) -> str:
    """整文件写入（审批门）。

    Args:
        agent: Agno-injected Agent instance.
        path: Target file path (relative to workspace root, or absolute).
        content: Full file content.
        purpose: What this write is meant to achieve (REQUIRED).

    Returns:
        Confirmation text.

    Raises:
        ValueError: Empty payload / invalid path / Python syntax error.
    """
    _require_purpose("write_file", purpose)
    return write_file_impl(agent, path, content, purpose)


# ── Section: edit_file ──────────────────────────────────────────────


def _file_edit_entrypoint() -> Optional[Any]:
    """取既有 file_edit 工具的入口函数（取不到返回 None）。"""
    try:
        from tools.file_tools import file_edit as file_edit_tool
    except Exception:  # noqa: BLE001
        logger.warning("edit_file: file_edit tool import failed", exc_info=True)
        return None
    entry = getattr(file_edit_tool, "entrypoint", None)
    return entry if callable(entry) else None


@approval
@tool(
    name="edit_file",
    description=(
        "Edit a file by exact search/replace — BLOCKING approval. Provide "
        "old_string (exact text to find, must be unique unless replace_all) "
        "and new_string. You MUST read the file with file_read before "
        "editing — the read-before-edit check is enforced. purpose is "
        "REQUIRED and shown on the approval card."
    ),
)
async def edit_file(
    agent,
    file_path: str,
    old_string: str,
    new_string: str,
    purpose: str,
    replace_all: bool = False,
) -> str:
    """精确替换（审批门，委托既有编辑工具校验链）。

    Args:
        agent: Agno-injected Agent instance.
        file_path: Path to the file to edit.
        old_string: Exact text to find (empty string = create new file).
        new_string: Replacement text.
        purpose: What this edit is meant to achieve (REQUIRED).
        replace_all: Replace all occurrences instead of requiring a unique match.

    Returns:
        Edit result or validation guidance (failures never write).
    """
    _require_purpose("edit_file", purpose)
    if old_string == new_string:
        raise ValueError("edit_file: old_string 与 new_string 相同，无改动可提交")

    entry = _file_edit_entrypoint()
    if entry is None:
        raise RuntimeError(
            "edit_file: 取不到 file_edit 入口（Agno 工具对象结构变化）——"
            "拒绝执行以避免绕过改前必读与精确匹配校验"
        )
    if agent is None:
        raise RuntimeError("edit_file: agent 未注入，相对路径无法解析")

    try:
        result = entry(
            agent=agent,
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 — 校验/写入异常一律回文本，不落半截改动
        logger.warning("edit_file: delegated edit failed: %s", exc, exc_info=True)
        return f"编辑未执行（{type(exc).__name__}）：{exc}"

    logger.info("[edit_file] %s — %s", file_path, purpose)
    return f"[purpose={purpose}]\n{result}"


# ── Section: run_command ────────────────────────────────────────────


def _bash_entrypoint() -> Optional[Any]:
    """取既有 bash 工具的入口函数（取不到返回 None）。"""
    try:
        from tools.bash_tool import bash as bash_tool
    except Exception:  # noqa: BLE001
        logger.warning("run_command: bash tool import failed", exc_info=True)
        return None
    entry = getattr(bash_tool, "entrypoint", None)
    return entry if callable(entry) else None


@approval
@tool(
    name="run_command",
    description=(
        "Execute an irreversible / destructive shell command — BLOCKING "
        "approval, EVERY call. This is the ONLY path for commands that delete, "
        "overwrite, discard work, publish, or otherwise cannot be undone "
        "(rm -rf, del /f, git reset --hard, git push --force, DROP TABLE, "
        "npm publish, chmod -R ...). Safe read/build/test commands go through "
        "the plain bash tool instead. purpose is REQUIRED and shown on the "
        "approval card together with the full command text."
    ),
)
async def run_command(command: str, purpose: str, timeout: Optional[int] = None) -> str:
    """破坏性命令出口（审批门，复用 bash 执行器）。

    Args:
        command: The shell command to execute (cmd.exe syntax).
        purpose: Why this command is needed (REQUIRED).
        timeout: Optional timeout override in seconds.

    Returns:
        Command output, error guidance, or background task ID.
    """
    _require_purpose("run_command", purpose)
    if not command or not command.strip():
        raise ValueError("run_command: command 不能为空")

    label = detect_dangerous_command(command)
    logger.info(
        "[run_command] approved execution (%s): %s — %s",
        label or "not-classified-dangerous", command[:160], purpose,
    )

    entry = _bash_entrypoint()
    if entry is None:
        raise RuntimeError("run_command: 取不到 bash 入口，命令未执行")

    kwargs: dict = {"command": command, "description": purpose}
    if timeout is not None:
        kwargs["timeout"] = timeout
    result = entry(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return f"[purpose={purpose}]\n{result}"
