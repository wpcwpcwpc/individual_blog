---
title: "实战案例与扩展指南"
summary: "基于 src/Tool.ts 的 buildTool 函数，一个完整工具需要实现："
publishedAt: 2026-09-30
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 24
seriesGroup: 工程支撑
source: "24-实战案例与扩展指南.md"
sourceSha256: 3bf59a2df2ef
---
> **导读**：本篇是 Claude Code CLI 深度拆解系列的收官之作。我们将基于前23篇的技术解析，提供可直接落地的实战案例和扩展指南，帮助开发者将理论知识转化为实际应用能力。

---

## 一、自定义工具开发

### 1.1 工具接口解析

基于 `src/Tool.ts` 的 `buildTool` 函数，一个完整工具需要实现：

```typescript
// 工具定义接口 (简化版)
interface ToolDef<Input, Output, Progress> {
  // 核心标识
  name: string                          // 工具唯一名称
  description: string                   // LLM 可见的描述
  inputSchema: ZodSchema<Input>         // 输入参数校验
  
  // 执行逻辑
  call: (
    input: Input,
    context: ToolUseContext,
    signal: AbortSignal
  ) => AsyncGenerator<Progress, Output>
  
  // UI 渲染
  renderToolUseMessage: (input: Input) => ReactNode
  renderToolResultMessage: (output: Output) => ReactNode
  
  // 权限控制
  checkPermissions?: (input: Input, ctx: ToolUseContext) => PermissionResult
  toAutoClassifierInput?: (input: Input) => string
  
  // 行为标记
  isEnabled?: () => boolean
  isConcurrencySafe?: (input: Input) => boolean
  isReadOnly?: (input: Input) => boolean
  isDestructive?: (input: Input) => boolean
}
```

### 1.2 示例：实现一个 HTTP 请求工具

```typescript
// src/tools/HttpRequestTool/HttpRequestTool.ts
import { z } from 'zod'
import { buildTool } from '../../Tool.js'
import type { ToolUseContext } from '../../Tool.js'

// 1. 定义输入 Schema
const inputSchema = z.object({
  method: z.enum(['GET', 'POST', 'PUT', 'DELETE']),
  url: z.string().url(),
  headers: z.record(z.string()).optional(),
  body: z.string().optional(),
  timeout: z.number().min(1000).max(30000).default(10000),
})

type Input = z.infer<typeof inputSchema>

// 2. 定义输出类型
interface Output {
  status: number
  headers: Record<string, string>
  body: string
  elapsed: number
}

// 3. 定义进度消息
interface Progress {
  phase: 'connecting' | 'sending' | 'receiving'
  bytesTransferred?: number
}

// 4. 构建工具
export const HttpRequestTool = buildTool({
  name: 'http_request',
  description: `发送 HTTP 请求到指定 URL。

用途:
- 调用 REST API
- 获取网页内容
- 测试服务端点

