---
title: "工具系统架构"
summary: "工具系统是 Agent 的手脚，就像 USB 接口标准一样，定义了\"能做什么\"的扩展框架。"
publishedAt: 2026-06-12
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 6
seriesGroup: 工具与能力扩展
source: "06-工具系统架构.md"
sourceSha256: 1df512894c62
---
> **一句话理解**：工具系统是 Agent 的手脚，就像 USB 接口标准一样，定义了"能做什么"的扩展框架。

> 📖 **阅读时长**: 约 60 分钟  
> 📂 **核心文件**: `src/Tool.ts`, `src/tools.ts`, `src/services/tools/StreamingToolExecutor.ts`
> **你将获得**：
> - 理解工具如何被定义、注册和执行
> - 掌握工具接口设计的核心模式
> - 能够自己设计和实现新工具

---

## 🎯 先用 2 分钟建立直觉

**想象你在设计一个 USB 接口标准**：

```
USB 标准定义了：                    Tool 接口定义了：
├── 物理插口形状                    ├── 输入参数格式 (inputSchema)
├── 供电规格                        ├── 执行逻辑 (call)
├── 数据传输协议                    ├── 权限检查 (checkPermissions)
└── 热插拔行为                      └── 并发安全性 (isConcurrencySafe)

不管是键盘、鼠标还是U盘，           不管是读文件、执行命令还是搜索，
只要符合 USB 标准就能用。           只要符合 Tool 接口就能被 AI 调用。
```

**为什么需要统一接口？**
- AI 不需要知道每个工具的实现细节
- 新工具可以无缝集成（包括 MCP 外部工具）
- 权限、日志、错误处理可以统一管理

**💡 关键洞察**：Tool 接口是 Claude Code 最重要的扩展点。
理解了这个接口，你就理解了如何扩展 AI 的能力边界。

---

## 📋 本章概览

工具系统是 Claude Code 的"手脚"——让 AI 从纯粹的对话转变为能实际操作文件、执行命令、搜索代码的智能助手。本章深入解析工具的定义、注册、执行和权限控制。

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Tool System Architecture                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Tool Registry (tools.ts)                  │   │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐    │   │
│  │  │ BashTool  │ │ FileRead  │ │ FileEdit  │ │ AgentTool │    │   │
│  │  └───────────┘ └───────────┘ └───────────┘ └───────────┘    │   │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐    │   │
│  │  │ GrepTool  │ │ GlobTool  │ │ SkillTool │ │ MCP Tools │    │   │
│  │  └───────────┘ └───────────┘ └───────────┘ └───────────┘    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                StreamingToolExecutor                         │   │
│  │  • 并发控制 (concurrency safe vs exclusive)                  │   │
│  │  • 流式执行 (streaming execution)                            │   │
│  │  • 错误隔离 (sibling abort on error)                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   Permission System                          │   │
│  │  • PermissionMode: default | plan | auto | bypass           │   │
│  │  • Rules: alwaysAllow | alwaysDeny | alwaysAsk              │   │
│  │  • Hooks: PreToolUse / PostToolUse                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 1. 工具类型定义 (Tool Interface)

### 1.1 核心 Tool 接口

```typescript
// 📁 src/Tool.ts:362-600 (精简版)

export type Tool<
  Input extends AnyObject = AnyObject,
  Output = unknown,
  P extends ToolProgressData = ToolProgressData,
> = {
  // === 基础属性 ===
  readonly name: string                     // 工具名称（唯一标识）
  aliases?: string[]                        // 兼容别名
  description(input, options): Promise<string>  // 动态描述
  readonly inputSchema: Input               // Zod 输入验证
  readonly inputJSONSchema?: ToolInputJSONSchema  // MCP 工具的 JSON Schema
  
  // === 执行相关 ===
  call(args, context, canUseTool, parentMessage, onProgress): Promise<ToolResult<Output>>
  
  // === 并发与行为 ===
  isConcurrencySafe(input): boolean         // 是否可并行
  isReadOnly(input): boolean                // 是否只读
  isDestructive?(input): boolean            // 是否破坏性操作
  isEnabled(): boolean                      // 是否启用
  
  // === 权限相关 ===
  validateInput?(input, context): Promise<ValidationResult>
  checkPermissions(input, context): Promise<PermissionResult>
  
  // === 渲染相关 ===
  prompt(options): Promise<string>          // 工具说明（给 AI）
  userFacingName(input): string             // UI 显示名
  renderToolResultMessage?(content, progressMessages, options): React.ReactNode
  
  // === 结果处理 ===
  maxResultSizeChars: number                // 最大结果大小
  mapToolResultToToolResultBlockParam(content, toolUseID): ToolResultBlockParam
}
```

