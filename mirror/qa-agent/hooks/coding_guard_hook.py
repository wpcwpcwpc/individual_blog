"""QA Agent System — Coding Guard Tool Hook (Agno middleware)

通用编码 Agent 的危险命令守卫（add-ai-coding-agent D4）：在工具调用层拦下
命中不可逆模式的 ``bash`` 调用，指向审批通道 ``run_command``。

Agno contract
-------------
与 ``execution_log_hook`` 相同的中间件签名——Agno 按参数名注入 kwargs
（``agno/tools/function.py::_build_hook_args``），因此参数名必须保持
``function_name`` / ``func`` / ``arguments`` / ``agent``。

为什么需要这一层
----------------
提示词要求「破坏性命令改走审批通道」只是软约束，模型可能直接调 ``bash``。
本 hook 提供确定性拦截：命中即不执行下游，返回改用 ``run_command`` 的指引。
换说法的变形由审批卡的命令全文覆盖（人工可判），不追求正则完备。

本模块不 import 任何业务模块（仅 ``re`` / ``asyncio``），无 import 副作用。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 受守卫的工具（只有通用命令工具；写盘工具本身已带审批）
_GUARDED_TOOLS = frozenset({"bash"})

#: 不可逆命令模式（正则, 人类可读标签）。命中即拦，改走 run_command 审批通道。
#: 覆盖面：递归/强制删除、盘符格式化、版本控制不可逆操作、整表删除、
#: 发布与外发、递归改权限、管道执行远端脚本。
DANGER_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # 递归 / 强制删除
    (r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)", "递归删除（rm -r）"),
    (r"\bdel\s+/[a-zA-Z]*[fsq]", "强制/静默删除（del /f /s /q）"),
    (r"\b(rd|rmdir)\s+/s", "递归删目录（rd /s）"),
    (r"\bRemove-Item\b[^\n]*-Recurse", "递归删除（Remove-Item -Recurse）"),
    # 磁盘 / 系统
    (r"\bformat\s+[a-zA-Z]:", "格式化盘符"),
    (r"\bmkfs\b", "格式化文件系统"),
    (r"\bdd\s+[^\n]*of=/dev/", "裸设备写入（dd of=/dev/...）"),
    (r"\bdiskpart\b", "磁盘分区操作"),
    (r"\b(shutdown|reboot|halt)\b", "关机/重启主机"),
    # 版本控制不可逆
    (r"\bgit\s+push\b[^\n]*(\s-f\b|--force)", "强制推送（git push --force）"),
    (r"\bgit\s+reset\s+--hard", "硬重置（git reset --hard）"),
    (r"\bgit\s+clean\b[^\n]*-[a-zA-Z]*[fd]", "清空工作树（git clean -fd）"),
    (r"\bgit\s+branch\s+-D\b", "强制删除分支（git branch -D）"),
    (r"\bgit\s+checkout\s+--\s+\.", "丢弃工作区改动（git checkout -- .）"),
    # 数据库
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA|COLLECTION)\b", "整表/整库删除（DROP）"),
    (r"\bTRUNCATE\s+TABLE\b", "清空整表（TRUNCATE TABLE）"),
    # 发布 / 外发
    (r"\bnpm\s+publish\b", "发布包（npm publish）"),
    (r"\btwine\s+upload\b", "发布包（twine upload）"),
    (r"\bdocker\s+push\b", "推送镜像（docker push）"),
    (r"\bgh\s+(pr\s+merge|release\s+create)\b", "远端合并/发版（gh）"),
    # 权限
    (r"\bchmod\s+-[a-zA-Z]*R", "递归改权限（chmod -R）"),
    (r"\bchown\s+-[a-zA-Z]*R", "递归改属主（chown -R）"),
    # 管道执行远端脚本
    (r"\b(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(sh|bash|zsh)\b", "管道执行远端脚本"),
    (r"\b(iwr|Invoke-WebRequest)\b[^\n]*\|\s*iex\b", "管道执行远端脚本（iex）"),
)

_DANGER_COMPILED: List[Tuple[re.Pattern, str]] = [
    (re.compile(pattern, re.IGNORECASE), label) for pattern, label in DANGER_PATTERNS
]


def detect_dangerous_command(command: str) -> Optional[str]:
    """返回命中的危险模式标签；未命中返回 None。"""
    if not command:
        return None
    for pattern, label in _DANGER_COMPILED:
        if pattern.search(command):
            return label
    return None


def build_interception_message(command: str, label: str) -> str:
    """构造拦截指引（给模型的可执行下一步，而非仅报错）。"""
    return (
        f"⛔ 已拦截：命中不可逆命令模式「{label}」，命令未执行。\n"
        f"命令：{command[:200]}\n"
        "破坏性操作只能经 `run_command(command=..., purpose=...)` 提交——"
        "该工具每次都会弹审批卡，由人工确认后执行。\n"
        "若本条命令并非破坏性操作，请改用普通 `bash`，或拆分为只读步骤。"
    )


async def coding_guard_tool_hook(
    function_name: str,
    func: Callable[..., Any],
    arguments: Dict[str, Any],
    *,
    agent: Optional[Any] = None,
) -> Any:
    """拦下命中危险模式的命令工具调用，其余调用直接放行下游。

    参数名 MUST 保持 Agno 约定的 ``function_name`` / ``func`` / ``arguments``
    / ``agent``（见模块 docstring）。

    Args:
        function_name: 被调用的工具名。
        func: 中间件链的下一环（同步或异步均可）。
        arguments: 工具参数字典。
        agent: Agno 注入的 Agent 实例。

    Returns:
        命中危险模式 → 拦截指引字符串（下游不执行）；
        否则 → 下游返回值。
    """
    args: Dict[str, Any] = dict(arguments) if arguments else {}

    if function_name in _GUARDED_TOOLS:
        command = str(args.get("command") or "")
        label = detect_dangerous_command(command)
        if label:
            logger.warning(
                "[coding_guard] intercepted %s: %s | %s",
                function_name, label, command[:160],
            )
            return build_interception_message(command, label)

    result = func(**args)
    if asyncio.iscoroutine(result):
        result = await result
    return result