限制:
- 最大响应体 1MB
- 超时时间 30s
- 不支持流式响应`,

  inputSchema,

  // 权限检查 - 外网请求需要确认
  async checkPermissions(input: Input, ctx: ToolUseContext) {
    const url = new URL(input.url)
    
    // 本地请求自动允许
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') {
      return { behavior: 'allow' as const, updatedInput: input }
    }
    
    // 外网请求需要权限
    return {
      behavior: 'ask' as const,
      message: `允许访问 ${url.origin}？`,
      updatedInput: input,
    }
  },

  // 为自动分类器提供输入摘要
  toAutoClassifierInput(input: Input): string {
    return `${input.method} ${input.url}`
  },

  // 标记为只读（GET 请求）或写入
  isReadOnly(input: Input): boolean {
    return input.method === 'GET'
  },

  // 并发安全（无状态）
  isConcurrencySafe(): boolean {
    return true
  },

  // 执行逻辑
  async *call(
    input: Input,
    _context: ToolUseContext,
    signal: AbortSignal
  ): AsyncGenerator<Progress, Output> {
    const start = Date.now()
    
    // 发送进度
    yield { phase: 'connecting' }
    
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), input.timeout)
    
    // 合并外部信号
    signal.addEventListener('abort', () => controller.abort())
    
    try {
      yield { phase: 'sending' }
      
      const response = await fetch(input.url, {
        method: input.method,
        headers: input.headers,
        body: input.body,
        signal: controller.signal,
      })
      
      yield { phase: 'receiving' }
      
      const body = await response.text()
      
      // 限制响应体大小
      const truncatedBody = body.length > 1_000_000
        ? body.slice(0, 1_000_000) + '\n[截断：超过 1MB 限制]'
        : body
      
      return {
        status: response.status,
        headers: Object.fromEntries(response.headers),
        body: truncatedBody,
        elapsed: Date.now() - start,
      }
    } finally {
      clearTimeout(timeout)
    }
  },

  // UI 渲染
  renderToolUseMessage(input: Input) {
    return (
      <Box flexDirection="column">
        <Text color="cyan">{input.method}</Text>
        <Text dimColor> {input.url}</Text>
      </Box>
    )
  },

  renderToolResultMessage(output: Output) {
    const statusColor = output.status < 400 ? 'green' : 'red'
    return (
      <Box flexDirection="column">
        <Text color={statusColor}>状态: {output.status}</Text>
        <Text dimColor>耗时: {output.elapsed}ms</Text>
        <Text>{output.body.slice(0, 500)}</Text>
      </Box>
    )
  },
})
```

### 1.3 注册工具

```typescript
// src/tools.ts
import { HttpRequestTool } from './tools/HttpRequestTool/HttpRequestTool.js'

// 在 getAllBaseTools 中添加
export function getAllBaseTools(): Tools {
  return {
    // ... 现有工具
    [HttpRequestTool.name]: HttpRequestTool,
  }
}
```

---

## 二、MCP 服务器开发

### 2.1 MCP 协议核心概念

```
┌─────────────────────────────────────────────────────────┐
│                    MCP 架构                              │
├─────────────────────────────────────────────────────────┤
│                                                          │
│   Claude Code CLI                MCP Server              │
│   ┌───────────────┐             ┌───────────────┐       │
│   │               │   stdio/    │               │       │
│   │  MCP Client   │◄───sse───►│  Your Server  │       │
│   │               │   http      │               │       │
│   └───────────────┘             └───────────────┘       │
│         │                              │                 │
│         │  Tool Discovery              │  Capabilities   │
│         │  Tool Invocation             │  - Tools        │
│         │  Resource Access             │  - Resources    │
│         │  Prompt Templates            │  - Prompts      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 2.2 示例：数据库查询 MCP 服务器

```typescript
// mcp-database-server/src/index.ts
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ListResourcesRequestSchema,
  ReadResourceRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'
import Database from 'better-sqlite3'

// 1. 创建服务器实例
const server = new Server(
  {
    name: 'database-mcp',
    version: '1.0.0',
  },
  {
    capabilities: {
      tools: {},
      resources: {},
    },
  }
)

// 2. 数据库连接
const db = new Database(process.env.DB_PATH || ':memory:')

// 3. 注册工具列表
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: 'query',
        description: '执行 SQL 查询（只读）',
        inputSchema: {
          type: 'object',
          properties: {
            sql: {
              type: 'string',
              description: 'SELECT 查询语句',
            },
            params: {
              type: 'array',
              items: { type: 'string' },
              description: '参数化查询的参数',
            },
          },
          required: ['sql'],
        },
      },
      {
        name: 'execute',
        description: '执行 SQL 修改（INSERT/UPDATE/DELETE）',
        inputSchema: {
          type: 'object',
          properties: {
            sql: {
              type: 'string',
              description: 'SQL 修改语句',
            },
            params: {
              type: 'array',
              items: { type: 'string' },
            },
          },
          required: ['sql'],
        },
      },
      {
        name: 'schema',
        description: '获取表结构',
        inputSchema: {
          type: 'object',
          properties: {
            table: {
              type: 'string',
              description: '表名',
            },
          },
          required: ['table'],
        },
      },
    ],
  }
})

