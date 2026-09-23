---
title: "消息系统设计"
summary: "消息系统是 Agent 的神经系统，所有信息都通过统一的\"消息\"格式在组件间流动，就像快递分拣中心处理包裹。"
publishedAt: 2026-05-29
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 4
seriesGroup: 核心架构
source: "04-消息系统设计.md"
sourceSha256: a5fc5a99e02c
---
> **一句话理解**：消息系统是 Agent 的神经系统，所有信息都通过统一的"消息"格式在组件间流动，就像快递分拣中心处理包裹。

> 📖 **阅读时长**: 约 50 分钟  
> 📂 **核心文件**: `src/utils/messages.ts`, `src/query.ts`, `src/types/logs.ts`
> **你将获得**：
> - 理解消息如何在系统中流转
> - 掌握不同消息类型的用途
> - 明白为什么需要这么多消息类型

---

## 🎯 先用 2 分钟建立直觉

**想象一个快递分拣中心**：

```
快递中心处理的包裹：                 消息系统处理的消息：
├── 客户寄出的包裹                  ├── UserMessage (用户输入)
├── 退回的包裹                      ├── AssistantMessage (AI 回复)
├── 内部调拨单                      ├── SystemMessage (系统通知)
├── 配送进度通知                    ├── ProgressMessage (工具进度)
└── 损坏报告                        └── TombstoneMessage (已删除)

每个包裹都有：                       每个消息都有：
├── 寄件人/收件人                   ├── 角色 (user/assistant)
├── 包裹内容                        ├── 内容块 (ContentBlock)
├── 唯一编号                        ├── UUID
└── 时间戳                          └── 时间戳
```

**为什么需要统一的消息格式？**
- Claude API 期望特定格式的消息历史
- UI 需要知道如何渲染每种消息
- 持久化需要统一的序列化格式
- 不同组件需要用同一种"语言"交流

**💡 关键洞察**：消息不只是数据，它是系统各组件的**通信协议**。
理解消息类型，就理解了组件之间如何"对话"。

---

## 📋 本章概览

消息系统是 Claude Code 中**数据流的载体**，所有用户输入、AI 响应、工具调用结果都以消息形式在系统中流转。理解消息系统是掌握整个架构的基础。