### 1.2 工具类型图示

```
┌────────────────────────────────────────────────────────────────────┐
│                         Tool Type System                            │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  Tool<Input, Output, Progress>                                     │
│  │                                                                 │
│  ├── Input: z.ZodType<{...}>         ← Zod Schema 定义输入        │
│  │   └─► 例：{ file_path: z.string(), offset: z.number().optional() }
│  │                                                                 │
│  ├── Output: any                     ← 工具执行结果类型           │
│  │   └─► 例：{ content: string, lines: number }                   │
│  │                                                                 │
│  └── Progress: ToolProgressData      ← 进度数据类型               │
│      └─► 例：BashProgress | AgentToolProgress                     │
│                                                                    │
│  ToolResult<Output> = {                                            │
│    data: Output,                     // 执行结果                   │
│    newMessages?: Message[],          // 附加消息（如子代理消息）   │
│    contextModifier?: fn,             // 上下文修改器               │
│    mcpMeta?: {...}                   // MCP 元数据                 │
│  }                                                                 │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. 内置工具一览

### 2.1 核心工具列表

```
┌────────────────────────────────────────────────────────────────────┐
│                       Built-in Tools                                │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  📂 文件操作                                                        │
│  ├── FileReadTool      读取文件内容（支持分页）                    │
│  ├── FileWriteTool     创建/覆盖文件                               │
│  ├── FileEditTool      编辑文件（search/replace 模式）             │
│  └── NotebookEditTool  编辑 Jupyter Notebook                       │
│                                                                    │
│  🔍 搜索工具                                                        │
│  ├── GrepTool          正则搜索文件内容                            │
│  ├── GlobTool          按模式搜索文件名                            │
│  └── ToolSearchTool    搜索可用工具（deferred tools）              │
│                                                                    │
│  💻 命令执行                                                        │
│  ├── BashTool          执行 shell 命令（macOS/Linux）              │
│  └── PowerShellTool    执行 PowerShell 命令（Windows）             │
│                                                                    │
│  🤖 智能工具                                                        │
│  ├── AgentTool         创建子代理                                  │
│  ├── SkillTool         执行 Skill（/command）                      │
│  └── AskUserQuestionTool  向用户提问                               │
│                                                                    │
│  📋 任务管理                                                        │
│  ├── TaskCreateTool    创建任务                                    │
│  ├── TaskUpdateTool    更新任务状态                                │
│  ├── TaskListTool      列出任务                                    │
│  └── TodoWriteTool     写入 TODO 列表                              │
│                                                                    │
│  🌐 网络工具                                                        │
│  ├── WebFetchTool      获取网页内容                                │
│  └── WebSearchTool     搜索网页                                    │
│                                                                    │
│  🔧 MCP 工具                                                        │
│  ├── ListMcpResourcesTool  列出 MCP 资源                           │
│  └── ReadMcpResourceTool   读取 MCP 资源                           │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 2.2 工具注册中心

```typescript
// 📁 src/tools.ts:1-100 (简化)

import { BashTool } from './tools/BashTool/BashTool.js'
import { FileEditTool } from './tools/FileEditTool/FileEditTool.js'
import { FileReadTool } from './tools/FileReadTool/FileReadTool.js'
import { FileWriteTool } from './tools/FileWriteTool/FileWriteTool.js'
import { GlobTool } from './tools/GlobTool/GlobTool.js'
import { GrepTool } from './tools/GrepTool/GrepTool.js'
import { AgentTool } from './tools/AgentTool/AgentTool.js'
import { SkillTool } from './tools/SkillTool/SkillTool.js'
// ... 更多工具导入

// 获取所有基础工具
export function getAllBaseTools(): Tools {
  return [
    BashTool,
    FileReadTool,
    FileWriteTool,
    FileEditTool,
    GlobTool,
    GrepTool,
    AgentTool,
    SkillTool,
    AskUserQuestionTool,
    WebFetchTool,
    WebSearchTool,
    // 条件工具
    ...(isToolSearchEnabledOptimistic() ? [ToolSearchTool] : []),
    ...(isTodoV2Enabled() ? [TaskCreateTool, TaskUpdateTool, TaskListTool] : [TodoWriteTool]),
    // MCP 工具
    ListMcpResourcesTool,
    ReadMcpResourceTool,
    // 更多...
  ].filter(t => t.isEnabled())
}
```