// 4. 实现工具调用
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params

  try {
    switch (name) {
      case 'query': {
        // 安全检查：只允许 SELECT
        if (!/^\s*SELECT/i.test(args.sql)) {
          throw new Error('query 工具只支持 SELECT 语句')
        }
        const rows = db.prepare(args.sql).all(...(args.params || []))
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(rows, null, 2),
            },
          ],
        }
      }

      case 'execute': {
        const result = db.prepare(args.sql).run(...(args.params || []))
        return {
          content: [
            {
              type: 'text',
              text: `影响行数: ${result.changes}`,
            },
          ],
        }
      }

      case 'schema': {
        const schema = db
          .prepare(
            `SELECT sql FROM sqlite_master WHERE type='table' AND name=?`
          )
          .get(args.table)
        return {
          content: [
            {
              type: 'text',
              text: schema?.sql || '表不存在',
            },
          ],
        }
      }

      default:
        throw new Error(`未知工具: ${name}`)
    }
  } catch (error) {
    return {
      content: [
        {
          type: 'text',
          text: `错误: ${error.message}`,
        },
      ],
      isError: true,
    }
  }
})

// 5. 注册资源（可选）
server.setRequestHandler(ListResourcesRequestSchema, async () => {
  const tables = db
    .prepare(`SELECT name FROM sqlite_master WHERE type='table'`)
    .all()
  
  return {
    resources: tables.map((t) => ({
      uri: `db://tables/${t.name}`,
      name: t.name,
      description: `表: ${t.name}`,
      mimeType: 'application/json',
    })),
  }
})

server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
  const uri = new URL(request.params.uri)
  const tableName = uri.pathname.replace('/tables/', '')
  
  const rows = db.prepare(`SELECT * FROM ${tableName} LIMIT 100`).all()
  
  return {
    contents: [
      {
        uri: request.params.uri,
        mimeType: 'application/json',
        text: JSON.stringify(rows, null, 2),
      },
    ],
  }
})

// 6. 启动服务器
async function main() {
  const transport = new StdioServerTransport()
  await server.connect(transport)
  console.error('Database MCP Server 已启动')
}

main().catch(console.error)
```

### 2.3 配置 MCP 服务器

```json
// ~/.claude/settings.json
{
  "mcpServers": {
    "database": {
      "command": "node",
      "args": ["/path/to/mcp-database-server/dist/index.js"],
      "env": {
        "DB_PATH": "/path/to/database.sqlite"
      }
    }
  }
}
```

```json
// 项目级配置 .claude/settings.local.json
{
  "mcpServers": {
    "project-db": {
      "command": "npx",
      "args": ["-y", "@your-org/mcp-database-server"],
      "env": {
        "DB_PATH": "./data/project.db"
      }
    }
  }
}
```

---

## 三、Hook 系统扩展

### 3.1 Hook 事件类型

基于 `src/utils/hooks.ts` 的事件系统：

| Hook 事件 | 触发时机 | 输入数据 |
|-----------|---------|---------|
| `SessionStart` | 会话开始 | `{ sessionId, cwd, mode }` |
| `SessionEnd` | 会话结束 | `{ sessionId, duration, exitReason }` |
| `PreToolUse` | 工具执行前 | `{ toolName, input }` |
| `PostToolUse` | 工具执行后 | `{ toolName, input, output }` |
| `PostToolUseFailure` | 工具失败后 | `{ toolName, input, error }` |
| `PreCompact` | 压缩前 | `{ tokenCount, messages }` |
| `PostCompact` | 压缩后 | `{ beforeTokens, afterTokens }` |
| `Notification` | 通知事件 | `{ title, body }` |
| `Stop` | 用户中断 | `{ reason }` |
| `CwdChanged` | 目录切换 | `{ oldCwd, newCwd }` |
| `FileChanged` | 文件变更 | `{ path, changeType }` |
| `UserPromptSubmit` | 用户提交 | `{ prompt }` |
| `PermissionRequest` | 权限请求 | `{ toolName, action }` |

### 3.2 示例：Git 自动提交 Hook

```json
// .claude/settings.local.json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": {
          "toolName": "file_write|file_edit"
        },
        "hooks": [
          {
            "type": "shell",
            "command": "bash -c 'git add -A && git commit -m \"Auto-commit by Claude\" --allow-empty || true'"
          }
        ]
      }
    ]
  }
}
```

### 3.3 示例：工具审计日志 Hook

```bash
#!/bin/bash
# hooks/audit-log.sh