```
┌─────────────────────────────────────────────────────────────────────┐
│                        消息系统全景                                   │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
│  │ UserMessage │    │ Assistant   │    │  Progress   │              │
│  │    用户输入   │    │   Message   │    │   Message   │              │
│  └──────┬──────┘    │   AI响应    │    │   进度通知   │              │
│         │           └──────┬──────┘    └──────┬──────┘              │
│         │                  │                  │                     │
│         ▼                  ▼                  ▼                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │                   Message Union Type                     │        │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐        │        │
│  │  │ System  │ │Attachment│ │Tombstone│ │ToolUse  │        │        │
│  │  │ Message │ │ Message │ │ Message │ │ Summary │        │        │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘        │        │
│  └─────────────────────────────────────────────────────────┘        │
│                              │                                      │
│                              ▼                                      │
│         ┌───────────────────────────────────────────┐              │
│         │  ContentBlock (Anthropic SDK Types)       │              │
│         │  • TextBlock   • ToolUseBlock             │              │
│         │  • ThinkingBlock • ToolResultBlock        │              │
│         │  • ImageBlock  • DocumentBlock            │              │
│         └───────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 1. 消息类型体系

### 1.1 核心消息类型概览

Claude Code 定义了一套丰富的消息类型体系，每种类型承担特定职责：

| 消息类型 | 角色 | 发送给 API | 渲染到 UI | 主要用途 |
|---------|------|-----------|-----------|---------|
| `UserMessage` | user | ✅ | ✅ | 用户输入、工具结果 |
| `AssistantMessage` | assistant | ✅ | ✅ | AI 文本响应、工具调用 |
| `SystemMessage` | - | ❌ | ✅ | 错误提示、状态通知 |
| `ProgressMessage` | - | ❌ | ✅ | 工具执行进度 |
| `AttachmentMessage` | - | ❌ | ✅ | Hook 结果、元数据 |
| `TombstoneMessage` | - | ❌ | ❌ | 标记已删除的消息 |
| `ToolUseSummaryMessage` | - | ❌ | ✅ | 工具调用摘要 |

### 1.2 消息类型定义

```
┌────────────────────────────────────────────────────────────────┐
│                      Message Type Hierarchy                    │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Message (Union Type)                                          │
│  ├── UserMessage ──────────────► API Message (role: 'user')    │
│  │   ├── content: string | ContentBlockParam[]                 │
│  │   ├── uuid: UUID                                            │
│  │   ├── timestamp: ISO string                                 │
│  │   ├── isMeta?: boolean          ← 元消息(不显示)            │
│  │   ├── isVirtual?: boolean       ← 虚拟消息(不发API)         │
│  │   ├── isCompactSummary?: boolean ← 压缩摘要                 │
│  │   ├── toolUseResult?: unknown   ← 工具调用结果              │
│  │   └── origin?: MessageOrigin    ← 消息来源                  │
│  │                                                             │
│  ├── AssistantMessage ─────────► API Message (role: 'assistant')
│  │   ├── message: BetaMessage      ← 完整 API 响应             │
│  │   ├── uuid: UUID                                            │
│  │   ├── requestId?: string        ← 请求追踪                  │
│  │   ├── apiError?: string         ← API 错误类型              │
│  │   ├── isApiErrorMessage?: boolean                           │
│  │   └── isVirtual?: boolean                                   │
│  │                                                             │
│  ├── SystemMessage ────────────► UI Only (不发 API)            │
│  │   ├── subtype: string           ← 子类型标识                │
│  │   │   ├── 'api_error'           ← API 错误                  │
│  │   │   ├── 'informational'       ← 信息提示                  │
│  │   │   ├── 'compact_boundary'    ← 压缩边界                  │
│  │   │   ├── 'memory_saved'        ← 记忆保存                  │
│  │   │   ├── 'turn_duration'       ← 回合耗时                  │
│  │   │   └── ...更多子类型                                     │
│  │   └── message: string           ← 显示内容                  │
│  │                                                             │
│  ├── ProgressMessage<T> ───────► UI Only (实时进度)            │
│  │   ├── data: T (Progress)        ← 进度数据                  │
│  │   ├── toolUseID: string         ← 关联的工具调用            │
│  │   └── parentToolUseID: string   ← 父工具调用(子代理)        │
│  │                                                             │
│  └── AttachmentMessage<T> ─────► UI Only (附件/Hook结果)       │
│      ├── attachment: T             ← 附件数据                  │
│      └── type: 'attachment'                                    │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

> 📁 **类型定义**: `src/types/message.js` (编译后导出), `src/query.ts:30-39`

---

## 2. ContentBlock 系统

### 2.1 Anthropic SDK 的 ContentBlock 类型

消息内容由 **ContentBlock** 数组组成，这是 Anthropic API 的核心数据结构：

```
┌──────────────────────────────────────────────────────────────────┐
│                      ContentBlock Types                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  发送给 API (ContentBlockParam)     │    API 响应 (ContentBlock) │
│  ─────────────────────────────────  │  ───────────────────────── │
│                                     │                            │
│  TextBlockParam ◄─────────────────► TextBlock                    │
│  { type: 'text', text: string }     │  + citations?: Citation[]  │
│                                     │                            │
│  ToolUseBlockParam ◄──────────────► ToolUseBlock                 │
│  { type: 'tool_use',                │                            │
│    id: string,                      │                            │
│    name: string,                    │                            │
│    input: object }                  │                            │
│                                     │                            │
│  ToolResultBlockParam ─────────────►                             │
│  { type: 'tool_result',             │  (只出现在 UserMessage)    │
│    tool_use_id: string,             │                            │
│    content: string | Array,         │                            │
│    is_error?: boolean }             │                            │
│                                     │                            │
│  ImageBlockParam ──────────────────►                             │
│  { type: 'image',                   │                            │
│    source: { type, media_type,      │                            │
│              data: base64 } }       │                            │
│                                     │                            │
│  ThinkingBlockParam ◄─────────────► ThinkingBlock                │
│  { type: 'thinking',                │  (Claude 3.5 思考过程)     │
│    thinking: string }               │                            │
│                                     │                            │
│  RedactedThinkingBlockParam ◄─────► RedactedThinkingBlock        │
│  { type: 'redacted_thinking' }      │  (隐藏的思考过程)          │
│                                     │                            │
└──────────────────────────────────────────────────────────────────┘
```

> 📁 **类型来源**: `@anthropic-ai/sdk/resources/messages.mjs`, `src/utils/messages.ts:3-14`

