## 1. 后端分层清理

- [x] 1.1 新建 `api/approvals_routes.py`（自原工坊路由派生）：router 去掉 path prefix、tag 改 `approvals`、日志前缀 `[case-studio]` → `[approvals]`、模块 docstring 重写为 Agent 无关的端点清单
- [x] 1.2 删除两个游戏端点及其实现：`GET /file`（业务落盘白名单读取）、`GET /kb/entry`（业务知识库读取），连带清掉其在文件内的 docstring 与注释引用
- [x] 1.3 删除 `api/case_studio_game_routes.py`（客户端/服务端进程控制与快照）
- [x] 1.4 删除 `api/case_studio_reports_routes.py`（游戏侧 run_dir 面板）
- [x] 1.5 删除 `api/case_studio_routes.py`（已被 1.1 取代）
- [x] 1.6 `api/server.py`：三处 router include 合并为审批面一处；`api/agent_os_adapter.py` 注释去业务语义

## 2. 验证

- [x] 2.1 `ast.parse` 核对三个被改动文件（`approvals_routes.py` / `server.py` / `agent_os_adapter.py`）语法通过
- [x] 2.2 残留检索：后端已无 `/case-studio` 路由定义、无对已删模块的 import（`read_case_file` / `read_kb_entry` / 三个 router 名零命中）
- [x] 2.3 端点集合核对：`/approvals`、`/approvals/count`、`/approvals/{id}`、`/approvals/{id}/resolve`、`/sessions/{id}/runs/{run_id}/continue`、`/sessions/{id}/pending-clarification` 全部保留
- [ ] 2.4 运行时核对：启动服务确认路由注册与审批流（本机无依赖环境，未覆盖）

## 3. 前端同步（本轮完成）

- [x] 3.1 API 域改名：`src/api/caseStudio.ts` → `src/api/approvals.ts`，只留审批 list/detail/resolve + continue SSE + pending-clarification；URL 换 `/approvals` 与 `/sessions/...`；删除 game / reports / 读用例文件 / 读知识条目四组函数与类型
- [x] 3.2 store 收敛：`stores/caseStudioStore.ts` → `stores/codingStore.ts`，只留审批待办/历史/拉取态（游戏面板状态机、Reports 树、脚本预览、人工直跑全部移除）
- [x] 3.3 卡片搬迁与改造：`components/caseStudio/` → `components/approvals/`（ApprovalCard / ClarifyCard + 测试）；kind 语义改为 `plan` / `design` / `file_write` / `file_edit` / `command`，body 增命令卡与替换预览、删 REPL 卡与登记卡
- [x] 3.4 面板下线：删除 `GameControlPanel` / `DebugDataPanel` / `ScriptPreviewPanel` 与 `pages/CaseStudioPage/`
- [x] 3.5 页面重建：`pages/CodingPage/index.tsx` —— 单列对话流（timeline 承载审批/澄清卡）+ 待审批徽标 + 返回工作台；去掉右侧运维面板与拖拽分栏
- [x] 3.6 路由与导航：`/case-studio` → `/coding`（`App.tsx` 路由与懒加载）、导航项改「编码工作台」、SessionPage 重定向与 SessionList 过滤同步、`agentIds.ts` 常量改 `CODING_AGENT_ID = 'coding-agent'`
- [x] 3.7 内网链接下架：`navRegistry` 删除两个指向内部平台域名的 link 项（脱敏硬红线，连带清理未再使用的图标 import）
- [x] 3.8 全仓残留检索：`case-studio` / `caseStudio` / `CaseStudio` / `case_studio` / `CASE_STUDIO` 在 UI 内零命中；对已删模块的 import 零命中
- [x] 3.9 验证：`tsc -b` 通过；`vitest run` 21 文件 195 用例全通过（含搬迁后的 ApprovalCard 6 例与 ClarifyCard 7 例）；`npm run build` 成功且产物含 `CodingPage-*.js` 分块、无 case-studio 分块
- [x] 3.10 顺带修复既有类型错误：`ClarifyCard` 引用了不存在的 `setMetaStatus`（既有破损，改为 `updateSessionMeta`）、测试 mock 的 spread 与返回值类型签名（均为本次搬迁文件内的既有问题）

## 4. 后续（未完成，需另行处理）

- [ ] 4.1 决定是否恢复审批卡「旧内容对比」：通用替代方案为 workspace 作用域的只读文件读取端点（design D3）
- [ ] 4.2 游戏层剩余清理：`tools/case_studio_tools.py`、`core/game_repo.py`、`core/reports_parser.py`、`config.case_studio_reports_root`、`session_state.case_*` 字段
- [ ] 4.3 内部变更号注释清理（`core/agno_approval_patch.py`、`core/reject_note.py`、`core/storage.py`、`core/stream_adapter.py` 等）；其中 `core/stream_adapter.py` 有一处面向模型的文案提到业务 skill 铁律，优先级最高
- [ ] 4.4 后端运行时核对：启动服务确认路由注册与审批流（本机无 Python 依赖环境，未覆盖；前端侧已由构建与用例覆盖）
- [ ] 4.5 前端 lint 基线：仓库现存 43 个 lint 错误分布在未涉及本次改动的位置（EvalPage / tracing / docs / useResizable 等），不是本次引入，但会掩盖新问题，建议单独治理