---

## 3. 工具执行流程

### 3.1 StreamingToolExecutor

```
┌────────────────────────────────────────────────────────────────────┐
│                   StreamingToolExecutor Flow                        │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   AI Response (ToolUseBlock[])                                     │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ addTool(block, assistantMessage)                             │ │
│   │ ├── 解析工具定义                                             │ │
│   │ ├── 检查 isConcurrencySafe                                   │ │
│   │ └── 加入执行队列 (status: 'queued')                          │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ processQueue()                                               │ │
│   │ ├── 检查并发条件：                                           │ │
│   │ │   • 无正在执行的工具？ → 可执行                            │ │
│   │ │   • 当前工具是并发安全的 AND 所有执行中工具都并发安全？    │ │
│   │ │     → 可并行执行                                           │ │
│   │ │   • 否则 → 等待                                            │ │
│   │ └── 满足条件时调用 executeTool()                             │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ executeTool(tool)                                            │ │
│   │ ├── status → 'executing'                                     │ │
│   │ ├── runToolUse(block, context, canUseTool, onProgress)       │ │
│   │ ├── 收集结果和进度消息                                       │ │
│   │ └── status → 'completed'                                     │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ getRemainingResults() → AsyncGenerator                       │ │
│   │ ├── 按顺序 yield 完成的工具结果                              │ │
│   │ ├── 实时 yield 进度消息                                      │ │
│   │ └── 保证结果顺序与工具调用顺序一致                           │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 3.2 并发控制策略

```typescript
// 📁 src/services/tools/StreamingToolExecutor.ts:40-135

export class StreamingToolExecutor {
  private tools: TrackedTool[] = []
  
  // 检查工具是否可以执行
  private canExecuteTool(isConcurrencySafe: boolean): boolean {
    const executingTools = this.tools.filter(t => t.status === 'executing')
    return (
      // 无正在执行的工具
      executingTools.length === 0 ||
      // 或者：当前工具安全 AND 所有执行中工具都安全
      (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe))
    )
  }
  
  // 添加工具到队列
  addTool(block: ToolUseBlock, assistantMessage: AssistantMessage): void {
    const toolDefinition = findToolByName(this.toolDefinitions, block.name)
    
    // 解析输入并检查并发安全性
    const parsedInput = toolDefinition.inputSchema.safeParse(block.input)
    const isConcurrencySafe = parsedInput?.success
      ? toolDefinition.isConcurrencySafe(parsedInput.data)
      : false
    
    this.tools.push({
      id: block.id,
      block,
      assistantMessage,
      status: 'queued',
      isConcurrencySafe,
      pendingProgress: [],
    })
    
    void this.processQueue()
  }
}
```

### 3.3 并发安全性判断

```
┌────────────────────────────────────────────────────────────────────┐
│                   Concurrency Safety Examples                       │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ✅ 并发安全 (isConcurrencySafe = true)                            │
│  ─────────────────────────────────────────────────────────────────  │
│  • FileReadTool     只读操作，无副作用                             │
│  • GrepTool         只读搜索                                       │
│  • GlobTool         只读搜索                                       │
│  • WebFetchTool     只读获取（不同 URL）                           │
│                                                                    │
│  ⚠️ 需要检查的工具                                                 │
│  ─────────────────────────────────────────────────────────────────  │
│  • FileWriteTool    同一文件不安全，不同文件安全                   │
│  • FileEditTool     同一文件不安全，不同文件安全                   │
│  • BashTool         取决于命令内容                                 │
│                                                                    │
│  ❌ 不并发安全 (isConcurrencySafe = false)                         │
│  ─────────────────────────────────────────────────────────────────  │
│  • AgentTool        子代理需要独占上下文                           │
│  • SkillTool        可能修改全局状态                               │
│  • EnterPlanModeTool 修改权限模式                                  │
│                                                                    │
│  判断示例：                                                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ // FileEditTool.isConcurrencySafe                           │   │
│  │ isConcurrencySafe(input) {                                  │   │
│  │   // 编辑不同文件时可以并行                                 │   │
│  │   return this.getPath(input) !== lastEditedPath             │   │
│  │ }                                                           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. 权限系统

