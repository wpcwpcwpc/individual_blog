# QA Agent UI

> QA Console — 面向 QA 团队的 Agent 执行可视化控制台

## 功能概览

| 模块 | 说明 |
|---|---|
| **会话管理** | 创建/切换/恢复会话，支持 Direct / Coordinator 两种模式 |
| **执行时间轴** | 实时 SSE 流，展示 Token 流、工具调用卡片、Worker 并发流水线 |
| **中断审核面板** | L2（命令预览/修改 + Monaco 编辑器）& L3（结果验证 + Markdown 渲染） |
| **系统总览** | 健康状态、配置信息、模型槽位 & Agent 映射 |

## 技术栈

- **框架**: React 18 + TypeScript 5
- **构建**: Vite 8
- **样式**: Tailwind CSS v4
- **状态管理**: Zustand
- **编辑器**: Monaco Editor（动态加载）
- **Markdown**: react-markdown + remark-gfm

## 目录结构

```
qa-agent-ui/
├── src/
│   ├── api/           # HTTP & SSE 接口封装
│   ├── components/
│   │   ├── interrupt/ # L2PreviewPanel / L3ResultPanel / InterruptPanel
│   │   ├── session/   # SessionList / CreateSessionModal / MessageInputBar
│   │   ├── system/    # HealthCard / ConfigCard / ModelSlotsCard
│   │   └── timeline/  # ExecutionTimeline / TimelineItem / ToolCallCard
│   ├── hooks/         # useSSE / useSystemData
│   ├── pages/         # SessionPage / SystemPage
│   ├── store/         # sessions.ts (Zustand)
│   ├── types/         # api.ts / events.ts
│   └── utils/         # agentColors.ts / formatters.ts
├── public/
└── dist/              # 构建产物
```

## 快速开始

### 前置条件

- Node.js >= 18
- 本地已启动 `qa-agent` 后端（默认监听 `http://localhost:8000`）

### 安装依赖

```bash
cd qa-agent-ui
npm install
```

### 开发模式

```bash
npm run dev
```

默认访问地址：`http://localhost:5173`

API 代理已配置：所有 `/api/*` 请求将自动转发到 `http://localhost:8000`。

### 生产构建

```bash
npm run build
# 产物在 dist/
```

用任意静态文件服务器托管 `dist/` 目录即可，例如：

```bash
npx serve dist
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VITE_API_BASE` | `/api` | 后端 API 前缀（留空则使用代理） |

在 `.env.local` 中覆盖：

```env
VITE_API_BASE=http://localhost:8000/api
```

## SSE 事件类型

UI 订阅以下 SSE 命名事件：

| 事件 | 触发时机 |
|---|---|
| `run_started` | 任务启动 |
| `token` | Agent 输出 token（流式） |
| `tool_start` | 工具调用开始 |
| `tool_end` | 工具调用成功 |
| `tool_error` | 工具调用失败 |
| `interrupt_request` | 需要人工审核（L2/L3） |
| `run_complete` | 任务完成 |
| `run_error` | 任务异常终止 |
| `heartbeat` | 保活心跳（忽略） |

## 中断审核流程

```
后端发送 interrupt_request
        │
        ▼
UI 检测 interrupt_type
        │
   ┌────┴────┐
  L2        L3
命令预览   结果验证
  │            │
修改脚本    标记 pass/fail
  │            │
  └────┬────┘
       ▼
  提交 POST /sessions/{id}/interrupt/respond
       │
       ▼
  后端恢复执行
```

## 会话持久化

活跃会话 ID 保存于 `localStorage`（key: `qa_session_ids`）。页面刷新后自动通过 `GET /sessions/{id}/status` 恢复状态，并重连 SSE 流（若任务仍在运行）。

## 开发注意事项

- Monaco Editor 体积较大（~500KB gzip 前），已通过 `React.lazy` + `Suspense` 动态加载，首屏不受影响。
- Coordinator 模式下多 Worker 并发时，时间轴会按 `agent_name` 分色显示；颜色由 `agentColors.ts` 哈希确定性分配，刷新后保持一致。
- 修改 Zustand store 时，注意 `appendEvent` 中的 token 合并逻辑依赖 `agent_name` 一致性，确保后端字段命名不变。