# 从 stdin 读取 JSON 输入
INPUT=$(cat)

# 提取关键字段
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName')
TIMESTAMP=$(date -Iseconds)
SESSION_ID=$(echo "$INPUT" | jq -r '.sessionId')

# 写入审计日志
echo "{\"timestamp\":\"$TIMESTAMP\",\"tool\":\"$TOOL_NAME\",\"session\":\"$SESSION_ID\"}" >> ~/.claude/audit.jsonl

# 输出空 JSON（表示无修改）
echo '{}'
```

```json
// .claude/settings.json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": {},
        "hooks": [
          {
            "type": "shell",
            "command": "bash ~/.claude/hooks/audit-log.sh"
          }
        ]
      }
    ]
  }
}
```

### 3.4 示例：敏感操作通知 Hook

```typescript
// hooks/notify-sensitive.ts
import { readFileSync } from 'fs'

interface PreToolUseInput {
  toolName: string
  input: Record<string, unknown>
  sessionId: string
}

const SENSITIVE_TOOLS = ['bash', 'file_write', 'file_edit']
const SENSITIVE_PATHS = ['/etc/', '/usr/', '~/.ssh/']

const input: PreToolUseInput = JSON.parse(
  readFileSync('/dev/stdin', 'utf-8')
)

// 检查敏感操作
if (SENSITIVE_TOOLS.includes(input.toolName)) {
  const path = input.input.path as string || input.input.command as string || ''
  
  if (SENSITIVE_PATHS.some(p => path.includes(p))) {
    // 发送通知（macOS）
    const { execSync } = require('child_process')
    execSync(`osascript -e 'display notification "工具: ${input.toolName}\n路径: ${path}" with title "Claude 敏感操作"'`)
    
    // 或通过 webhook
    fetch('https://your-webhook.com/notify', {
      method: 'POST',
      body: JSON.stringify({
        tool: input.toolName,
        path,
        session: input.sessionId,
      }),
    })
  }
}

// 允许继续执行
console.log('{}')
```

---

## 四、技能包（Skills）开发

### 4.1 技能包结构

```
skills/
└── my-skill/
    ├── skill.md           # 主技能定义
    ├── prompts/
    │   ├── base.md        # 基础提示词
    │   └── advanced.md    # 高级提示词
    └── examples/
        └── example1.md    # 使用示例
```

### 4.2 示例：代码审查技能

```markdown
<!-- skills/code-review/skill.md -->
---
name: code-review
description: 系统化的代码审查技能
version: 1.0.0
triggers:
  - "review"
  - "code review"
  - "审查代码"
tools:
  - file_read
  - grep
  - glob
---

# 代码审查技能

## 审查流程

当用户请求代码审查时，按以下步骤执行：

### 1. 确定审查范围

```
如果用户指定了文件：
  → 直接读取指定文件
如果用户指定了目录：
  → glob 搜索相关文件
如果用户说"最近的更改"：
  → 使用 git diff 获取变更
```

### 2. 分析维度

对每个文件从以下维度分析：