### 2.2 工具调用的 ContentBlock 流转

工具调用涉及两种关键的 ContentBlock 类型：

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Tool Use / Tool Result 流转                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   1. AI 发起工具调用 (AssistantMessage)                              │
│   ┌─────────────────────────────────────────────────┐               │
│   │ ToolUseBlock                                    │               │
│   │ {                                               │               │
│   │   type: 'tool_use',                            │               │
│   │   id: 'toolu_01XYZ...',      ← 唯一标识符      │               │
│   │   name: 'FileReadTool',      ← 工具名称        │               │
│   │   input: {                   ← 工具输入参数    │               │
│   │     file_path: '/src/main.ts',                 │               │
│   │     offset: 1,                                 │               │
│   │     limit: 100                                 │               │
│   │   }                                            │               │
│   │ }                                              │               │
│   └─────────────────────────────────────────────────┘               │
│                              │                                      │
│                              ▼                                      │
│   2. 系统执行工具，返回结果 (UserMessage)                            │
│   ┌─────────────────────────────────────────────────┐               │
│   │ ToolResultBlockParam                            │               │
│   │ {                                               │               │
│   │   type: 'tool_result',                         │               │
│   │   tool_use_id: 'toolu_01XYZ...',  ← 对应的调用 │               │
│   │   content: [                      ← 结果内容   │               │
│   │     { type: 'text', text: '...' }              │               │
│   │   ],                                           │               │
│   │   is_error: false               ← 是否出错    │               │
│   │ }                                              │               │
│   └─────────────────────────────────────────────────┘               │
│                                                                     │
│   3. 重要：tool_use_id 必须严格匹配！                               │
│      API 会校验每个 tool_use 都有对应的 tool_result                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 消息创建工厂函数

### 3.1 核心工厂函数

系统通过工厂函数统一创建消息，确保格式正确：

```typescript
// 📁 src/utils/messages.ts:460-523

// 创建用户消息
export function createUserMessage({
  content,
  isMeta,              // 元消息（不显示）
  isVisibleInTranscriptOnly, // 仅在日志中可见
  isVirtual,           // 不发送到 API
  isCompactSummary,    // 压缩摘要
  toolUseResult,       // 工具结果（用于类型推断）
  origin,              // 消息来源
  // ...
}): UserMessage {
  return {
    type: 'user',
    message: {
      role: 'user',
      content: content || NO_CONTENT_MESSAGE,
    },
    uuid: randomUUID(),
    timestamp: new Date().toISOString(),
    // ... 其他字段
  }
}
```

### 3.2 工厂函数一览

```
┌────────────────────────────────────────────────────────────────────┐
│                     Message Factory Functions                       │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  createUserMessage()                                               │
│  └─► 普通用户输入、工具结果、压缩摘要                               │
│                                                                    │
│  createUserInterruptionMessage()                                   │
│  └─► 用户中断消息 "[Request interrupted by user]"                  │
│                                                                    │
│  createAssistantMessage()                                          │
│  └─► AI 文本响应（从流式响应构建）                                  │
│                                                                    │
│  createAssistantAPIErrorMessage()                                  │
│  └─► API 错误响应（带 apiError 标记）                              │
│                                                                    │
│  createProgressMessage<P>()                                        │
│  └─► 工具执行进度（类型参数 P 对应进度数据类型）                    │
│                                                                    │
│  createSystemMessage()                                             │
│  └─► 系统通知（错误、信息、边界标记等）                             │
│                                                                    │
│  createToolResultStopMessage()                                     │
│  └─► 工具中断结果 { is_error: true, content: CANCEL_MESSAGE }      │
│                                                                    │
│  createSyntheticUserCaveatMessage()                                │
│  └─► 本地命令警告（告诉 AI 忽略后续的本地命令输出）                 │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

> 📁 **完整实现**: `src/utils/messages.ts:355-631`

---

## 4. 消息归一化 (Normalization)

### 4.1 为什么需要归一化？

从 API 收到的 `AssistantMessage` 可能包含多个 ContentBlock：

```typescript
// 原始 API 响应
{
  type: 'assistant',
  message: {
    content: [
      { type: 'text', text: '我来读取这个文件' },
      { type: 'tool_use', id: 'toolu_01', name: 'FileReadTool', input: {...} },
      { type: 'tool_use', id: 'toolu_02', name: 'GrepTool', input: {...} }
    ]
  }
}
```

UI 渲染时，需要将每个 ContentBlock **拆分为独立消息**：

```
┌────────────────────────────────────────────────────────────────────┐
│                        Message Normalization                        │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   原始 AssistantMessage (1条)                                       │
│   ┌────────────────────────────────────────────┐                   │
│   │ content: [TextBlock, ToolUseBlock×2]       │                   │
│   └────────────────────────────────────────────┘                   │
│                         │                                          │
│                         ▼ normalizeMessages()                      │
│                                                                    │
│   归一化后 (3条 NormalizedMessage)                                  │
│   ┌────────────────────────────────────────────┐                   │
│   │ NormalizedAssistantMessage<TextBlock>      │                   │
│   │ content: [{ type: 'text', text: '...' }]   │                   │
│   └────────────────────────────────────────────┘                   │
│   ┌────────────────────────────────────────────┐                   │
│   │ NormalizedAssistantMessage<ToolUseBlock>   │                   │
│   │ content: [{ type: 'tool_use', id: '01' }]  │                   │
│   └────────────────────────────────────────────┘                   │
│   ┌────────────────────────────────────────────┐                   │
│   │ NormalizedAssistantMessage<ToolUseBlock>   │                   │
│   │ content: [{ type: 'tool_use', id: '02' }]  │                   │
│   └────────────────────────────────────────────┘                   │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 4.2 归一化类型定义