### 4.1 PermissionMode

```typescript
// 📁 src/types/permissions.ts

export type PermissionMode = 
  | 'default'    // 默认：敏感操作需确认
  | 'plan'       // 计划模式：只允许只读操作
  | 'auto'       // 自动模式：分类器自动判断
  | 'bypass'     // 绕过模式：信任所有操作
```

### 4.2 权限判断流程

```
┌────────────────────────────────────────────────────────────────────┐
│                     Permission Check Flow                           │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   Tool Call Request                                                │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ 1. validateInput(input, context)                             │ │
│   │    └─► 验证输入格式和基本约束                                │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │ (通过)                                                  │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ 2. checkPermissions(input, context)                          │ │
│   │    ├─► 检查 alwaysAllow 规则                                 │ │
│   │    ├─► 检查 alwaysDeny 规则                                  │ │
│   │    ├─► 检查 alwaysAsk 规则                                   │ │
│   │    └─► 返回 PermissionResult                                 │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ 3. PermissionResult 类型：                                   │ │
│   │    • { behavior: 'allow' }  → 直接执行                       │ │
│   │    • { behavior: 'deny', message }  → 拒绝                   │ │
│   │    • { behavior: 'askUser', ... }  → 弹出确认框              │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ▼ (如果是 askUser)                                        │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ 4. canUseTool(tool, input) → 用户交互                        │ │
│   │    ├─► Accept (一次) → 执行                                  │ │
│   │    ├─► Accept (总是) → 添加到 alwaysAllow                    │ │
│   │    ├─► Reject (一次) → 拒绝此次                              │ │
│   │    └─► Reject (总是) → 添加到 alwaysDeny                     │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 4.3 ToolPermissionContext

```typescript
// 📁 src/Tool.ts:123-138

export type ToolPermissionContext = DeepImmutable<{
  mode: PermissionMode
  additionalWorkingDirectories: Map<string, AdditionalWorkingDirectory>
  alwaysAllowRules: ToolPermissionRulesBySource
  alwaysDenyRules: ToolPermissionRulesBySource
  alwaysAskRules: ToolPermissionRulesBySource
  isBypassPermissionsModeAvailable: boolean
  isAutoModeAvailable?: boolean
  shouldAvoidPermissionPrompts?: boolean  // 后台代理
  awaitAutomatedChecksBeforeDialog?: boolean  // 协调器
  prePlanMode?: PermissionMode  // 进入 plan 模式前的状态
}>
```

### 4.4 Auto 模式的分类器

```
┌────────────────────────────────────────────────────────────────────┐
│                     Auto Mode Classifier                            │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   Tool Call (Auto Mode)                                            │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ YOLO Classifier (LLM-based)                                  │ │
│   │ ├── 输入：工具名、参数、上下文                               │ │
│   │ ├── 判断：该操作是否安全                                     │ │
│   │ └── 输出：SAFE | UNSAFE | ASK                                │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ├─► SAFE    → 自动执行                                    │
│          ├─► UNSAFE  → 自动拒绝                                    │
│          └─► ASK     → 回退到用户确认                              │
│                                                                    │
│   分类器系统提示词（简化）：                                        │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ You are a security classifier. Analyze if the tool call     │ │
│   │ is safe to execute without user confirmation.               │ │
│   │                                                              │ │
│   │ SAFE operations:                                             │ │
│   │ - Reading files in the project directory                    │ │
│   │ - Running tests                                              │ │
│   │ - Searching code                                             │ │
│   │                                                              │ │
│   │ UNSAFE operations:                                           │ │
│   │ - Deleting files outside project                            │ │
│   │ - Running commands with `sudo`                              │ │
│   │ - Pushing to git remote                                      │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 5. 工具上下文 (ToolUseContext)