| 维度 | 检查点 |
|------|--------|
| **正确性** | 逻辑错误、边界条件、空值处理 |
| **安全性** | 注入风险、敏感数据、认证授权 |
| **性能** | 复杂度、内存泄漏、N+1 查询 |
| **可维护性** | 命名、注释、模块化、DRY |
| **测试** | 覆盖率、边界用例、Mock 合理性 |

### 3. 输出格式

```markdown
## 代码审查报告

### 📁 文件: {filename}

#### ✅ 优点
- ...

#### ⚠️ 建议改进
- **[严重程度]** 问题描述
  - 位置: 行 X-Y
  - 原因: ...
  - 建议: ...

#### 🔧 重构建议
- ...

### 📊 总结
- 问题总数: X
- 严重: X | 中等: X | 轻微: X
```

## 审查规则

### 安全规则
- 检测硬编码的密钥/密码
- 检测 SQL 注入风险
- 检测 XSS 风险
- 检测不安全的 eval/exec

### 性能规则
- 循环中的 await
- 未优化的正则表达式
- 大对象的深拷贝
- 重复的数据库查询

### 代码质量规则
- 函数超过 50 行
- 嵌套超过 3 层
- 未使用的变量/导入
- 魔法数字
```

### 4.3 注册技能

```json
// .claude/settings.json
{
  "skills": [
    {
      "path": "./skills/code-review",
      "enabled": true
    }
  ]
}
```

---

## 五、企业集成模式

### 5.1 CI/CD 集成

```yaml
# .github/workflows/claude-review.yml
name: Claude Code Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Setup Claude
        run: npm install -g @anthropic-ai/claude-code

      - name: Run Review
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          # 获取变更文件
          CHANGED_FILES=$(git diff --name-only origin/main...HEAD | grep -E '\.(ts|js|tsx|jsx)$')
          
          # 运行审查
          claude -p "请审查以下文件的代码变更：$CHANGED_FILES" \
            --output-format json \
            > review.json

      - name: Post Review Comment
        uses: actions/github-script@v7
        with:
          script: |
            const review = require('./review.json')
            await github.rest.pulls.createReview({
              owner: context.repo.owner,
              repo: context.repo.repo,
              pull_number: context.issue.number,
              body: review.summary,
              event: 'COMMENT'
            })
```

### 5.2 Slack 集成

```typescript
// integrations/slack-bot.ts
import { App } from '@slack/bolt'
import { spawn } from 'child_process'

const app = new App({
  token: process.env.SLACK_BOT_TOKEN,
  signingSecret: process.env.SLACK_SIGNING_SECRET,
})

app.message(/^claude (.+)$/i, async ({ message, say, context }) => {
  const prompt = context.matches[1]
  
  // 运行 Claude
  const claude = spawn('claude', ['-p', prompt, '--output-format', 'json'], {
    env: {
      ...process.env,
      ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
    },
  })
  
  let output = ''
  claude.stdout.on('data', (data) => output += data)
  
  claude.on('close', async (code) => {
    if (code === 0) {
      const result = JSON.parse(output)
      await say({
        thread_ts: message.ts,
        text: result.response,
      })
    } else {
      await say({
        thread_ts: message.ts,
        text: '❌ Claude 执行出错',
      })
    }
  })
})