```typescript
// 📁 推断自 src/utils/groupToolUses.ts:4-11

// 归一化后的消息类型（每条消息只有一个 ContentBlock）
type NormalizedAssistantMessage<T extends BetaContentBlock = BetaContentBlock> = 
  AssistantMessage & {
    message: {
      content: [T]  // 恰好一个元素
    }
  }

type NormalizedUserMessage = UserMessage & {
  message: {
    content: ContentBlockParam[]
  }
}

type NormalizedMessage = 
  | NormalizedUserMessage 
  | NormalizedAssistantMessage 
  | ProgressMessage 
  | AttachmentMessage 
  | SystemMessage
```

### 4.3 工具调用分组 (Grouped Tool Use)

对于同一条 API 响应中的多个同类型工具调用，UI 可以**分组渲染**：

```
┌────────────────────────────────────────────────────────────────────┐
│                     Tool Use Grouping Logic                         │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   归一化消息 (5条)                    分组后 (3条 RenderableMessage) │
│   ┌──────────────────┐               ┌────────────────────────┐    │
│   │ TextBlock        │ ────────────► │ TextBlock              │    │
│   └──────────────────┘               └────────────────────────┘    │
│   ┌──────────────────┐                                             │
│   │ FileRead #1      │ ─┐             ┌────────────────────────┐   │
│   └──────────────────┘  │             │ GroupedToolUseMessage  │   │
│   ┌──────────────────┐  ├───────────► │ ├── FileRead #1        │   │
│   │ FileRead #2      │  │             │ ├── FileRead #2        │   │
│   └──────────────────┘  │             │ └── FileRead #3        │   │
│   ┌──────────────────┐  │             └────────────────────────┘   │
│   │ FileRead #3      │ ─┘                                          │
│   └──────────────────┘               ┌────────────────────────┐    │
│   ┌──────────────────┐               │ GrepTool (独立)        │    │
│   │ GrepTool         │ ────────────► │ (不同工具不分组)       │    │
│   └──────────────────┘               └────────────────────────┘    │
│                                                                    │
│   分组条件：                                                        │
│   1. 同一 message.id (来自同一 API 响应)                           │
│   2. 同一 tool name                                                │
│   3. 工具支持分组渲染 (renderGroupedToolUse 方法)                  │
│   4. 数量 >= 2                                                     │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

> 📁 **分组实现**: `src/utils/groupToolUses.ts:54-100`

---

## 5. 消息 ID 与追踪

### 5.1 ID 体系设计

```
┌────────────────────────────────────────────────────────────────────┐
│                        Message ID System                            │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  uuid (Message.uuid)                                               │
│  └─► 客户端生成的 UUID，全局唯一                                   │
│  └─► 用于：日志持久化、消息引用、断点续传                          │
│                                                                    │
│  message.id (AssistantMessage.message.id)                          │
│  └─► API 返回的响应 ID (msg_xxx...)                                │
│  └─► 用于：分组、去重、API 关联                                    │
│                                                                    │
│  tool_use_id (ToolUseBlock.id)                                     │
│  └─► 工具调用唯一标识 (toolu_xxx...)                               │
│  └─► 用于：tool_result 匹配、进度追踪                              │
│                                                                    │
│  requestId (AssistantMessage.requestId)                            │
│  └─► 可选，用于请求追踪和调试                                       │
│                                                                    │
│  shortMessageId (6字符 base36)                                     │
│  └─► 从 UUID 派生的短 ID                                           │
│  └─► 用于：Snip 压缩引用 [id:abc123]                               │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 5.2 Short Message ID 推导