### 5.1 核心属性

```typescript
// 📁 src/Tool.ts:158-300 (精简)

export type ToolUseContext = {
  // 配置选项
  options: {
    commands: Command[]          // 可用命令
    debug: boolean               // 调试模式
    mainLoopModel: string        // 主模型
    tools: Tools                 // 可用工具列表
    verbose: boolean             // 详细输出
    thinkingConfig: ThinkingConfig  // 思考配置
    mcpClients: MCPServerConnection[]  // MCP 客户端
    isNonInteractiveSession: boolean   // 非交互模式
    agentDefinitions: AgentDefinitionsResult  // Agent 定义
  }
  
  // 控制器
  abortController: AbortController  // 中断控制
  
  // 状态缓存
  readFileState: FileStateCache     // 文件读取缓存
  
  // 状态访问器
  getAppState(): AppState
  setAppState(f: (prev: AppState) => AppState): void
  
  // UI 交互
  setToolJSX?: SetToolJSXFn         // 设置工具 UI
  addNotification?: (notif: Notification) => void
  appendSystemMessage?: (msg: SystemMessage) => void
  
  // 进度控制
  setInProgressToolUseIDs: (f: (prev: Set<string>) => Set<string>) => void
  setResponseLength: (f: (prev: number) => number) => void
  
  // 消息历史
  messages: Message[]
  
  // 子代理相关
  agentId?: AgentId
  agentType?: string
  
  // 查询链追踪
  queryTracking?: QueryChainTracking
}
```

### 5.2 上下文流转图

```
┌────────────────────────────────────────────────────────────────────┐
│                    ToolUseContext Flow                              │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   REPL / QueryEngine                                               │
│          │ 创建初始 context                                        │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ ToolUseContext (Main Thread)                                 │ │
│   │ • options.tools = getAllBaseTools() + MCP tools              │ │
│   │ • messages = 当前会话消息                                    │ │
│   │ • abortController = 用户可中断                               │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          │ 传递给工具                                              │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ Tool.call(args, context, canUseTool, parentMessage, onProgress)
│   │ • 使用 context.readFileState 缓存文件                        │
│   │ • 使用 context.setAppState 更新状态                          │ │
│   │ • 使用 context.abortController 检查中断                      │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          │ 子代理场景                                              │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ createSubagentContext(parentContext)                         │ │
│   │ • 继承父上下文的大部分属性                                   │ │
│   │ • 创建独立的 agentId                                         │ │
│   │ • 可选：克隆 contentReplacementState                         │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 6. 工具进度系统

### 6.1 ToolProgressData 类型

```typescript
// 📁 src/types/tools.ts (概念)

export type ToolProgressData =
  | BashProgress           // Bash 执行进度
  | AgentToolProgress      // 子代理进度
  | MCPProgress           // MCP 工具进度
  | REPLToolProgress      // REPL 工具进度
  | SkillToolProgress     // Skill 工具进度
  | TaskOutputProgress    // 任务输出进度
  | WebSearchProgress     // 网页搜索进度

export type BashProgress = {
  type: 'bash_progress'
  content: string          // stdout 输出
  isError?: boolean        // 是否 stderr
  isComplete?: boolean     // 是否完成
  exitCode?: number        // 退出码
}

export type AgentToolProgress = {
  type: 'agent_progress'
  agentType: string
  content: string
  isComplete?: boolean
}
```

### 6.2 进度消息流

```
┌────────────────────────────────────────────────────────────────────┐
│                     Progress Message Flow                           │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   Tool.call(..., onProgress)                                       │
│          │                                                         │
│          │ onProgress({ toolUseID, data: { type: 'bash_progress' } })
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ createProgressMessage({ toolUseID, parentToolUseID, data })  │ │
│   │ → ProgressMessage<BashProgress>                              │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ▼ yield                                                   │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ StreamingToolExecutor.getRemainingResults()                  │ │
│   │ • 实时 yield 进度消息（不等待工具完成）                      │ │
│   └──────────────────────────────────────────────────────────────┘ │
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ UI 渲染                                                      │ │
│   │ • 显示 Bash 输出流                                           │ │
│   │ • 显示子代理工作状态                                         │ │
│   │ • 显示搜索进度等                                             │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 7. MCP 工具集成

