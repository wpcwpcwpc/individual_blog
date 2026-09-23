---
title: "Agent记忆与状态管理"
summary: "Agent的\"记忆\"有三层——短期（对话历史）、中期（会话摘要）、长期（CLAUDE.md + Auto Memory），通过智能压缩在有限的上下文窗口中保持最佳工作状态。"
publishedAt: 2026-07-08
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 10
seriesGroup: Agent 核心系统
readingMinutes: 45
weight: 3
source: "10-Agent记忆与状态管理.md"
sourceSha256: 28b3f980dc1b
---
> **一句话理解**：Agent的"记忆"有三层——短期（对话历史）、中期（会话摘要）、长期（CLAUDE.md + Auto Memory），通过智能压缩在有限的上下文窗口中保持最佳工作状态。

> **阅读时长**：45分钟  
> **前置阅读**：[09-Agent系统架构](/claudecode/09-agent-system)  
> **重要程度**：⭐️⭐️⭐️（核心必读）  
> **核心价值**：理解Agent如何"记住"项目知识、管理对话上下文、持久化状态
> 
> **你将获得**：
> - 理解Agent的三层记忆架构
> - 掌握CLAUDE.md的层级加载机制
> - 学会Auto Memory的自动学习原理
> - 理解Autocompact的智能压缩策略
> - 掌握状态持久化和会话恢复

---

## 📖 本章导航

