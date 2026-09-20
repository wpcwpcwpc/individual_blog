# QA Agent System

基于 [Agno](https://www.agno.com/) 框架的 AI-powered QA Agent 系统，面向 QA 团队。

**核心宗旨：AI 辅助人工，人工永远兜底测试结果。**

## 特性

- 🤖 **Agent 引擎**：基于 Agno Agent 的声明式工具调用循环，支持 Normal / Coordinator 两种模式
- 📝 **Skill 系统**：QA 工程师通过编写 `SKILL.md` 定义专项测试技能，热更新生效
- 🧠 **三层记忆**：短期（Agent.memory）+ 中期（Agent.storage）+ 长期（Milvus 向量库）
- 🔒 **四级权限**：L1 自动执行 / L2 执行前确认 / L3 结果验收 / L4 人工操作
- 🔌 **MCP 集成**：原生支持 MCP 工具（stdio / SSE 传输）
- 👥 **Coordinator 模式**：Agno Team(coordinate) 多 Worker 并行编排
- 🌐 **API 服务**：FastAPI REST + WebSocket 流式推送

## 项目结构

```
qa-agent/
├── core/                   # 核心引擎（Agent 工厂、配置、存储、模型）
│   ├── config.py           # Pydantic Settings 配置
│   ├── engine.py           # Agent 创建工厂函数
│   ├── models.py           # LLM 模型配置
│   ├── storage.py          # Storage 工厂（Sqlite/Pg）
│   ├── memory_setup.py     # Agno Memory 配置
│   └── session_state.py    # QASessionState 运行时状态
├── tools/                  # 工具定义
│   ├── base.py             # 工具元数据结构
│   ├── registry.py         # 全局工具注册表
│   ├── file_tools.py       # 文件读写工具
│   ├── bash_tool.py        # Shell 执行工具
│   └── search_tools.py     # 搜索工具（grep）
├── agents/                 # Agent 定义
│   ├── base.py             # Agent 工厂基类
│   ├── registry.py         # Agent 注册表
│   ├── agent_loader.py     # AGENT.md 热加载
│   └── builtin/            # 内置 Agent
│       ├── explore_agent.py
│       └── config_analyzer_agent.py
├── skills/                 # Skill 系统
│   ├── loader.py           # SKILL.md 目录扫描与解析
│   ├── expander.py         # Prompt 展开与变量替换
│   └── skill_tool.py       # SkillTool（Agno @tool）
├── memory/                 # Milvus 记忆层
│   ├── milvus_client.py    # Milvus 连接与降级
│   ├── collections.py      # Collection 定义与初始化
│   ├── embedder.py         # 文本嵌入
│   ├── writer.py           # 执行记录写入
│   ├── distiller.py        # 上下文蒸馏
│   ├── injector.py         # 历史上下文注入
│   └── conclusion_writer.py # 结论强制写入
├── hooks/                  # Agno Hook 系统
│   ├── permission_hook.py  # L2 权限 pre_hook
│   ├── execution_log_hook.py   # 执行记录 post_hook
│   ├── context_threshold_hook.py  # 阈值检查 post_hook
│   ├── result_verify_hook.py  # L3 结果验收 post_hook
│   └── interrupt_manager.py   # Interrupt 状态管理
├── coordinator/            # Coordinator 模式（Agno Team）
│   └── team_builder.py     # Team 创建工厂
├── mcp/                    # MCP 集成
│   └── loader.py           # MCPTools 加载器
├── api/                    # FastAPI 服务
│   ├── server.py           # FastAPI + AgentOS 入口
│   ├── schemas.py          # Pydantic 请求/响应模型
│   └── session_manager.py  # 会话生命周期管理
├── tests/                  # 测试
├── docker-compose.yml      # Milvus + PostgreSQL 基础设施
├── Dockerfile              # 应用容器构建
├── requirements.txt        # Python 依赖
├── pyproject.toml          # 项目配置
├── .env.example            # 环境变量模板
└── README.md               # 本文件
```

## 快速启动

### 1. 环境准备

```bash
# 克隆并进入项目
cd qa-agent

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM API Key 等必要配置
```

### 3. 启动基础设施（可选）

```bash
# 启动 Milvus + PostgreSQL（需要 Docker）
docker-compose up -d

# 如不启动 Milvus，系统将以"无长期记忆"降级模式运行
# 如使用 SQLite，无需启动 PostgreSQL
```

### 4. 启动服务

```bash
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 验证

```bash
curl http://localhost:8000/health
```

## Skill 开发指南

QA 工程师可以通过编写 `SKILL.md` 文件定义专项测试能力。

### Skill 目录

- **项目级**：`.claude/skills/<skill-name>/SKILL.md`
- **用户级**：`~/.qa-agent/skills/<skill-name>/SKILL.md`

### 变量替换规则

| 变量 | 说明 |
|---|---|
| `$ARGUMENTS` | 用户调用时传入的完整参数字符串 |
| `$scenario` / `$platform` | Frontmatter `args` 中定义的命名参数 |
| `${CLAUDE_SKILL_DIR}` | 该 Skill 目录的绝对路径 |
| `` !`command` `` | 内嵌 Shell 命令，在 Skill 目录下执行（超时 5 秒） |

## API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/sessions` | 创建 Agent 会话 |
| `POST` | `/sessions/{id}/messages` | 发送消息 |
| `WS` | `/sessions/{id}/stream` | WebSocket 流式事件 |
| `POST` | `/sessions/{id}/review` | 提交人工审核结果 |
| `GET` | `/sessions/{id}/status` | 查询会话状态 |
| `DELETE` | `/sessions/{id}` | 关闭会话 |
| `GET` | `/skills` | 列出可用 Skill |
| `GET` | `/agents` | 列出可用 Agent |
| `GET` | `/health` | 健康检查 |
| `GET` | `/config` | 配置摘要 |

### AgentOS 兼容接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/agents/{agent_id}` | Agent 元数据（AgentOS 兼容） |
| `POST` | `/agents/{agent_id}/runs` | 调用 Agent（SSE 或 JSON） |
| `GET` | `/workflows` | 列出所有已注册 Workflow（含 `qa-auto`） |
| `GET` | `/workflows/{workflow_id}` | Workflow 元数据（4 个 Step、`session_state` schema） |
| `POST` | `/workflows/{workflow_id}/runs` | 启动或续跑 Workflow（首次不传 `session_id`；续跑带上同一个 `session_id` 即可，message 会被 adapter 改写为 `[resume]`）。SSE 30s 静默自动补 `: keep-alive` 注释帧。 |

`qa-auto` Workflow 的完整设计、`task_status` 11 个枚举、断点续跑用法见
[`docs/25-QA-Auto-Workflow.md`](docs/25-QA-Auto-Workflow.md)。

## 权限体系

| 级别 | 操作类型 | 介入方式 |
|---|---|---|
| **L1** | 只读（文件读、搜索） | 自动执行 |
| **L2** | 有副作用（命令执行、文件写入） | 暂停 → 人工确认 → 执行 |
| **L3** | 测试结论产出 | 暂停 → 展示结果 → 人工 Pass/Fail |
| **L4** | Bug 提交、报告发布 | AI 建议 → 人工操作 |

## License

MIT