```typescript
// 📁 src/utils/messages.ts:200-205

export function deriveShortMessageId(uuid: string): string {
  // 取 UUID 前 10 个 hex 字符（跳过连字符）
  const hex = uuid.replace(/-/g, '').slice(0, 10)
  // 转换为 base36，取前 6 位
  return parseInt(hex, 16).toString(36).slice(0, 6)
}

// 示例：
// uuid: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
// hex: "a1b2c3d4e5" (前10位)
// base36: "1k9xz7m" → "1k9xz7" (前6位)
```

---

## 6. 特殊消息处理

### 6.1 合成消息 (Synthetic Messages)

某些消息不是来自 AI，而是系统合成的：

```typescript
// 📁 src/utils/messages.ts:207-247

// 用户中断
export const INTERRUPT_MESSAGE = '[Request interrupted by user]'
export const INTERRUPT_MESSAGE_FOR_TOOL_USE = 
  '[Request interrupted by user for tool use]'

// 取消操作
export const CANCEL_MESSAGE =
  "The user doesn't want to take this action right now. " +
  "STOP what you are doing and wait for the user to tell you how to proceed."

// 权限拒绝
export const REJECT_MESSAGE =
  "The user doesn't want to proceed with this tool use. " +
  "The tool use was rejected. STOP and wait for the user."

// 合成消息集合
export const SYNTHETIC_MESSAGES = new Set([
  INTERRUPT_MESSAGE,
  INTERRUPT_MESSAGE_FOR_TOOL_USE,
  CANCEL_MESSAGE,
  REJECT_MESSAGE,
  NO_RESPONSE_REQUESTED,
])

// 判断是否合成消息
export function isSyntheticMessage(message: Message): boolean {
  return (
    message.type !== 'progress' &&
    message.type !== 'attachment' &&
    message.type !== 'system' &&
    Array.isArray(message.message.content) &&
    message.message.content[0]?.type === 'text' &&
    SYNTHETIC_MESSAGES.has(message.message.content[0].text)
  )
}
```

### 6.2 权限拒绝的分类器消息

```typescript
// 📁 src/utils/messages.ts:267-282

// Auto 模式下分类器拒绝
export function buildYoloRejectionMessage(reason: string): string {
  const prefix = 'Permission for this action has been denied. Reason: '
  const ruleHint = 'To allow this in the future, add a permission rule.'
  
  return (
    `${prefix}${reason}. ` +
    `If you have other tasks that don't depend on this, continue. ` +
    `${DENIAL_WORKAROUND_GUIDANCE} ` +
    ruleHint
  )
}

// 工具被拒绝的通用消息
export function AUTO_REJECT_MESSAGE(toolName: string): string {
  return `Permission to use ${toolName} has been denied. ` +
         `${DENIAL_WORKAROUND_GUIDANCE}`
}
```

### 6.3 消息来源追踪 (MessageOrigin)

```
┌────────────────────────────────────────────────────────────────────┐
│                        Message Origin Types                         │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  MessageOrigin = {                                                 │
│    type: 'keyboard' | 'paste' | 'command' | 'hook' |              │
│          'api' | 'teammate' | 'task' | 'continuation'             │
│    commandName?: string     // 如果是 /command                     │
│    hookName?: string        // 如果是 Hook 注入                    │
│    teammateId?: string      // 如果是队友消息                      │
│  }                                                                 │
│                                                                    │
│  用途：                                                            │
│  1. UI 显示不同的消息标签                                          │
│  2. 分析用户输入来源                                               │
│  3. 权限判断（来自 Hook 的消息可能有特殊权限）                      │
│  4. 日志和遥测                                                     │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 7. Tool Result 配对保证

### 7.1 严格配对规则

Anthropic API **要求**每个 `tool_use` 块必须有对应的 `tool_result`：

