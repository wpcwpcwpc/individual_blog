# Design

## Context

素材来自上一变更的残留复核：Agent 层已通用化，但工坊 API 面分三类——纯游戏通道、通用审批底座、底座里混装的业务端点。三类处置不同，需要分层而不是一刀切。

## Decisions

### D1 通用底座保留，改名去语义

审批 list/count/detail/resolve、SSE continue、pending-clarification 六件事与业务无关：它们是「任何挂阻塞审批工具的 Agent」的前端承载链路，新 CodingAgent 正在复用。因此保留实现，只把命名语义从业务侧摘出来（模块名、tag、日志前缀、注释）。

### D2 路由不带 path prefix

原实现 `APIRouter(prefix="/case-studio")` 把业务名焊进 URL。改成不带前缀、显式写全路径（`/approvals`、`/sessions/{id}/runs/{run_id}/continue`）。备选方案是 `prefix="/approvals"` 并把会话级端点搬进第二个 router——为两个端点多开一个 router 不值得，故不采用。

### D3 两个业务端点直接删，不做替换

`GET /file`（按业务落盘白名单读用例文件）与 `GET /kb/entry`（读业务知识库）是审批卡「旧值来源」，纯业务实现（白名单正则、业务集合名）。保留它们等于把白名单与业务集合名留在通用审批面里；就地通用化又需要为通用 Agent 定义新的路径语义（workspace 作用域）。

取舍：先删，承认写盘审批卡暂时失去「旧内容对比」。这属功能性退让而不是安全问题——审批卡仍带完整新内容与 purpose，人工仍可判。通用替代端点（workspace 作用域只读文件读取）留待作者决定是否要恢复对比能力。

### D4 前端同步方式：删面板、留卡片、重建页面

前端 99 处引用旧 URL，处置不是逐条改名而是按「能力是否还成立」重排：

- **删**：游戏控制面板、Reports 调试面板、脚本预览面板（三者的数据源端点已删），工坊页随之下线
- **留**：审批卡与澄清卡——它们是 HITL 的前端承载，与业务无关；搬到 `components/approvals/`，kind 语义改为通用五类
- **重建**：`pages/CodingPage`（路由 `/coding`）——单列对话流 + 待审批徽标，去掉右侧运维面板与拖拽分栏（没有面板可放了）

代价是失去工坊式「左对话 + 右运维」的对比布局。取舍理由：右侧三块面板全部依赖已删的业务端点，保留布局等于保留死 UI。

备选方案「整块下线工坊页（含审批卡）」被否：审批卡在通用会话页 timeline 也有承载（`ExecutionTimeline` 引用），但工作台页提供了过滤、入口与待办徽标，删掉会让审批体验退化。

### D5 游戏工具与状态字段不动

`tools/case_studio_tools.py`、`core/game_repo.py`、`core/reports_parser.py`、`config.case_studio_reports_root`、`session_state.case_*` 属游戏层，删除它们要连带清掉存量配置与状态字段的兼容处理，属独立变更。本轮只在被触碰的文件里清语义（server.py 的 include 与注释、adapter 注释、前端引用点）。

## Risks

- **对比能力缺口**：写盘审批卡失去旧内容来源（D3）；替换卡以工具参数内的旧串→新串预览补位，覆盖 diff 需作者决定是否补通用只读端点
- **前端 lint 基线**：仓库存在 43 个既有 lint 错误（未涉及本次改动位置），不是本次引入，但会掩盖新问题；本轮以 `tsc -b` + `vitest run` + `npm run build` 三项作为绿灯依据
- **后端未运行时核对**：本机无 Python 依赖环境，后端只做到解析级核对与残留检索；前端侧已由类型检查、195 个用例与生产构建覆盖
- **前端依赖目录**：为验证在镜像区安装了 `node_modules`（已 gitignore，属排除清单项）；构建产物 `dist/` 已删除