### 7.1 MCP 工具结构

```
┌────────────────────────────────────────────────────────────────────┐
│                       MCP Tool Integration                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   MCP Server Connection                                            │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ MCPServerConnection                                         │  │
│   │ ├── name: string         // 服务器名称                      │  │
│   │ ├── type: 'connected' | 'connecting' | 'disconnected'      │  │
│   │ ├── tools: MCPToolDefinition[]  // 工具列表                 │  │
│   │ ├── resources: ServerResource[]  // 资源列表               │  │
│   │ └── instructions?: string  // 使用说明                      │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│   MCP Tool 命名规范：                                              │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ 格式: mcp__{serverName}__{toolName}                         │  │
│   │ 例如: mcp__github__search_repos                             │  │
│   │                                                              │  │
│   │ mcpInfo = {                                                 │  │
│   │   serverName: 'github',                                     │  │
│   │   toolName: 'search_repos'                                  │  │
│   │ }                                                           │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│   MCP 工具特殊属性：                                               │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ isMcp: true                                                 │  │
│   │ inputJSONSchema: { ... }  // 直接使用 JSON Schema           │  │
│   │ mcpInfo: { serverName, toolName }                          │  │
│   │ alwaysLoad?: boolean  // 强制加载（不 defer）               │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 7.2 Deferred Tools (延迟加载)

```
┌────────────────────────────────────────────────────────────────────┐
│                      Deferred Tools System                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   问题：工具太多会导致 prompt 过长                                  │
│                                                                    │
│   解决方案：Tool Search + Deferred Loading                         │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ 1. 初始加载：核心工具 + ToolSearchTool                      │  │
│   │ 2. AI 需要特定功能时，调用 ToolSearchTool                   │  │
│   │ 3. ToolSearchTool 返回匹配的工具定义                        │  │
│   │ 4. AI 可以使用这些工具                                      │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│   标记方式：                                                       │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ // 工具定义                                                 │  │
│   │ {                                                           │  │
│   │   name: 'mcp__github__search_repos',                       │  │
│   │   shouldDefer: true,  // 延迟加载                           │  │
│   │   alwaysLoad: false,  // MCP: _meta['anthropic/alwaysLoad'] │  │
│   │   searchHint: 'search GitHub repositories'  // 搜索关键词  │  │
│   │ }                                                           │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│   ToolSearchTool 工作流：                                          │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │ 用户: "搜索 GitHub 上的 React 项目"                         │  │
│   │                                                              │  │
│   │ AI: 调用 ToolSearchTool({ query: "search GitHub repos" })   │  │
│   │                                                              │  │
│   │ ToolSearchTool 返回:                                        │  │
│   │ "Found tools: mcp__github__search_repos - Search GitHub..." │  │
│   │                                                              │  │
│   │ AI: 调用 mcp__github__search_repos({ query: "react" })      │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 8. 错误处理

### 8.1 工具执行错误

```typescript
// 📁 src/services/tools/toolExecution.ts:150+

export function classifyToolError(error: unknown): string {
  // TelemetrySafeError: 使用其 telemetryMessage
  if (error instanceof TelemetrySafeError) {
    return error.telemetryMessage
  }
  
  // Node.js fs 错误: 使用错误码
  const errnoCode = getErrnoCode(error)
  if (errnoCode) {
    return `fs_${errnoCode}` // e.g., 'fs_ENOENT', 'fs_EACCES'
  }
  
  // 已知错误类型
  if (error instanceof AbortError) return 'AbortError'
  if (error instanceof ShellError) return 'ShellError'
  if (error instanceof McpAuthError) return 'McpAuthError'
  
  // 回退
  return 'Error'
}
```

### 8.2 Sibling Abort 机制

