## Why

镜像区里的脚本工坊 Agent 绑着两条内部业务链路：目标仓库上下文注入（仓库规则文件 + 代码图谱服务 + 落盘白名单）与用例生命周期（六阶段状态机、游戏内 REPL 执行、语义库与知识库入库）。这两条链路让它无法作为通用能力复用，也让它与内部系统深度耦合。

镜像区需要的是一个**通用编码型 Agent**：读代码、改代码、跑命令、验结果；不可逆动作由人工审批兜底。本次把 `agents/builtin/case_studio_agent.py` 整体替换为 `CodingAgent`，游戏侧与业务侧逻辑一并剥离，HITL（阻塞审批门 + 澄清门）保留。

## What Changes

- **Agent 替换**：新增 `agents/builtin/coding_agent.py`（`CodingAgent` / `coding-agent`），删除 `agents/builtin/case_studio_agent.py`，并在 `agents/registry.py` 注册
- **行为契约重写**：instructions 由「六阶段状态机 + 六道审批门」改为通用编码契约——先探索后动手、禁臆造 API、非平凡改动先过方案门、改前必读、命令分级、任务清单推进、验证通过才声明完成
- **工具面重建**：新增 `tools/coding_tools.py`，提供 `request_approval`（plan / design）、`write_file`、`edit_file`、`run_command` 四个阻塞审批工具；只读工具（file_read / grep / glob_search / bash / wait / task_list / task_update）直通
- **危险命令拦截**：新增 `hooks/coding_guard_hook.py`，命中不可逆命令模式时拦下 `bash` 调用并指向审批通道；`run_command` 作为唯一的破坏性命令出口
- **HITL 澄清门保留**：`ask_user` / `get_user_input`（agno 内置）继续挂载，独占一轮、零副作用
- **依赖切断**：不注入仓库规则文件、不接代码图谱服务、不查配置表 RAG、不推 SVN 信息、不加载工坊 skill；MCP 工具与历史记忆注入关闭
- **不动游戏侧资产**：工坊 API 路由、`tools/case_studio_tools.py` 其余工具、`session_state.case_*` 字段、工坊 skill 目录保持原样（其它调用方仍引用）

## Capabilities

### New Capabilities

- `coding-agent`: Agent 身份与行为契约——工具面构成、instructions 约束（探索优先、禁臆造、改动前审批、写盘唯一入口、验证后声明）、依赖切断项、与既有游戏侧资产的共存边界
- `coding-hitl-gates`: 分级审批——审批工具的 kind 语义与载荷要求、写盘闸门、危险命令双层拦截（工具调用层硬拦 + 审批通道人工兜底）、澄清门纪律

## Impact

- 新增：`mirror/qa-agent/agents/builtin/coding_agent.py`、`mirror/qa-agent/tools/coding_tools.py`、`mirror/qa-agent/hooks/coding_guard_hook.py`
- 修改：`mirror/qa-agent/agents/registry.py`（注册新 Agent）
- 删除：`mirror/qa-agent/agents/builtin/case_studio_agent.py`
- 不改：`api/case_studio_*.py`、`tools/case_studio_tools.py`、`core/*`、`hooks/mcp_safety_hook.py`（其 `CaseStudioAgent` 白名单条目变为死键，无害）
- 不受影响的既有契约：Agno Agent 装配链（`agents/base.py`）、审批流转链路（审批记录落库 → 前端决议 → 续跑）、工具注册表
- 风险与待确认：镜像区既有口径为「只删不创」（见 `GLOSSARY.md` 第〇节），本次属受控加法；是否同步该口径由作者决定（见 tasks 5.3）
- 验证范围：本机未安装 Agno 依赖，本次只能做到语法与结构核对；运行时链路需在装好依赖的环境复核（见 tasks 5.4）
