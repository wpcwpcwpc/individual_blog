## 1. 规格与设计

- [x] 1.1 `proposal.md`：改造动机、变更清单、新增能力（`coding-agent` / `coding-hitl-gates`）与影响面
- [x] 1.2 `design.md`：八项决策（去状态机 / 工具分层 / 审批工具独立 / 危险命令双层拦截 / 编辑委托 / 依赖切断 / 与游戏侧资产共存 / 镜像区口径待确认）与风险登记
- [x] 1.3 两份规格：`specs/coding-agent/spec.md`、`specs/coding-hitl-gates/spec.md`

## 2. 审批工具层

- [x] 2.1 新增 `mirror/qa-agent/tools/coding_tools.py`：`request_approval`（kind ∈ plan / design，载荷非空校验 + 已废除业务 kind 定向报错）
- [x] 2.2 `write_file(path, content, purpose)`：阻塞审批 + 工作区路径解析 + 落盘前 Python 语法闸门（`.py` 才校验）
- [x] 2.3 `edit_file(file_path, old_string, new_string, replace_all, purpose)`：阻塞审批 + 委托既有编辑工具入口（保留改前必读 / 过期检测 / 多重匹配校验），取不到入口即报错不落盘
- [x] 2.4 `run_command(command, purpose)`：阻塞审批 + 危险模式自检（即使被直接调用，命中危险模式也只走审批）+ 复用既有命令执行器

## 3. 危险命令守卫

- [x] 3.1 新增 `mirror/qa-agent/hooks/coding_guard_hook.py`：不可逆命令模式表（递归删除、盘符格式化、强推、硬重置、清空工作树、整表删除、发布、递归改权限等）
- [x] 3.2 `coding_guard_tool_hook`：按 Agno 中间件签名（`function_name` / `func` / `arguments` / `agent`）拦 `bash`，命中即返回改用审批通道的指引，不执行下游
- [x] 3.3 非目标工具与安全命令直接放行下游

## 4. Agent 定义与注册

- [x] 4.1 新增 `mirror/qa-agent/agents/builtin/coding_agent.py`：`CodingAgentDefinition`（`agent_id=coding-agent`、只读工具面、`max_turns=60`、MCP 与历史注入关闭、skill 发现保留）
- [x] 4.2 通用编码行为契约 instructions（探索优先 / 禁臆造 / 方案门 / 写盘唯一入口 / 改前必读 / 命令分级 / 任务清单 / 验证后声明 / 风格与依赖纪律 / 不落凭证）
- [x] 4.3 `build_coding_agent_definition()`：装配四个审批工具 + 内置结构化问答工具 + 守卫 hook
- [x] 4.4 `agents/registry.py` 注册新 Agent；删除 `agents/builtin/case_studio_agent.py`

## 5. 验证与登记

- [x] 5.1 四个文件（三个新增 + `agents/registry.py`）`py_compile` 通过
- [x] 5.2 旧 Agent 引用核对：全仓检索无 `case_studio_agent` / `CaseStudioAgentDefinition` 残留；`coordinator/prompts.py` 的 Worker 兜底表原列 `CaseStudioAgent` 已换为 `CodingAgent`（删旧文件带来的悬空引用）；`hooks/mcp_safety_hook.py` 的白名单死键按设计 D7 保留
- [x] 5.3 待作者确认项登记：镜像区「只删不创」口径（`GLOSSARY.md` 第〇节）与本次受控加法是否同步（design D8 / proposal Impact）
- [x] 5.4 离线结构核对（Agno 以最小桩替代）：20 项断言全过 —— 注册表含 `coding-agent` 且业务工具零命中、`extra_tools` 装配齐四审批工具与两个澄清工具、守卫 hook 已挂、kind 校验（含已废除 kind）、载荷非空校验、Python 语法闸门阻断坏文件、写盘后改前必读放行、未读文件被拒、`run_command` 的 purpose 必填
- [x] 5.5 守卫行为核对：18 条危险样本全命中、10 条安全样本零误伤；`bash` 危险命令被拦（下游未执行）、安全命令放行、非守卫工具直通
- [ ] 5.6 运行时链路复核（未覆盖）：需在装好依赖的环境启动服务，走通「方案审批 → 写盘审批 → 危险命令拦截 → 审批通道执行」四步；`run_command` 对 `bash` 执行器的实际委托亦待该环境确认

