# QA Agent System — 部署文档

## 目录

1. [系统要求](#系统要求)
2. [环境变量说明](#环境变量说明)
3. [快速启动（Docker Compose）](#快速启动)
4. [Milvus Collection 初始化](#milvus-collection-初始化)
5. [Skill 开发指南](#skill-开发指南)
6. [Agent 定义指南](#agent-定义指南)
7. [API 接口速查](#api-接口速查)
8. [权限系统说明](#权限系统说明)
9. [运维与监控](#运维与监控)

---

## 系统要求

| 组件 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.11+ | 运行时 |
| Docker | 24.0+ | 容器化部署 |
| Docker Compose | 2.20+ | 编排 |
| 内存 | 8 GB | Milvus 至少需要 4 GB |
| 磁盘 | 20 GB | 向量数据 + PostgreSQL 数据 |

---

## 环境变量说明

复制 `.env.example` 为 `.env` 后按需修改：

```bash
cp .env.example .env
```

### LLM 配置（必填）

走 OpenAI 兼容协议。默认指向 DeepSeek 官方 API（`https://platform.deepseek.com` 申请 key），
也可换成任何兼容 OpenAI chat-completions 的服务。

| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_BASE_URL` | LLM API 基础 URL | `https://api.deepseek.com` |
| `LLM_API_KEY` | API 密钥 | `sk-...` |
| `LLM_MODEL` | 使用的模型名 | `deepseek-flash` / `deepseek-v4-pro` |
| `EMBEDDING_MODEL` | 嵌入模型名（可选，供 Milvus 长期记忆用） | `text-embedding-3-small` |

> `EMBEDDING_BASE_URL` 会回退到 `LLM_BASE_URL`。聊天端点不提供 `/embeddings`：
> 启用 Milvus 长期记忆时必须单独配置 `EMBEDDING_BASE_URL`，否则记忆写入会失败。

### Milvus 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MILVUS_HOST` | `localhost` | Milvus 主机 |
| `MILVUS_PORT` | `19530` | Milvus 端口 |
| `MILVUS_ENABLED` | `false` | 是否启用长期记忆（默认关闭，便于无外部依赖启动） |

> 若 `MILVUS_ENABLED=false` 或 Milvus 不可达，系统自动降级为"无长期记忆"模式，所有功能仍正常运行。

### Storage 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STORAGE_BACKEND` | `mongo` | `mongo` / `sqlite` / `postgres` |
| `MONGO_URI` | `mongodb://localhost:27017/` | MongoDB 连接串（`storage_backend=mongo`） |
| `SQLITE_PATH` | `./data/qa_agent.db` | SQLite 文件路径（`storage_backend=sqlite`） |
| `STORAGE_DSN` | 无（必填于 postgres 模式） | PostgreSQL DSN（`storage_backend=postgres`）。刻意不设默认值——未配置时存储工厂直接报错，避免任何口令字面量随源码传播 |

### 认证配置（OAuth 2.0 / OIDC，可选）

登录走标准授权码流，**不绑定任何供应商**。三项 URL 与 `client_id` 任一为空时，`/auth/*` 登录路由返回 503，仅「已登录会话」可用。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OAUTH_AUTHORIZE_URL` | （空） | 浏览器重定向到的授权端点 |
| `OAUTH_TOKEN_URL` | （空） | 服务端用授权码换取 token 的端点 |
| `OAUTH_USERINFO_URL` | （空） | 返回用户资料的端点（需含 `email` 或 `sub`） |
| `OAUTH_CLIENT_ID` | （空） | 供应商分配的 client id |
| `OAUTH_CLIENT_SECRET` | （空） | client secret：仅服务端换 token 用，**不落 session** |
| `OAUTH_SCOPE` | `openid email profile` | 请求的 scope（空格分隔） |
| `OAUTH_PKCE_ENABLED` | `true` | 是否发送 PKCE `code_challenge`（S256）；不支持 PKCE 的供应商设为 `false` |

需在供应商侧注册回调地址：`http://<host>:<port>/api/auth/login_callback`。回调强制校验 `state`（CSRF）。

> 会话 cookie 只保存本系统用户标识（`email`）与显示名，**不保存**供应商的 access/refresh token（D12）。用户键取 userinfo 的 `email`；供应商不返回 email 时回退为 `<sub>@<userinfo 主机名>`。

### 权限与 HIL 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_PERMISSION_MODE` | `default` | `default` / `plan` / `bypass` |
| `INTERRUPT_TIMEOUT_MINUTES` | `30` | 人工审核等待超时（分钟） |
| `DEFAULT_MAX_TURNS` | `25` | Agent 最大轮次 |

### 应用服务配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_HOST` | `0.0.0.0` | 监听地址。**只认这个名字**——`SERVER_HOST` 会被静默忽略 |
| `API_PORT` | `8000` | 监听端口。同上，`SERVER_PORT` 无效 |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warning` |
| `DEBUG` | `false` | 开发模式（自动重载） |

### Skill / Agent 目录

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROJECT_SKILLS_DIR` | `./.claude/skills` | 项目级 Skill 目录 |
| `USER_SKILLS_DIR` | `~/.qa-agent/skills` | 用户级 Skill 目录 |
| （无环境变量） | `.claude/agents` | 自定义 Agent 目录，由 `agents/agent_loader.py` 的 `agents_dir` 参数决定 |
| `MCP_CONFIG_PATH` | （空） | MCP 服务器配置路径；留空 = 不加载任何 MCP 服务器（仅内置工具） |

---

## 快速启动

### 开发模式（本地）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境
cp .env.example .env
# 编辑 .env，至少填写 LLM_BASE_URL 和 LLM_API_KEY

# 3. 仅启动基础设施（按 .env 的 STORAGE_BACKEND 决定启哪一套）
docker compose up -d postgres milvus-standalone
#    注意：.env 默认 STORAGE_BACKEND=mongo，而 compose 不提供 mongo 服务。
#    走 postgres 需在 .env 显式设 STORAGE_BACKEND=postgres + STORAGE_DSN；
#    走 mongo 需自备 MongoDB 实例。

# 4. 启动服务（开发模式，自动重载）
DEBUG=true python main.py
```

### 生产模式（Docker Compose）

```bash
# 1. 配置环境变量
cp .env.example .env
# 必填: LLM_BASE_URL, LLM_API_KEY, SESSION_SECRET_KEY；
# 若 STORAGE_BACKEND=postgres（compose 默认）还需 STORAGE_DSN（无默认值）；
# compose 不含任何口令字面量 → POSTGRES_PASSWORD 与 STORAGE_DSN 均必填，
# 且两者口令必须一致，否则 compose 直接报错退出
# qa-agent 服务用 env_file 读 .env —— 文件缺失时 compose 直接报错，不会静默
# 用空值启动。需要登录则同时填 OAUTH_*（缺省时 /api/auth/* 一律返回 503）
# 并以 http://<host>:<port>/api/auth/login_callback 向供应商注册回调地址。

# 2. 一键启动所有服务
docker compose up -d

# 3. 查看日志
docker compose logs -f qa-agent

# 4. 健康检查
curl http://localhost:8000/api/health
```

### 等待服务就绪

```bash
# 等待所有服务通过健康检查（约 60 秒）
docker compose ps

# 预期所有服务状态为 healthy
```

---

## Milvus Collection 初始化

Collection 由 Agno 的 Knowledge + Milvus adapter 在**首次写入时自动创建**（`enable_dynamic_field=True`），无需手动建表或迁移脚本。

### Collection 说明

| Collection | 用途 | 写入时机 |
|-----------|------|---------|
| `qa_execution_log` | 逐次工具调用执行记录 | 每次工具调用结束 |
| `qa_knowledge` | 上下文阈值触发的 LLM 蒸馏知识 | 上下文超阈值或会话结束 |
| `qa_conclusions` | 人工确认的测试结论 | 用户确认结论时 |

### 历史 Collection（pre-Agno）

`qa_memory` / `qa_session` 属旧 schema 遗留（见 `memory/collections.py:LEGACY_COLLECTIONS`），仅在 `client.list_collections()` 仍报告它们时可见。它们不会被自动清理，也没有配套的清理命令——确认两者为空后手动 drop 即可，其他任何路径都不应删除它们。

---

## Skill 开发指南

Skill 是 Markdown 文件（`SKILL.md`），包含 YAML Frontmatter 和提示模板。

### 文件位置

```
.claude/
  skills/
    my-skill/
      SKILL.md        # 必须
      helper.py       # 可选（被 !`cmd` 调用）
```

### SKILL.md 格式

```markdown
---
name: my-skill
description: 简短描述，Agent 用于判断何时调用该 Skill
version: "1.0"
author: your-name
args:
  - name: target
    description: 测试目标模块名
    required: true
  - name: depth
    description: 分析深度（1-3）
    required: false
    default: "2"
tags:
  - qa
  - automation
---

# My Skill

你是一个专业 QA 工程师，正在分析 $target 模块。

分析深度: $depth

## 当前项目状态

!`git log --oneline -5`

## 任务

请执行以下步骤：
1. 分析 $target 的相关配置
2. 列出潜在问题
3. 给出测试建议
```

### 变量替换

| 语法 | 说明 |
|------|------|
| `$ARGUMENTS` | 全部原始参数（位置参数时使用） |
| `$arg_name` | 具名参数值 |
| `${CLAUDE_SKILL_DIR}` | 当前 Skill 目录绝对路径 |
| `` !`cmd` `` | 执行 Shell 命令并内联输出（5 秒超时） |

### 调用方式

通过 API 发送消息，Agent 会自动识别并调用 Skill：

```
执行 my-skill，目标模块 combat，深度 3
```

---

## Agent 定义指南

自定义 Agent 使用 `AGENT.md` 格式（同 Claude Code 兼容）。

### 文件位置

```
.claude/
  agents/
    my-agent/
      AGENT.md
```

### AGENT.md 格式

```markdown
---
name: my-agent
description: 该 Agent 的用途描述，供 Coordinator 选择成员时使用
version: "1.0"
tools:
  - file_read
  - grep
  - bash
permission_mode: default   # default / plan / bypass
model: slot:default    # 或具体模型名，可覆盖全局 LLM 设置
---

# My Agent Instructions

你是一个专业的测试工程师，专注于...

## 工作准则

1. 始终先分析再操作
2. 重要操作前等待人工确认
3. 测试结果必须记录
```

---

## API 接口速查

### 创建会话

```bash
curl -X POST http://localhost:8000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "general-agent",
    "mode": "normal",
    "game_version": "1.0.0",
    "module": "combat"
  }'
```

### 发送消息（触发 Agent 执行）

```bash
curl -X POST http://localhost:8000/api/sessions/{session_id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "请分析战斗模块伤害计算的配置表"}'
```

### WebSocket 接收事件流

```javascript
const ws = new WebSocket(`ws://localhost:8000/api/sessions/${sessionId}/stream/ws`);

ws.onmessage = (msg) => {
  const event = JSON.parse(msg.data);
  switch (event.event_type) {
    case "token":
      process.stdout.write(event.content);
      break;
    case "interrupt_request":
      console.log("需要人工审核:", event.payload);
      break;
    case "run_complete":
      console.log("执行完成:", event.final_response);
      break;
  }
};
```

### 提交人工审核

```bash
# 批准
curl -X POST http://localhost:8000/api/sessions/{session_id}/review \
  -H "Content-Type: application/json" \
  -d '{"action": "confirm"}'

# 拒绝（中止当前操作）
curl -X POST http://localhost:8000/api/sessions/{session_id}/review \
  -H "Content-Type: application/json" \
  -d '{"action": "cancel", "notes": "该命令风险过高"}'

# 修改内容后批准
curl -X POST http://localhost:8000/api/sessions/{session_id}/review \
  -H "Content-Type: application/json" \
  -d '{"action": "modify", "modified_content": "修改后的内容"}'
```

### 查询状态

```bash
curl http://localhost:8000/api/sessions/{session_id}/status
```

---

## 权限系统说明

| 级别 | 名称 | 触发条件 | 默认行为 |
|------|------|---------|---------|
| L1 | 只读 | file_read, grep, glob_search | 自动执行，无需确认 |
| L2 | 副作用 | file_edit, bash | 暂停等待人工审核 |
| L3 | 结果验收 | 每次 Agent 执行完成后 | 展示结果等待人工确认 |
| L4 | 危险操作 | 特定高风险命令 | 强制要求显式批准 |

### Permission Mode

- **`default`**（推荐）：L2+ 操作均暂停等待人工确认
- **`plan`**：先展示执行计划，需显式确认后才执行
- **`bypass`**：跳过所有 L2 确认，全自动执行

---

## 运维与监控

### 日志

```bash
# 实时日志
docker compose logs -f qa-agent

# 日志格式示例
2024-01-15 10:23:45 | INFO     | api.server — ✓ QA Agent System ready
2024-01-15 10:23:46 | INFO     | hooks.interrupt_manager — Interrupt requested (session=abc123, type=L2_TOOL)
```

### 健康检查端点

```bash
curl http://localhost:8000/api/health
# {"status": "ok", "milvus": "connected", "storage": "ok", "version": "0.1.0"}

# degraded 模式（Milvus 不可达但服务正常运行）
# {"status": "degraded", "milvus": "disconnected", "storage": "ok", ...}
#
# milvus 三态：connected / disconnected / disabled。MILVUS_ENABLED=false（默认）
# 时为 disabled —— 未启用不算降级，status 仍为 ok。
# storage 为 ok / error。
```

### 限流错误文本检测

**背景**：部分 OpenAI 兼容网关对 TPM 限流不返回标准 429 异常，而是 HTTP 200 + 自然语言错误文本（如 `please try again later due to token limit (TPM)`）。此时 Agno 会把错误文本当正常 assistant content yield，导致会话被静默标 `completed`，用户看到一段裸错误串，任务中断。

**两层防御机制**（`core/rate_limiter.py` + `core/stream_adapter.py`）：

1. **RateLimitedModel 内容层检测**：流式/非流式 LLM 调用中，对 chunk content / response content 做 ≥2 特征组合匹配（`token limit` / `tpm` / `please try again later` / `tokens per minute` / `tokenlimiterror`），命中则抛 `RuntimeError` 触发既有 TPM 重试循环（流式 5 次 + 超时 2 次），尽量让任务自愈
2. **stream_adapter 兜底层**：即使 RateLimitedModel 漏检，`stream_agent_events` 在 yield `run_complete` 前对累积的 `final_response` 做同特征检测，命中则改发 `run_error` + raise，让 `_run_agent_task` 走 `set_failed`

命中时的行为：Layer 1 命中 → 自动重试自愈；Layer 2 兜底 → 会话标 `failed` + `run_error` 事件 + 错误提示（不再把网关的错误文本当成 AI 回复展示）。

观察日志中 `[TPM-Content]` / `[TPM-Content-Fallback]` 前缀的命中记录，可确认特征串覆盖度。

### 清理过期会话

系统每 5 分钟做一次后台清理：回收超时的人工审核等待会话（`INTERRUPT_TIMEOUT_MINUTES`，默认 30 分钟），并释放已终态会话的事件缓存（终态后 10 分钟）。
手动触发：重启服务即可（会话状态由 `STORAGE_BACKEND` 指向的存储持久化）。

### 扩容说明

- **水平扩展**：由于使用 `asyncio.Event` 管理 HIL 状态，必须使用**单进程单实例**。
- **多实例部署**：需将 `asyncio.Event` 替换为 Redis 分布式锁（未来规划）。
- 推荐通过增加硬件垂直扩展单实例性能。