```
┌────────────────────────────────────────────────────────────────────┐
│                   Tool Use / Result Pairing                         │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   正确的消息序列：                                                  │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ 1. UserMessage (用户提问)                                    │ │
│   │ 2. AssistantMessage [text + tool_use(id='001')]             │ │
│   │ 3. UserMessage [tool_result(tool_use_id='001')]  ← 必须配对 │ │
│   │ 4. AssistantMessage [text]                                   │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│   错误的序列（API 会报错）：                                        │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ 1. UserMessage                                               │ │
│   │ 2. AssistantMessage [tool_use(id='001')]                    │ │
│   │ 3. AssistantMessage [text]  ← ❌ 缺少 tool_result!          │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 7.2 缺失结果的补全

当工具执行被中断时，系统会自动补全缺失的 `tool_result`：

```typescript
// 📁 src/query.ts:123-149

function* yieldMissingToolResultBlocks(
  assistantMessages: AssistantMessage[],
  errorMessage: string,
) {
  for (const assistantMessage of assistantMessages) {
    // 提取所有 tool_use 块
    const toolUseBlocks = assistantMessage.message.content.filter(
      content => content.type === 'tool_use',
    ) as ToolUseBlock[]

    // 为每个 tool_use 生成中断消息
    for (const toolUse of toolUseBlocks) {
      yield createUserMessage({
        content: [
          {
            type: 'tool_result',
            content: errorMessage,
            is_error: true,
            tool_use_id: toolUse.id,  // 必须与 tool_use.id 匹配
          },
        ],
        toolUseResult: errorMessage,
        sourceToolAssistantUUID: assistantMessage.uuid,
      })
    }
  }
}
```

### 7.3 合成占位符

当 tool_result 完全缺失时，使用占位符：

```typescript
// 📁 src/utils/messages.ts:246-247

export const SYNTHETIC_TOOL_RESULT_PLACEHOLDER =
  '[Tool result missing due to internal error]'
```

---

## 8. 日志持久化消息格式

### 8.1 SerializedMessage

消息持久化到 JSONL 日志时，会添加元数据：

```typescript
// 📁 src/types/logs.ts:8-17

export type SerializedMessage = Message & {
  cwd: string           // 当前工作目录
  userType: string      // 用户类型
  entrypoint?: string   // 入口点 (cli/sdk-ts/sdk-py)
  sessionId: string     // 会话 ID
  timestamp: string     // ISO 时间戳
  version: string       // 版本号
  gitBranch?: string    // Git 分支
  slug?: string         // 会话 slug
}
```

### 8.2 TranscriptMessage (完整日志消息)

```typescript
// 📁 src/types/logs.ts:221-231

export type TranscriptMessage = SerializedMessage & {
  parentUuid: UUID | null         // 父消息 UUID（树状结构）
  logicalParentUuid?: UUID | null // 逻辑父消息（用于断点恢复）
  isSidechain: boolean            // 是否是子代理的消息
  gitBranch?: string              // Git 分支
  agentId?: string                // 代理 ID（子代理场景）
  teamName?: string               // 团队名称（Swarm 场景）
  agentName?: string              // 代理名称
  promptId?: string               // OpenTelemetry 追踪
}
```

### 8.3 会话元数据消息

```
┌────────────────────────────────────────────────────────────────────┐
│                    Session Metadata Messages                        │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  SummaryMessage          ← 会话摘要                                │
│  CustomTitleMessage      ← 用户自定义标题                          │
│  AiTitleMessage          ← AI 生成的标题                           │
│  LastPromptMessage       ← 最后一条用户输入                         │
│  TaskSummaryMessage      ← 当前任务摘要（用于 claude ps）          │
│  TagMessage              ← 会话标签                                │
│  PRLinkMessage           ← 关联的 GitHub PR                        │
│  AgentNameMessage        ← 代理名称                                │
│  AgentColorMessage       ← 代理颜色                                │
│  WorktreeStateEntry      ← Worktree 状态                          │
│  FileHistorySnapshotMessage ← 文件历史快照                         │
│  AttributionSnapshotMessage ← 贡献归属快照                         │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

> 📁 **完整定义**: `src/types/logs.ts:55-220`

---

