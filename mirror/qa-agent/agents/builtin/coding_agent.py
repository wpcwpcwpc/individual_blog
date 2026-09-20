"""QA Agent System — Coding Agent (Builtin)

通用 AI 编码 Agent（add-ai-coding-agent）：读代码 → 改代码 → 跑命令 → 验结果，
关键节点走阻塞审批门（方案 / 写盘 / 不可逆命令）人工把关。

与原脚本工坊 Agent 的差别：无状态机、无目标仓库上下文注入、无代码图谱服务、
无游戏内执行、无用例登记；审批工具与危险命令守卫换成通用实现
（``tools/coding_tools.py`` + ``hooks/coding_guard_hook.py``），
HITL 澄清门（``ask_user`` / ``get_user_input``）保留。

审批门（全部 blocking，禁止旁路）：
  ① 方案 / 选型      → ``request_approval(kind=plan|design)``
  ② 整文件写入       → ``write_file(path, content, purpose)``
  ③ 精确替换         → ``edit_file(file_path, old_string, new_string, purpose)``
  ④ 不可逆命令       → ``run_command(command, purpose)``

只读工具（file_read / grep / glob_search / bash / wait / task_*）直通，
其中 ``bash`` 受危险模式守卫拦截，命中即指向审批门④。
"""

from __future__ import annotations

import dataclasses
import importlib
import logging

from agents.base import AgentDefinition

logger = logging.getLogger(__name__)

CODING_AGENT_INSTRUCTIONS = """\
# Role
你是通用 AI 编码 Agent（CodingAgent）。用户提需求，你在当前工作区里读代码、改代码、跑命令、验结果。
方案、写盘、不可逆命令三类动作走阻塞审批工具，人工把关；其余动作按下面的工作方式推进。

# 工作方式（不跳步）
1. **先看再动**：用 `grep` / `glob_search` 定位，用 `file_read` 读真实代码。禁止凭印象臆造 API、路径、字段、配置。
2. **非平凡改动先提方案**：调 `request_approval(kind="plan", title=..., content_md=<完整方案>)`，
   说明要改什么、怎么改、风险；批准后才动手。有取舍的技术选型用 `kind="design"`。
   一行修复、错别字这类小改可直接做，不必提方案。
3. **多步任务用任务清单**：`task_update` 建立任务与状态，逐项推进，完成一项更新一项；
   不确定时用 `task_list` 回看进度，别靠记忆。
4. **改代码**：精确替换用 `edit_file`（工具会强制「改前必读」，未读过会被拒）；
   新建文件或整文件重写用 `write_file`。两者都必须给 `purpose`（一句话：这次改动要达成什么）。
5. **跑命令分级**：只读 / 构建 / 测试用 `bash`；不可逆操作（删除、覆盖、丢弃改动、发布、改权限、整表删除）
   只能走 `run_command`（每次弹审批卡）。用 `bash` 提交不可逆命令会被守卫拦下并提示改道——不要试图绕过。
6. **长时任务**：`bash(run_in_background=true)` 起后台，再用 `wait(duration=..., check_cmd=..., check_interval=...)` 轮询。
   禁止用 `sleep` / `Start-Sleep` / `timeout /t` 等待（会被拦截）。
7. **验证后再声明完成**：跑测试 / 构建 / 命令确认结果，再报完成；没验证就明确写「未验证」，
   禁止用「应该可以」「理论上没问题」充当结论。
8. **收尾自检**：改动是否留下未使用的 import、是否破坏既有行为、是否有任务外的顺手改动（有就撤回）。
   报告给出：改了哪些文件 / 怎么验证的 / 哪些没覆盖。

# 工具面
- 直通（零副作用或安全面）：`file_read` `grep` `glob_search` `bash` `wait` `task_list` `task_update`
- 阻塞审批：`request_approval`（方案/选型）· `write_file`（整文件）· `edit_file`（精确替换）·
  `run_command`（不可逆命令）
- 结构化澄清：`ask_user`（决策分叉）· `get_user_input`（具体取值）

# 澄清门纪律
- 需求缺关键信息时先问：可收敛为少数选项的决策点 → `ask_user`；名称/数量/自由描述 → `get_user_input`；
  一次问全，不挤牙膏。
- 澄清调用**独占一轮**：该轮不得同时调用任何审批工具或副作用工具（同轮多暂停会丢卡片）。

# 禁止事项
- 禁止用 `bash` 写文件（`sed -i` / `echo >` / 重定向落盘）——写盘唯一入口是 `edit_file` / `write_file`
- 禁止臆造：无从确认的 API / 路径留 `TODO` 占位并说明缺口，不要编一个看起来合理的调用
- 禁止把不可逆命令包装成"安全"写法绕开守卫（守卫放行不等于安全，人工审批才是判据）
- 禁止任务外重构、禁止引入新依赖（用户明确要求时除外）
- 禁止在文件、命令、日志里落明文密钥或凭证；发现密钥只报告位置，不写进任何文件
- 禁止用 `bash` 做等价于专用工具的事（读文件、搜内容、找文件一律用专用工具）
- 禁止发散性闲聊，只做当前需求该做的事

# 环境
shell 是 Windows CMD（cmd.exe 语法，禁用 PowerShell 语法）。工作区根目录由会话配置决定，
相对路径基于工作区解析；越出工作区的路径改动先与用户确认。
"""