```
┌────────────────────────────────────────────────────────────────────┐
│                      Sibling Abort Pattern                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   场景：并行执行的多个工具中，一个出错                              │
│                                                                    │
│   ┌───────────┐  ┌───────────┐  ┌───────────┐                     │
│   │ BashTool  │  │ BashTool  │  │ BashTool  │                     │
│   │ (npm test)│  │ (build)   │  │ (lint)    │                     │
│   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘                     │
│         │              │              │                            │
│         │              │  ❌ Error    │                            │
│         │              │              │                            │
│         ▼              ▼              ▼                            │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ siblingAbortController.abort()                               │ │
│   │ • 取消所有正在执行的兄弟工具                                 │ │
│   │ • 不会中断父查询循环                                         │ │
│   │ • 生成合成错误消息给被取消的工具                             │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│   结果消息：                                                       │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ tool_result (npm test): <actual_output>                      │ │
│   │ tool_result (build):    <error_output>                       │ │
│   │ tool_result (lint):     "Cancelled: parallel tool errored"   │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 9. 关键文件索引

| 文件路径 | 核心内容 | 行数参考 |
|---------|---------|---------|
| `src/Tool.ts` | Tool 接口定义、ToolUseContext | 1-600+ |
| `src/tools.ts` | 工具注册中心、getAllBaseTools | 1-200 |
| `src/services/tools/StreamingToolExecutor.ts` | 流式工具执行器 | 1-400+ |
| `src/services/tools/toolExecution.ts` | runToolUse、错误分类 | 1-600+ |
| `src/services/tools/toolHooks.ts` | Pre/Post ToolUse Hooks | - |
| `src/tools/*/` | 各工具实现目录 | - |
| `src/services/mcp/client.ts` | MCP 客户端、工具代理 | - |

---

## 🎓 本章小结

### 核心要点

1. **Tool 接口是扩展核心**：所有工具遵循相同接口，像 USB 标准一样即插即用
2. **并发安全是关键设计**：`isConcurrencySafe` 让只读工具并行、写入工具串行
3. **权限系统分层**：从严格到宽松 (default → plan → auto → bypass)
4. **MCP 无缝集成**：外部工具通过适配层变成"本地工具"

### 可迁移的设计原则

| 原则 | 说明 | 适用场景 |
|------|------|---------|
| 统一接口 | 所有扩展遵循相同契约 | 任何需要扩展的系统 |
| 并发标记 | 显式声明并发安全性 | 有读写混合操作的场景 |
| 延迟加载 | 工具太多时按需发现 | 资源受限或冷启动敏感 |
| 错误隔离 | 一个失败不影响全局 | 并行执行的场景 |
| Hook 机制 | Pre/Post 钩子支持定制 | 需要审计、日志、权限的场景 |

### 🧠 为什么这样设计？

**问题：工具太多怎么办？**
- Claude 的上下文窗口有限，不能同时描述 100+ 个工具
- 解决方案：延迟加载 + ToolSearchTool（先搜索再加载）

**问题：工具执行失败会影响其他工具吗？**
- 并行执行的工具可能有依赖关系（如先创建文件，再写入）
- 解决方案：Sibling Abort 机制，一个失败取消其他正在执行的

**问题：如何接入外部服务的工具？**
- 每个外部服务有不同的 API 格式
- 解决方案：MCP 协议统一标准，适配层转换为 Tool 接口

### 🤔 思考题

1. **如果你要添加一个"发送邮件"的工具**，它应该是 `isConcurrencySafe` 还是不是？为什么？
   - 提示：考虑重复发送的风险

2. **为什么 `isReadOnly` 和 `isConcurrencySafe` 是两个独立的属性**，而不是合并为一个？
   - 提示：想一个"只读但不能并发"的场景

3. **延迟加载机制的缺点是什么？** 有什么场景下不应该使用？
   - 提示：考虑用户体验和额外的 API 调用成本

4. **如果你要设计一个"可撤销"的工具执行机制**，需要对 Tool 接口做什么扩展？
   - 提示：考虑 `undo()` 方法和执行历史

```
┌─────────────────────────────────────────────────────────────────┐
│                     Tool System Takeaways                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. 工具定义包含执行、权限、渲染三大类方法                       │
│  2. isConcurrencySafe 决定是否可以并行执行                      │
│  3. 权限系统支持规则匹配和用户交互两种方式                       │
│  4. 进度消息允许工具执行时实时反馈                               │
│  5. MCP 工具通过适配层无缝集成到系统                            │
│  6. 错误处理包含分类、隔离、恢复多个层面                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

接下来，我们将深入核心工具的实现细节：

👉 **继续阅读**: [07-核心工具实现.md](/claudecode/07-core-tools)