- [记忆系统概览](#记忆系统概览)
- [长期记忆：CLAUDE.md系统](#长期记忆claudemd系统)
- [长期记忆：Auto Memory](#长期记忆auto-memory)
- [短期记忆：对话上下文管理](#短期记忆对话上下文管理)
- [中期记忆：会话压缩](#中期记忆会话压缩)
- [运行时状态管理](#运行时状态管理)
- [会话持久化与恢复](#会话持久化与恢复)
- [Token计数与优化](#token计数与优化)
- [最佳实践](#最佳实践)

---

## 记忆系统概览

### Agent为什么需要"记忆"？

想象你是一个新来的员工，每天早上失忆：

| 问题 | 没有记忆系统 | 有记忆系统 |
|------|-------------|-----------|
| "项目用什么框架？" | 每天重新问 | 看 CLAUDE.md |
| "昨天讨论了什么？" | 完全不知道 | 看 Auto Memory |
| "之前犯过什么错？" | 重复犯错 | 看历史记录 |
| "对话太长记不住" | 忘记开头 | 自动压缩摘要 |

### 三层记忆架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent 记忆系统架构                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           长期记忆（跨会话持久化）                    │   │
│  │  ┌───────────────┐    ┌───────────────────────┐    │   │
│  │  │  CLAUDE.md    │    │     Auto Memory       │    │   │
│  │  │  项目/用户指令 │    │  自动学习的知识点      │    │   │
│  │  │  手动维护     │    │  AI自动生成           │    │   │
│  │  └───────────────┘    └───────────────────────┘    │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           中期记忆（会话级别）                        │   │
│  │  ┌───────────────┐    ┌───────────────────────┐    │   │
│  │  │ Session Memory│    │   Autocompact摘要     │    │   │
│  │  │  会话级摘要    │    │  压缩的历史对话        │    │   │
│  │  └───────────────┘    └───────────────────────┘    │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           短期记忆（当前对话）                        │   │
│  │  ┌───────────────────────────────────────────────┐  │   │
│  │  │        Messages Array（对话历史）               │  │   │
│  │  │  user → assistant → user → assistant → ...    │  │   │
│  │  │  ⚠️ 受上下文窗口限制（200K tokens）            │  │   │
│  │  └───────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 记忆类型对比

| 记忆类型 | 存储位置 | 持久性 | 更新方式 | 典型内容 |
|---------|---------|--------|---------|---------|
| **长期-CLAUDE.md** | 文件系统 | 永久 | 手动编辑 | 项目规范、代码风格 |
| **长期-Auto Memory** | `~/.claude/memory/` | 永久 | AI自动 | 学到的知识点 |
| **中期-Session** | 会话状态 | 会话内 | 自动压缩 | 对话摘要 |
| **短期-Messages** | 内存 | 单轮 | 实时更新 | 当前对话历史 |

---

## 长期记忆：CLAUDE.md系统

### CLAUDE.md是什么？

CLAUDE.md是Agent的"项目手册"——每次对话开始时自动加载，提供项目相关的持久化指令。

**类比**：
```
CLAUDE.md = 员工入职时收到的《项目指南》
├─ 告诉你项目用什么技术栈
├─ 告诉你代码风格规范
├─ 告诉你特殊注意事项
└─ 让你不用每次都问老员工
```

### 层级加载机制

**源码位置**：`restored-src/src/claudeMd.ts`

```typescript
// CLAUDE.md 加载优先级（从低到高合并）
async function loadClaudeMd(): Promise<string> {
  const layers = [
    // Layer 1: 用户全局配置（最低优先级）
    `~/.claude/CLAUDE.md`,
    
    // Layer 2: 项目根目录
    `${projectRoot}/CLAUDE.md`,
    
    // Layer 3: 项目.claude目录
    `${projectRoot}/.claude/CLAUDE.md`,
    
    // Layer 4: 当前目录的CLAUDE.local.md（最高优先级）
    `${cwd}/CLAUDE.local.md`,
  ]
  
  // 合并所有层级的内容
  return mergeClaudeMdLayers(layers)
}
```

**层级图示**：

```
优先级（低→高）：

~/.claude/CLAUDE.md          [用户级] 全局偏好
      ↓ 合并
/project/CLAUDE.md           [项目级] 项目规范
      ↓ 合并
/project/.claude/CLAUDE.md   [项目级] 详细指令
      ↓ 合并
/project/src/CLAUDE.local.md [目录级] 本地覆盖（不提交Git）
      ↓
最终的 CLAUDE.md 内容
```

### 推荐的CLAUDE.md结构

```markdown
# 项目名称

## 技术栈
- 后端：Node.js + Express + TypeScript
- 数据库：PostgreSQL + Prisma ORM
- 前端：React + Redux Toolkit

## 代码规范
- 使用 Prettier 格式化（配置见 .prettierrc）
- 使用 ESLint Airbnb 规则
- Commit message 遵循 Conventional Commits

## 测试要求
- 所有新功能必须有单元测试
- 使用 Jest 作为测试框架
- 覆盖率目标：80%

## 特殊说明
- API 响应格式统一使用 { success: boolean, data: any, error?: string }
- 不要修改 src/legacy/ 目录下的代码（历史遗留）
- 环境变量在 .env.example 中有说明

## 常用命令
- `npm run dev` - 启动开发服务器
- `npm test` - 运行测试
- `npm run build` - 构建生产版本
```

### omitClaudeMd优化

在文档12中我们讨论过，某些Agent会设置`omitClaudeMd: true`跳过加载：

```typescript
// Explore Agent：只搜索，不需要知道代码规范
omitClaudeMd: true

// Verification Agent：需要对照规范验证
omitClaudeMd: false
```

**节省的成本**：
- 假设CLAUDE.md有5000 tokens
- 10次Agent调用 × 5000 = 50K tokens
- Sonnet价格：50K × $3/M = $0.15
- 一天100次 = $1.5，一月 = $45

---

## 长期记忆：Auto Memory

### Auto Memory是什么？

Auto Memory是Agent的"学习笔记"——在对话过程中自动识别和保存有价值的知识点。

**类比**：
```
Auto Memory = 你工作时自动记录的备忘录
├─ "原来这个项目用的是 pnpm 不是 npm"
├─ "数据库连接字符串在 .env.local 里"
├─ "这个 bug 是因为缓存导致的"
└─ 下次遇到类似情况，自动想起来
```

### Auto Memory的工作原理

**源码位置**：`restored-src/src/memory/autoMemory.ts`

```typescript
// Auto Memory 自动学习流程
export async function processAutoMemory(
  conversation: Message[],
  context: ToolUseContext
): Promise<void> {
  // 1. 分析对话，识别有价值的知识点
  const insights = await extractInsights(conversation)
  
  // 2. 过滤已知的知识（避免重复）
  const newInsights = filterKnownInsights(insights)
  
  // 3. 持久化到内存文件
  for (const insight of newInsights) {
    await saveToMemory(insight)
  }
}

// 知识点提取（AI驱动）
async function extractInsights(conversation: Message[]): Promise<Insight[]> {
  // 使用LLM分析对话，提取可复用的知识
  const prompt = `
    分析以下对话，提取可以在未来复用的知识点：
    - 项目特定的配置或约定
    - 解决问题的方法
    - 用户的偏好
    - 重要的决策和原因
    
    ${formatConversation(conversation)}
  `
  return await llm.extract(prompt)
}
```

### Auto Memory存储结构

```
~/.claude/memory/
├── projects/
│   ├── my-app/
│   │   ├── learned_patterns.json    # 学到的代码模式
│   │   ├── user_preferences.json    # 用户偏好
│   │   └── common_issues.json       # 常见问题解决方案
│   └── another-project/
│       └── ...
└── global/
    ├── coding_style.json            # 全局代码风格偏好
    └── tool_preferences.json        # 工具使用偏好
```

### Auto Memory示例

```json
// ~/.claude/memory/projects/my-app/learned_patterns.json
{
  "patterns": [
    {
      "id": "auth-token-refresh",
      "learned_at": "2026-03-15T10:30:00Z",
      "context": "用户修复了token刷新的bug",
      "insight": "这个项目的token刷新逻辑在 src/auth/refreshToken.ts，使用 axios interceptor 实现",
      "confidence": 0.95
    },
    {
      "id": "test-database",
      "learned_at": "2026-03-16T14:20:00Z", 
      "context": "用户配置测试数据库",
      "insight": "测试时使用 SQLite 内存数据库（:memory:），通过 NODE_ENV=test 触发",
      "confidence": 0.88
    }
  ]
}
```

### Auto Memory与CLAUDE.md的区别

| 维度 | CLAUDE.md | Auto Memory |
|------|-----------|-------------|
| **更新方式** | 手动编辑 | AI自动学习 |
| **内容类型** | 规范性指令 | 学习到的知识 |
| **可见性** | 用户可直接编辑 | AI管理，用户可查看 |
| **优先级** | 更高（显式指令） | 较低（隐式知识） |
| **典型内容** | "使用Prettier格式化" | "上次用户喜欢简洁的注释" |

---

## 短期记忆：对话上下文管理

### 上下文窗口限制

Claude的上下文窗口是有限的（约200K tokens）：

```
┌─────────────────────────────────────────────────┐
│              上下文窗口 (200K tokens)            │
├─────────────────────────────────────────────────┤
│  System Prompt      │ ~5K tokens               │
│  CLAUDE.md          │ ~5K tokens               │
│  Auto Memory        │ ~2K tokens               │
│  工具定义           │ ~10K tokens              │
│  ─────────────────────────────────────────      │
│  可用于对话         │ ~178K tokens             │
│  ─────────────────────────────────────────      │
│  当前对话历史       │ ??? tokens (动态)        │
└─────────────────────────────────────────────────┘

问题：对话太长怎么办？
答案：Autocompact（自动压缩）
```

### 对话历史结构

```typescript
// 对话历史的基本结构
type Message = {
  role: 'user' | 'assistant' | 'system'
  content: string | ContentBlock[]
  // 可能包含工具调用
  tool_use?: ToolUseBlock[]
  tool_result?: ToolResultBlock[]
}

// 完整的对话可能很长
const conversation: Message[] = [
  { role: 'user', content: '帮我添加登录功能' },
  { role: 'assistant', content: '好的，让我先搜索相关文件...', tool_use: [...] },
  { role: 'user', content: '[tool_result: 找到5个文件]' },
  { role: 'assistant', content: '我来修改auth.ts...', tool_use: [...] },
  // ... 可能有几十甚至上百轮
]
```

### Microcompact：工具输出压缩

当工具输出太长时，使用Microcompact进行实时压缩：

**源码位置**：`restored-src/src/compact/microcompact.ts`

```typescript
// Microcompact 策略
export async function microcompact(
  toolResult: string,
  maxTokens: number = 2000
): Promise<string> {
  const currentTokens = countTokens(toolResult)
  
  if (currentTokens <= maxTokens) {
    return toolResult  // 不需要压缩
  }
  
  // 使用LLM压缩
  const compressed = await llm.compress({
    content: toolResult,
    instruction: `
      保留关键信息，压缩到${maxTokens} tokens以内：
      - 保留文件路径
      - 保留错误信息
      - 保留关键代码片段
      - 删除重复内容
      - 用摘要替代详细日志
    `
  })
  
  return compressed
}
```

**Microcompact示例**：

```
原始工具输出（5000 tokens）：
────────────────────────────────
> npm test
Running 156 tests...
✓ auth/login.test.ts (15 tests)
  ✓ should login with valid credentials
  ✓ should reject invalid password
  ... (150+ 行测试输出)
✓ All tests passed

Microcompact压缩后（500 tokens）：
────────────────────────────────
npm test 执行完成：
- 总计 156 个测试
- 全部通过 ✓
- 关键模块：auth (15), user (23), product (45)
- 耗时：12.3s
```

---

## 中期记忆：会话压缩

### Autocompact：自动上下文压缩

当对话历史接近上下文限制时，Autocompact自动触发：

**源码位置**：`restored-src/src/compact/autocompact.ts`

```typescript
// Autocompact 触发条件
const AUTOCOMPACT_THRESHOLD = 0.75  // 75%容量时触发

export async function checkAndAutocompact(
  messages: Message[],
  contextLimit: number
): Promise<Message[]> {
  const currentUsage = countTokens(messages)
  const usageRatio = currentUsage / contextLimit
  
  if (usageRatio < AUTOCOMPACT_THRESHOLD) {
    return messages  // 不需要压缩
  }
  
  // 触发压缩
  return await performAutocompact(messages)
}
```

### Autocompact策略

```
┌─────────────────────────────────────────────────────────────┐
│                    Autocompact 策略图示                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  压缩前的对话历史：                                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Turn 1-10   │ Turn 11-20  │ Turn 21-30  │ Turn 31+  │   │
│  │   (旧)      │   (较旧)    │   (较新)    │   (新)    │   │
│  │  20K tokens │  20K tokens │  20K tokens │ 40K tokens│   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Autocompact 执行后：                                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 摘要    │ Turn 21-30  │ Turn 31+ │     空闲空间     │   │
│  │ (压缩)  │  (保留)     │  (保留)  │                  │   │
│  │ 5K tok  │ 20K tokens  │ 40K tok  │    35K tokens    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  策略：压缩最旧的部分，保留最近的完整内容                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Autocompact实现

```typescript
async function performAutocompact(messages: Message[]): Promise<Message[]> {
  // 1. 确定压缩范围（前半部分）
  const compactRatio = 0.5  // 压缩前50%的内容
  const splitIndex = Math.floor(messages.length * compactRatio)
  
  const toCompress = messages.slice(0, splitIndex)
  const toKeep = messages.slice(splitIndex)
  
  // 2. 生成摘要
  const summary = await generateSummary(toCompress)
  
  // 3. 构建新的消息数组
  const compactedMessages: Message[] = [
    {
      role: 'system',
      content: `[对话历史摘要]\n${summary}\n\n[以下是最近的对话]`
    },
    ...toKeep
  ]
  
  return compactedMessages
}

async function generateSummary(messages: Message[]): Promise<string> {
  return await llm.summarize({
    content: messages,
    instruction: `
      生成这段对话的摘要，保留：
      1. 讨论的主要任务
      2. 做出的关键决策
      3. 修改的文件
      4. 遇到的问题和解决方案
      5. 用户的重要偏好
      
      格式：简洁的要点列表
    `
  })
}
```

### Autocompact摘要示例

```markdown
[对话历史摘要 - Turn 1-20]

## 任务背景
用户需要为电商应用添加购物车功能。

## 已完成的工作
1. 创建了 Cart 模型（src/models/Cart.ts）
2. 实现了购物车 API（src/routes/cart.ts）
   - POST /api/cart/add
   - DELETE /api/cart/remove
   - GET /api/cart

## 关键决策
- 使用 Redis 缓存购物车数据（性能考虑）
- 购物车有效期：7天

## 遇到的问题
- Redis 连接配置错误 → 已修复（.env.local 中添加 REDIS_URL）

## 用户偏好
- 喜欢详细的注释
- 倾向使用 async/await 而非 Promise.then

[以下是最近的对话]
```

---

## 运行时状态管理

### AppState架构

Claude Code使用**单一状态树**管理所有运行时状态：

**源码位置**：`restored-src/src/state/appState.ts`

```typescript
// AppState 完整结构
interface AppState {
  // 用户设置
  settings: {
    theme: 'light' | 'dark'
    model: string
    maxTokens: number
    autocompactEnabled: boolean
  }
  
  // MCP 连接状态
  mcp: {
    servers: Map<string, MCPServerState>
    pendingConnections: string[]
  }
  
  // 工具权限上下文
  toolPermissionCtx: {
    sessionPermissions: Map<string, boolean>
    projectPermissions: Map<string, boolean>
  }
  
  // 任务状态（Agent/Worker）
  tasks: {
    active: Map<string, TaskState>
    completed: TaskState[]
  }
  
  // 文件历史
  fileHistory: {
    opened: string[]
    modified: string[]
    lastAccessed: Map<string, Date>
  }
  
  // 通知队列
  notifications: Notification[]
  
  // 投机执行状态
  speculation: {
    enabled: boolean
    pendingResults: Map<string, any>
  }
}
```

### Store实现

```typescript
// 轻量级Store实现（类似Redux但更简单）
type Listener = () => void
type OnChange<T> = (args: { newState: T; oldState: T }) => void

export type Store<T> = {
  getState: () => T
  setState: (updater: (prev: T) => T) => void
  subscribe: (listener: Listener) => () => void
}

export function createStore<T>(
  initialState: T,
  onChange?: OnChange<T>
): Store<T> {
  let state = initialState
  const listeners = new Set<Listener>()

  return {
    getState: () => state,
    
    setState: (updater) => {
      const oldState = state
      state = updater(state)
      
      // 通知变更
      if (onChange) {
        onChange({ newState: state, oldState })
      }
      
      // 通知订阅者
      listeners.forEach(listener => listener())
    },
    
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    }
  }
}
```

### 配置持久化

```
配置文件层级（优先级从低到高）：

~/.claude/                    [用户级配置]
├── settings.json             # 全局设置
├── permissions.json          # 全局权限
└── memory/                   # Auto Memory

/project/.claude/             [项目级配置]
├── settings.json             # 项目设置（覆盖全局）
├── permissions.json          # 项目权限
└── CLAUDE.md                 # 项目指令

最终配置 = merge(全局, 项目)
```

---

## 会话持久化与恢复

### 会话状态保存

每个会话的完整状态保存在`subagents/`目录：

```
.claude/subagents/
├── session-abc123/
│   ├── transcript.json       # 对话历史
│   ├── state.json           # 会话状态快照
│   └── tools/               # 工具执行记录
│       ├── tool-001.json
│       └── tool-002.json
└── session-def456/
    └── ...
```

### Sidechain Transcript（子链记录）

**源码位置**：`restored-src/src/agents/sidechainTranscript.ts`

```typescript
// 子Agent的对话记录独立保存
export async function saveSidechainTranscript(
  agentId: string,
  messages: Message[]
): Promise<void> {
  const transcriptPath = `.claude/subagents/${agentId}/transcript.json`
  
  await fs.writeFile(transcriptPath, JSON.stringify({
    agentId,
    savedAt: new Date().toISOString(),
    messages,
    metadata: {
      totalTokens: countTokens(messages),
      turnCount: messages.length
    }
  }, null, 2))
}
```

### 会话恢复（Resume）

```typescript
// 恢复中断的会话
export async function resumeSession(sessionId: string): Promise<ConversationState> {
  const transcriptPath = `.claude/subagents/${sessionId}/transcript.json`
  
  if (!await fs.exists(transcriptPath)) {
    throw new Error(`Session ${sessionId} not found`)
  }
  
  const transcript = await fs.readJSON(transcriptPath)
  
  return {
    messages: transcript.messages,
    state: await loadSessionState(sessionId),
    // 恢复时可能需要重新压缩（如果太长）
    needsCompact: transcript.metadata.totalTokens > COMPACT_THRESHOLD
  }
}
```

---

## Token计数与优化

### Token计数实现

**源码位置**：`restored-src/src/utils/tokenCount.ts`

```typescript
// Token计数（使用tiktoken库）
import { encoding_for_model } from 'tiktoken'

const encoder = encoding_for_model('claude-3-sonnet')

export function countTokens(content: string | Message[]): number {
  if (typeof content === 'string') {
    return encoder.encode(content).length
  }
  
  // 对消息数组递归计数
  return content.reduce((total, msg) => {
    return total + countTokens(formatMessage(msg))
  }, 0)
}

// 快速估算（不使用完整编码，用于频繁检查）
export function estimateTokens(content: string): number {
  // 粗略估算：英文约4字符/token，中文约1.5字符/token
  const englishChars = content.replace(/[^\x00-\x7F]/g, '').length
  const otherChars = content.length - englishChars
  
  return Math.ceil(englishChars / 4 + otherChars / 1.5)
}
```

### Token优化策略

```
┌─────────────────────────────────────────────────────────────┐
│                    Token 优化策略                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Microcompact（工具输出压缩）                             │
│     ├─ 触发：单次工具输出 > 2K tokens                        │
│     └─ 效果：压缩至原来的10-20%                              │
│                                                             │
│  2. Autocompact（对话历史压缩）                              │
│     ├─ 触发：总使用量 > 75%上下文窗口                        │
│     └─ 效果：压缩前50%历史为摘要                             │
│                                                             │
│  3. omitClaudeMd（跳过CLAUDE.md）                           │
│     ├─ 适用：只读Agent（Explore、Plan）                      │
│     └─ 效果：节省~5K tokens/次                               │
│                                                             │
│  4. Context Forking（上下文分叉）                            │
│     ├─ 适用：子Agent只需部分上下文                           │
│     └─ 效果：避免传递不相关历史                              │
│                                                             │
│  5. Prompt Caching（提示缓存）                               │
│     ├─ 适用：System Prompt + CLAUDE.md                      │
│     └─ 效果：重复内容节省90%成本                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 最佳实践

### 实践1：编写高效的CLAUDE.md

**❌ 坏的CLAUDE.md**：
```markdown
# 项目

这是一个很复杂的项目...（500字的历史介绍）

我们在2020年开始这个项目...（更多历史）

代码在src目录下...（显而易见的信息）
```

**✅ 好的CLAUDE.md**：
```markdown
# 项目名称（电商后台）

## 技术栈
- Node.js 18 + TypeScript
- PostgreSQL + Prisma
- Redis（缓存）

## 关键约定
- API响应格式：`{ success, data, error }`
- 认证：JWT，刷新token在 /auth/refresh
- 不要改 src/legacy/（历史代码）

## 常用命令
- `pnpm dev` - 开发
- `pnpm test` - 测试
```

### 实践2：利用Auto Memory

**让Agent主动学习**：
```
用户："记住，这个项目的数据库迁移用 prisma migrate dev"

Agent：[自动记录到Auto Memory]
下次用户问"怎么迁移数据库"时，直接知道答案
```

**定期Review Auto Memory**：
```bash
# 查看项目的Auto Memory
cat ~/.claude/memory/projects/my-app/learned_patterns.json

# 删除过时的记忆
# 编辑JSON文件，移除不再有效的条目
```

### 实践3：控制上下文使用

**监控Token使用**：
- Claude Code会在状态栏显示当前token使用量
- 关注使用率，超过70%时考虑手动compact

**主动触发Compact**：
```
用户："压缩一下对话历史"
或
用户："/compact"
```

### 实践4：合理使用会话恢复

**什么时候Resume**：
- 复杂任务中断后继续
- 需要之前的上下文

**什么时候新开会话**：
- 开始完全不同的任务
- 之前的上下文不再相关
- 上下文已经太长

---

## 常见问题

### Q1：CLAUDE.md太长会影响性能吗？

**答**：会。CLAUDE.md内容越长，每次请求的token消耗越多。

**建议**：
- 保持CLAUDE.md简洁（<1000行）
- 详细内容放到单独的文档，需要时手动引用
- 使用`.local.md`文件存放临时覆盖

### Q2：Auto Memory会无限增长吗？

**答**：不会。Auto Memory有以下机制：
- 置信度衰减（长时间未验证的知识降低优先级）
- 容量限制（超过限制时清理低置信度记忆）
- 用户可手动删除

### Q3：Autocompact会丢失重要信息吗？

**答**：设计上会尽量保留关键信息：
- 摘要由AI生成，保留"重要"内容
- 最近的对话完整保留
- 但仍可能丢失细节

**建议**：
- 重要决策记录到CLAUDE.md
- 复杂任务分步进行，每步commit

### Q4：如何查看当前Token使用量？

**答**：
- 状态栏会显示使用百分比
- 使用`/tokens`命令查看详细信息
- 日志中会记录每次请求的token消耗

---

## 总结

### 三层记忆架构回顾

```
┌─────────────────────────────────────────────┐
│  长期记忆                                    │
│  ├─ CLAUDE.md：手动维护的项目指令            │
│  └─ Auto Memory：AI自动学习的知识            │
├─────────────────────────────────────────────┤
│  中期记忆                                    │
│  ├─ Session Memory：会话级摘要              │
│  └─ Autocompact：压缩的历史对话             │
├─────────────────────────────────────────────┤
│  短期记忆                                    │
│  └─ Messages：当前对话历史                  │
└─────────────────────────────────────────────┘
```

### 关键设计要点

1. **CLAUDE.md是Agent的"入职手册"** - 每次对话自动加载
2. **Auto Memory让Agent持续学习** - 跨会话积累知识
3. **Autocompact解决上下文限制** - 智能压缩保留关键信息
4. **单一状态树简化管理** - 所有状态集中在AppState
5. **Sidechain Transcript支持恢复** - 子Agent对话可持久化

### 优化清单

- ✅ 保持CLAUDE.md简洁有效
- ✅ 利用Auto Memory让Agent学习
- ✅ 理解并利用Autocompact
- ✅ 只读Agent使用omitClaudeMd
- ✅ 复杂任务分步骤减少上下文压力
- ✅ 定期清理过时的Auto Memory

---

**📚 下一步建议**：

1. **检查项目的CLAUDE.md** - 是否简洁有效？
2. **查看Auto Memory** - Agent学到了什么？
3. **阅读工具系统** - [13-工具系统深度解析](/claudecode/13-git-integration)

**🔗 相关文档**：
- [09-Agent系统架构](/claudecode/09-agent-system) - Agent的底层实现
- [11-Agent协作与Coordinator模式](/claudecode/11-agent-collaboration) - 多Agent协作
- [12-内置Agent深度解析](/claudecode/12-builtin-agents) - 6种内置Agent

---

> 文档版本：v1.0  
> 最后更新：2026-04-02  
> 说明：本文档合并自原10-状态管理与持久化.md和14-内存与上下文管理.md  
> 反馈：如发现错误或有改进建议，欢迎提Issue或PR