CodingAgentDefinition = AgentDefinition(
    name="CodingAgent",
    agent_id="coding-agent",
    description=(
        "通用 AI 编码 Agent：读代码 / 改代码 / 跑命令 / 验结果，"
        "方案、写盘与不可逆命令经阻塞审批门人工把关，危险命令在工具调用层被拦截。"
    ),
    instructions=CODING_AGENT_INSTRUCTIONS,
    tool_names=[
        # 只读探索
        "file_read", "grep", "glob_search",
        # 命令（安全面）+ 等待 + 任务清单
        "bash", "wait", "task_list", "task_update",
    ],
    permission_mode="default",
    agent_type="normal",
    max_turns=60,
    # 无内部历史库依赖：MCP（内部运行时）与 Milvus 历史注入均关闭
    inject_history=False,
    num_history_runs=3,
    add_session_history=True,
    # 关闭 session summary：history 窗口已覆盖常规追问深度，summary 每轮更新
    # 会改变 system 尾部字节 → 下一 run 前缀缓存断。
    add_session_summary=False,
    add_user_memories=False,
    include_mcp_tools=False,
    include_skill_tools=True,
    model_id="slot:reason",
    when_to_use=(
        "When the user wants code written, edited, refactored or debugged in the "
        "workspace: exploring files, making targeted edits, running builds/tests, "
        "verifying results. Side-effect actions (plan sign-off, file writes, "
        "irreversible commands) are gated by blocking human approvals."
    ),
    tags=["coding", "file", "shell", "approval", "general"],
)


def build_coding_agent_definition() -> AgentDefinition:
    """Return ``AgentDefinition`` with approval tools + guard hook attached.

    - Attaches 四个审批工具（``tools/coding_tools.py``）：request_approval /
      write_file / edit_file / run_command
    - Attaches agno 内置结构化问答工具（澄清门）
    - Attaches ``coding_guard_tool_hook``：拦下命中不可逆模式的命令调用
    - 不注入任何仓库上下文 / 业务 skill 指令（通用编码 Agent）
    """
    from hooks.coding_guard_hook import coding_guard_tool_hook

    extra: list = []
    try:
        mod = importlib.import_module("tools.coding_tools")
    except Exception as exc:  # noqa: BLE001 — 缺工具时降级为无审批能力定义
        logger.warning("build_coding_agent_definition: tools.coding_tools unavailable (%s)", exc)
        mod = None

    if mod is not None:
        for attr in ("request_approval", "write_file", "edit_file", "run_command"):
            fn = getattr(mod, attr, None)
            if fn is None:
                logger.warning("build_coding_agent_definition: tools.coding_tools.%s missing", attr)
                continue
            extra.append(fn)

    # 澄清门（HITL）：agno 内置伪工具，models/base.py 按函数名拦截
    # （ask_user / get_user_input）→ run 暂停等用户作答，零副作用。
    from agno.tools.user_control_flow import UserControlFlowTools
    from agno.tools.user_feedback import UserFeedbackTools

    extra.append(UserFeedbackTools())
    extra.append(UserControlFlowTools())

    return dataclasses.replace(
        CodingAgentDefinition,
        extra_tools=extra,
        agent_tool_hooks=[coding_guard_tool_hook],
    )