app.start(3000)
```

### 5.3 企业策略配置

```json
// /etc/claude/managed-settings.json (管理员配置)
{
  "permissions": {
    "deny": [
      { "tool": "bash", "command": "rm -rf /*" },
      { "tool": "file_write", "path": "/etc/**" },
      { "tool": "file_write", "path": "/usr/**" }
    ],
    "ask": [
      { "tool": "bash", "command": "sudo *" },
      { "tool": "http_request", "url": "http://*" }
    ]
  },
  "apiKeySource": "oauth_only",
  "telemetryLevel": "enabled",
  "allowedMcpServers": [
    "internal-db",
    "company-wiki"
  ]
}
```

---

## 六、性能优化实践

### 6.1 Token 优化策略

```typescript
// 示例：智能文件读取
async function smartFileRead(path: string, maxTokens: number = 2000) {
  const content = await fs.readFile(path, 'utf-8')
  const lines = content.split('\n')
  
  // 估算 token 数（粗略：4 字符 ≈ 1 token）
  const estimatedTokens = content.length / 4
  
  if (estimatedTokens <= maxTokens) {
    return content
  }
  
  // 策略 1：只返回关键部分
  const imports = lines.filter(l => l.includes('import ')).join('\n')
  const exports = lines.filter(l => l.includes('export ')).join('\n')
  const functions = extractFunctionSignatures(content)
  
  return `// 文件摘要（原文件 ${lines.length} 行）
  
// === 导入 ===
${imports}

// === 导出 ===
${exports}

// === 函数签名 ===
${functions}

// 使用 file_read 工具并指定 offset 和 limit 参数查看具体实现`
}
```

### 6.2 并发执行优化

```typescript
// 利用工具的 isConcurrencySafe 标记
const concurrencyPlan = tools.reduce((plan, tool) => {
  if (tool.isConcurrencySafe(input)) {
    plan.parallel.push(tool)
  } else {
    plan.sequential.push(tool)
  }
  return plan
}, { parallel: [], sequential: [] })

// 并行执行安全工具
const parallelResults = await Promise.all(
  concurrencyPlan.parallel.map(t => t.call(input))
)

// 顺序执行非安全工具
for (const tool of concurrencyPlan.sequential) {
  await tool.call(input)
}
```

### 6.3 缓存策略

```typescript
// 利用 memoizeWithTTL 模式
import { memoizeWithTTL } from './utils/memoize.js'

// 缓存 MCP 服务器的工具列表（5分钟）
const getCachedTools = memoizeWithTTL(
  async (serverName: string) => {
    const client = await getMcpClient(serverName)
    return client.listTools()
  },
  5 * 60 * 1000
)

// 缓存文件内容（带 LRU）
import { memoizeWithLRU } from './utils/memoize.js'

const getCachedFileContent = memoizeWithLRU(
  async (path: string) => fs.readFile(path, 'utf-8'),
  { maxSize: 100 } // 最多缓存 100 个文件
)
```

---

## 七、调试与诊断

### 7.1 调试模式启动

```bash
# 启用调试日志
claude --debug

# 启用特定类别
claude --debug=mcp,tools

# 排除特定类别
claude --debug=*,!api

# 启用性能追踪
claude --perfetto-trace

# 输出完整 prompt
claude --dump-prompts
```

### 7.2 MCP 服务器调试

```bash
# 独立测试 MCP 服务器
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | node your-mcp-server.js

# 使用 MCP Inspector
npx @modelcontextprotocol/inspector node your-mcp-server.js
```

### 7.3 会话诊断

```bash
# 运行诊断
claude /doctor

# 输出示例
╭─────────────────────────────────────╮
│        Claude Code Doctor           │
├─────────────────────────────────────┤
│ ✓ Node.js: 20.10.0                 │
│ ✓ npm: 10.2.3                      │
│ ✓ API Key: Valid                   │
│ ✓ MCP Servers: 2/2 healthy         │
│ ⚠ Git: Not in repository           │
│ ✓ Permissions: Configured          │
╰─────────────────────────────────────╯
```

---

## 八、常见问题与解决方案

### 8.1 MCP 服务器连接失败

```bash
# 症状：MCP 工具不可用
# 诊断步骤：

# 1. 检查服务器配置
cat ~/.claude/settings.json | jq '.mcpServers'

# 2. 手动测试服务器
node /path/to/mcp-server.js

# 3. 检查日志
tail -f ~/.claude/logs/mcp-*.log

# 4. 验证 JSON-RPC 通信
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' \
  | node /path/to/mcp-server.js
```

### 8.2 权限被拒绝

```bash
# 症状：工具执行被权限系统阻止

# 1. 检查权限规则
cat ~/.claude/settings.json | jq '.permissions'

