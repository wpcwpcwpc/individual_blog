"""QA Agent System — Reject Note Wrapper

审批拒绝意见包装。

Claude Code 范式对齐：拒绝反馈 = 工具结果（tool_result/is_error），
意见原文 verbatim + 归因声明 + 行为指引三段式包装。包装仅发生在
注入时（agno ToolExecution.confirmation_note），MUST NOT 改写 DB 中
``resolution_data.note`` 的审计原文。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 意见注入长度上限（超长截断保留头部，spec「超长意见截断」）
MAX_NOTE_LEN = 2000

_REJECT_PREFIX = "用户拒绝了本次审批。"
_REJECT_SUFFIX = "请基于意见修订后重新发起同一审批；不要原样重试。"


def wrap_reject_note(note: str | None) -> str:
    """包装审批拒绝意见为 LLM 可读的引导文本。

    Args:
        note: 用户在审批卡填写的拒绝意见原文。

    Returns:
        三段式包装文本（拒绝声明 + 意见原文 + 修订指引）；
        超过 ``MAX_NOTE_LEN`` 截断保留头部；空意见返回 ``""``
        （调用方回退 agno 原生通用文案，spec「无意见拒绝回退通用文案」）。
    """
    if not note or not note.strip():
        return ""
    trimmed = note.strip()
    if len(trimmed) > MAX_NOTE_LEN:
        trimmed = trimmed[:MAX_NOTE_LEN]
    return f"{_REJECT_PREFIX}\n拒绝意见：{trimmed}\n{_REJECT_SUFFIX}"
