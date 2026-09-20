## Why

上一变更把脚本工坊 Agent 换成了通用 `CodingAgent`，但工坊的 API 面整块留在后端：三个 `case_studio_*` 路由文件仍在服务，其中两个是纯游戏侧通道（客户端/服务端进程控制、游戏 Reports 面板），一个混装了通用审批/澄清底座与两个游戏专用端点。结果是「Agent 已经通用化，能力入口还叫脚本工坊」，业务侧残留继续以 URL、日志前缀与模块名对外暴露。

本轮按分层处置：**通用底座保留并改名去业务语义，纯游戏通道整块删除**。

## What Changes

- **删除两个纯游戏路由文件**：`api/case_studio_game_routes.py`（客户端/服务端进程控制、快照，走内部 MCP）、`api/case_studio_reports_routes.py`（游戏侧 run_dir 步骤树与截图）
- **审批面通用化**：`api/case_studio_routes.py` → `api/approvals_routes.py`；路由不带 path prefix，URL 不再绑定 Agent 名；日志前缀 `[case-studio]` → `[approvals]`；注释与 docstring 去业务语义
- **删除两个游戏专用端点**：`GET /case-studio/file`（游戏仓库用例文件读取，走落盘白名单）、`GET /case-studio/kb/entry`（业务知识库条目读取）
- **删除引用**：`api/server.py` 的三处 router include 合并为审批面一处；`api/agent_os_adapter.py` 注释去业务语义
- **前端同步**：API 域 `caseStudio.ts` → `approvals.ts`（只留审批/澄清/续跑）、store 收敛为 `codingStore`、审批卡与澄清卡搬到 `components/approvals/`、工坊页重建为「编码工作台」（`/coding`，`pages/CodingPage`）、导航与重定向同步、删除游戏面板与 Reports 面板
- **顺带脱敏**：导航里两个指向内部平台域名的 link 项下架（原属禁出项）
- **不动**：`tools/case_studio_tools.py`（游戏工具）、`core/game_repo.py`、`core/reports_parser.py`、`config.case_studio_reports_root`、`session_state.case_*` 字段

## Capabilities

### New Capabilities

- `approvals-surface`: 审批与澄清路由的 Agent 无关契约——URL 构成、端点集合、业务端点缺席、前缀与日志命名约束、前端消费面（只走审批与续跑端点、工作台路由）

## Impact

- 新增：`mirror/qa-agent/api/approvals_routes.py`、`mirror/qa-agent-ui/src/api/approvals.ts`、`src/stores/codingStore.ts`、`src/pages/CodingPage/index.tsx`、`src/components/approvals/*`
- 修改：`api/server.py`（router include）、`api/agent_os_adapter.py`（注释）、`src/App.tsx`（路由）、`src/config/navRegistry.ts`、`src/config/agentIds.ts`、`src/store/sessions.ts`、`src/pages/SessionPage.tsx`、`src/components/session/SessionList.tsx`、`src/components/timeline/ExecutionTimeline.tsx` 等引用点
- 删除：后端三个 `case_studio_*` 路由文件；前端 `api/caseStudio.ts`、`stores/caseStudioStore.ts`、`components/caseStudio/`（含游戏面板与 Reports 面板）、`pages/CaseStudioPage/`
- **已消除**：前端断链（旧 URL 引用清零）、前端对已删业务端点的调用清零
- **已知功能缺口**：审批卡的「旧内容 → 新内容」对比源随 `/file`、`/kb/entry` 一并删除，写盘审批卡只剩新内容；替换卡以工具参数内的旧串→新串预览补位。要恢复覆盖 diff，通用替代方案（workspace 作用域的只读文件读取端点）留待作者决定
- **验证**：`tsc -b` 通过；`vitest run` 21 文件 195 用例全通过；`npm run build` 成功（产物含 `CodingPage` 分块）。前端 lint 存在 43 个既有错误（未涉及本次改动位置），非本次引入
- 剩余后端残留（本轮范围外）：`tools/case_studio_tools.py`、`core/game_repo.py`、`core/reports_parser.py`、`config.case_studio_reports_root`、`session_state.case_*`、若干内部变更号注释（含 `core/stream_adapter.py` 一处面向模型的文案）