# 2. 检查企业策略
cat /etc/claude/managed-settings.json 2>/dev/null

# 3. 添加允许规则
claude settings set permissions.allow '[{"tool":"bash","command":"npm *"}]'
```

### 8.3 性能问题

```bash
# 症状：响应缓慢或超时

# 1. 启用性能追踪
claude --perfetto-trace

# 2. 分析追踪文件
# 在 Chrome 中打开 chrome://tracing
# 加载 ~/.claude/traces/*.json

# 3. 检查 token 使用
claude --verbose 2>&1 | grep "token"

# 4. 优化配置
claude settings set compact.microcompactEnabled true
claude settings set compact.autocompactEnabled true
```

---

## 九、最佳实践总结

### 9.1 工具开发检查清单

- [ ] 定义清晰的 `inputSchema` 和 `description`
- [ ] 实现 `checkPermissions` 进行安全控制
- [ ] 实现 `toAutoClassifierInput` 支持自动分类
- [ ] 正确设置 `isReadOnly`、`isConcurrencySafe`、`isDestructive`
- [ ] 处理 `AbortSignal` 支持取消
- [ ] 使用 `yield` 报告进度
- [ ] 实现友好的 UI 渲染函数
- [ ] 添加错误处理和重试逻辑
- [ ] 编写单元测试

### 9.2 MCP 服务器检查清单

- [ ] 实现 `tools/list` 和 `tools/call` 处理器
- [ ] 提供清晰的工具描述和 inputSchema
- [ ] 正确处理错误并设置 `isError: true`
- [ ] 实现超时处理
- [ ] 添加健康检查端点
- [ ] 记录审计日志
- [ ] 编写集成测试

### 9.3 Hook 开发检查清单

- [ ] 使用正确的事件类型和 matcher
- [ ] 从 stdin 读取 JSON 输入
- [ ] 输出有效的 JSON 到 stdout
- [ ] 处理超时（默认 10 分钟）
- [ ] 避免阻塞主线程
- [ ] 记录执行日志

---

## 十、总结与展望

通过本系列 24 篇文档，我们完成了对 Claude Code CLI 的全面逆向分析：

### 知识体系

| 领域 | 核心概念 | 关键文件 |
|------|---------|---------|
| **架构** | QueryEngine、消息循环、Tool 接口 | `query.ts`, `Tool.ts` |
| **提示** | System Prompt、动态注入、上下文 | `prompt.ts`, `systemPrompt.ts` |
| **工具** | buildTool、权限、MCP 集成 | `tools/*.ts`, `mcp/*.ts` |
| **状态** | 全局状态、持久化、会话管理 | `bootstrap/state.ts` |
| **UI** | Ink 渲染、双缓冲、事件循环 | `screens/REPL.tsx` |
| **安全** | 沙箱、权限分类、策略系统 | `sandbox/`, `permissions/` |
| **扩展** | Hooks、技能、MCP 协议 | `hooks.ts`, `skills/` |

### 扩展能力

基于本系列文档，开发者现在可以：

1. **开发自定义工具** - 扩展 Claude 的能力边界
2. **构建 MCP 服务器** - 连接外部系统和数据源
3. **配置 Hook 系统** - 实现自动化工作流
4. **创建技能包** - 封装领域知识和最佳实践
5. **企业集成** - CI/CD、Slack、策略管理
6. **性能优化** - Token 管理、并发、缓存
7. **调试诊断** - 追踪、日志、问题排查

### 未来方向

- **多模态扩展** - 视频、音频处理能力
- **Agent 网络** - 多 Agent 协作模式
- **实时协作** - 多用户共享会话
- **云原生部署** - Kubernetes、Serverless 集成
- **AI 安全** - 更细粒度的行为控制

---

> **致谢**：感谢所有阅读本系列的开发者。希望这 24 篇文档能帮助你深入理解 Claude Code CLI 的架构精髓，并在实际项目中发挥价值。
>
> **系列完结** 🎉
