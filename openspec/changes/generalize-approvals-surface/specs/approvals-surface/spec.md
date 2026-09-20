## ADDED Requirements

### Requirement: 审批路由 Agent 无关

审批与澄清路由 SHALL 不绑定任何具体 Agent 名：MUST NOT 使用业务前缀，URL 与日志前缀 MUST NOT 出现业务标识。

#### Scenario: 路由前缀

- **WHEN** 检查审批路由的注册方式
- **THEN** router 不带 path prefix，端点路径为 `/approvals` 系列与会话级的 `/sessions/{id}/runs/{run_id}/continue`、`/sessions/{id}/pending-clarification`

#### Scenario: 日志与注释

- **WHEN** 检查该模块的日志前缀与 docstring
- **THEN** 前缀为 `[approvals]`，注释与 docstring 不出现业务语义（脚本工坊 / 用例 / 知识库）

### Requirement: 端点集合

审批面 SHALL 提供：审批列表、pending 计数、单条详情、决议提交、决议后 SSE 续跑、会话待答澄清。MUST NOT 提供任何依赖业务仓库布局或业务数据源的端点。

#### Scenario: 通用端点存在

- **WHEN** 任一挂阻塞审批工具的 Agent 暂停
- **THEN** 前端可通过 `/approvals` 系列与 continue 端点完成「展示审批卡 → 决议 → 续跑」

#### Scenario: 业务端点缺席

- **WHEN** 检查端点集合
- **THEN** 不含按业务落盘白名单读用例文件、读业务知识库条目的端点

### Requirement: 会话级续跑语义保持

续跑端点 SHALL 保持既有语义：审批仍 pending → 403；澄清作答缺失或无法应用 → 409/422（防静默护栏）；并发会话 → 409；SSE 事件透传与终态落库不变。

#### Scenario: 审批未决议就续跑

- **WHEN** 请求 continue 而目标 run 仍有 pending 审批
- **THEN** 返回 403 且不启动续跑

#### Scenario: 澄清作答缺失

- **WHEN** run 存在未作答的澄清 requirement 而请求体未携带作答
- **THEN** 返回 409 并列出待答项，MUST NOT 以空值续跑

### Requirement: 前端消费面

前端 SHALL 只经 `/approvals` 系列与会话级续跑端点消费审批面；编码工作台页面 MUST NOT 调用已下线的业务端点（游戏生命周期、Reports、业务文件与知识条目读取）。

#### Scenario: 审批卡数据来源

- **WHEN** 前端渲染审批待办、提交决议、续跑或自愈澄清卡
- **THEN** 请求全部落在 `/approvals` 系列与 `/sessions/{id}/...` 上

#### Scenario: 工作台路由

- **WHEN** 用户从导航进入编码工作台
- **THEN** 路由为 `/coding`（含 `/coding/{session_id}`），旧业务路径不再注册

#### Scenario: 已删端点的调用残留

- **WHEN** 检索前端源码
- **THEN** 不含对已下线业务端点路径的调用，也不含被删组件/模块的 import