## 9. 消息流转总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Complete Message Flow                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   用户输入                                                           │
│   ┌─────────┐                                                        │
│   │ 键盘/   │                                                        │
│   │ 粘贴    │                                                        │
│   └────┬────┘                                                        │
│        │                                                             │
│        ▼                                                             │
│   createUserMessage() ──► UserMessage                                │
│        │                    │                                        │
│        │                    ├──► 追加到 messages[]                   │
│        │                    └──► 持久化到 JSONL                      │
│        ▼                                                             │
│   normalizeMessagesForAPI() ──► API 格式消息                         │
│        │                                                             │
│        ▼                                                             │
│   ┌─────────────────────────────────────────┐                       │
│   │           Anthropic API                 │                       │
│   └────────────────────┬────────────────────┘                       │
│                        │                                             │
│                        ▼ 流式响应                                    │
│   AssistantMessage (BetaMessage)                                     │
│        │                                                             │
│        ├──► 追加到 messages[]                                        │
│        ├──► 持久化到 JSONL                                           │
│        │                                                             │
│        ▼ 归一化                                                      │
│   normalizeMessages() ──► NormalizedMessage[]                        │
│        │                                                             │
│        ├──► ProgressMessage (进度)                                   │
│        ├──► AttachmentMessage (附件)                                 │
│        │                                                             │
│        ▼ 分组                                                        │
│   applyGrouping() ──► RenderableMessage[]                            │
│        │                                                             │
│        ▼                                                             │
│   ┌─────────────────────────────────────────┐                       │
│   │              UI 渲染                    │                       │
│   │  Messages.tsx → Message.tsx             │                       │
│   └─────────────────────────────────────────┘                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. 关键文件索引

| 文件路径 | 核心内容 | 行数参考 |
|---------|---------|---------|
| `src/utils/messages.ts` | 消息创建工厂、归一化、工具函数 | 1-1200+ |
| `src/types/logs.ts` | 持久化消息类型定义 | 1-250 |
| `src/query.ts` | 消息类型导入、tool_result 补全 | 30-150 |
| `src/utils/groupToolUses.ts` | 工具调用分组逻辑 | 1-100 |
| `src/components/Messages.tsx` | UI 消息列表渲染 | - |
| `src/components/Message.tsx` | 单条消息渲染 | - |
| `src/utils/sessionStorage.ts` | 会话消息持久化 | - |

---

## 🎓 本章小结

### 核心要点

1. **消息是通信协议**：所有组件通过统一的消息格式交流
2. **tool_use/tool_result 配对**：Claude API 要求严格匹配，缺失会导致错误
3. **归一化是渲染的前置步骤**：多 Block 拆分为单 Block 方便逐个渲染
4. **持久化支持恢复**：JSONL 格式让会话可以随时恢复

### 可迁移的设计原则

| 原则 | 说明 | 适用场景 |
|------|------|---------|
| 统一消息格式 | 所有组件用同一种数据结构交流 | 多组件系统 |
| 类型区分用途 | 不同消息类型有不同处理逻辑 | 需要差异化处理的场景 |
| 配对验证 | 请求和响应必须匹配 | API 调用、事务处理 |
| 归一化预处理 | 复杂结构拆分为简单单元 | UI 渲染、批量处理 |

### 🧠 为什么这么多消息类型？

**问题：为什么不用一个通用 Message 类型？**
- 不同消息有不同的处理逻辑（发 API、渲染、持久化）
- 类型系统帮助编译器检查错误
- 每种类型可以有专属字段（如 ProgressMessage 有进度百分比）

**问题：为什么 tool_result 要和 tool_use 严格配对？**
- Claude API 的设计：每个工具调用必须有结果
- 方便 AI 理解"这个工具返回了什么"
- 如果不配对，AI 会不知道工具是成功还是失败

### 🤔 思考题

1. **如果你要添加一个"音频消息"类型**（用户发送语音），需要修改哪些地方？
   - 提示：考虑类型定义、序列化、UI 渲染

2. **为什么 TombstoneMessage 不发送给 API 也不渲染？** 它有什么作用？
   - 提示：考虑消息历史的完整性和撤销操作

3. **归一化为什么是"多 Block 拆单 Block"**，而不是反过来合并？
   - 提示：考虑 React 渲染的粒度和性能

4. **如果消息历史非常长**，有什么策略可以优化内存占用？
   - 提示：考虑压缩、分页、摘要

---

接下来，我们将探索 Prompt 工程实践：

👉 **继续阅读**: [05-Prompt工程实践.md](/claudecode/05-prompt-engineering)
