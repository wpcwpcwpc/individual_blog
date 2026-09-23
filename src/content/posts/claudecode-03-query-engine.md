---
title: "QueryEngine深度解析"
summary: "QueryEngine 是 Agent 的大脑，像交响乐指挥一样协调 AI 和工具的交互。"
publishedAt: 2026-05-21
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 3
seriesGroup: 核心架构
readingMinutes: 60
weight: 3
source: "03-QueryEngine深度解析.md"
sourceSha256: 85f8db4d62c2
---
> **一句话理解**：QueryEngine 是 Agent 的大脑，像交响乐指挥一样协调 AI 和工具的交互。

> **阅读时长**：60分钟  
> **前置阅读**：[02-核心数据流与生命周期](/claudecode/02-data-flow-lifecycle)  
> **重要程度**：⭐️⭐️⭐️（核心中的核心）  
> **核心价值**：深入理解AI交互引擎的内部实现，掌握消息流编排的核心机制
> **你将获得**：
> - 理解 Agent 循环的具体实现
> - 掌握状态管理和依赖注入模式
> - 能够自己设计类似的交互引擎

---

## 🎯 先用 2 分钟建立直觉

**想象你在主持一个技术讨论会**：

```
你（主持人）           专家（Claude）           助理团队（工具）
     │                      │                      │
     │  "请分析这个 bug"     │                      │
     │─────────────────────▶│                      │
     │                      │                      │
     │                      │  "我需要看下代码"    │
     │                      │─────────────────────▶│
     │                      │                      │
     │   （你在协调）        │◀─────────────────────│
     │                      │   [返回代码内容]      │
     │                      │                      │
     │                      │  "还需要看日志"      │
     │                      │─────────────────────▶│
     │                      │                      │
     │                      │◀─────────────────────│
     │                      │   [返回日志内容]      │
     │                      │                      │
     │◀─────────────────────│                      │
     │  "bug 在第 42 行..."  │                      │
```

**QueryEngine 就是这个主持人**。它的工作是：
1. 把用户的问题转达给专家（Claude）
2. 专家说"我需要查资料"时，帮他调用工具
3. 把工具结果告诉专家
4. 不断循环，直到专家说"我回答完了"

**💡 关键洞察**：QueryEngine 自己不回答问题，也不执行工具。它只做**协调**。
这种"只做协调"的设计叫做 **编排模式（Orchestration）**，是构建复杂系统的核心思想。

---

## 📖 本章导航

- [QueryEngine架构概述](#queryengine架构概述)
- [配置与初始化](#配置与初始化)
- [消息流编排引擎](#消息流编排引擎)
- [状态管理机制](#状态管理机制)
- [错误处理与重试](#错误处理与重试)
- [性能优化设计](#性能优化设计)
- [关键要点总结](#关键要点总结)

---

## QueryEngine架构概述

### 核心定位

QueryEngine 是整个 Claude Code 的"大脑"，负责编排 AI 与工具之间的交互循环。

```
┌─────────────────────────────────────────────────────────────┐
│                      QueryEngine                             │
│                                                              │
│   "AI交互的编排引擎，系统的核心控制器"                        │
│                                                              │
│   ┌─────────────────────────────────────────────────────┐   │
│   │                    职责边界                          │   │
│   │                                                      │   │
│   │   ✅ 消息流编排        ✅ 工具调用协调               │   │
│   │   ✅ 状态管理          ✅ 错误处理与重试              │   │
│   │   ✅ 上下文压缩        ✅ Stream事件分发              │   │
│   │                                                      │   │
│   │   ❌ 具体工具实现      ❌ UI渲染                     │   │
│   │   ❌ 网络通信细节      ❌ 持久化存储                  │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 架构分层图

```
┌─────────────────────────────────────────────────────────────┐
│                     调用方 (REPL/App)                        │
└───────────────────────────┬─────────────────────────────────┘
                            │ submitMessage()
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                     QueryEngine                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                   query() 主循环                       │  │
│  │                                                        │  │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │  │
│  │   │   State     │  │   Config    │  │   Deps      │   │  │
│  │   │   状态管理   │  │   配置      │  │   依赖注入   │   │  │
│  │   └─────────────┘  └─────────────┘  └─────────────┘   │  │
│  │                                                        │  │
│  │   ┌────────────────────────────────────────────────┐  │  │
│  │   │              Loop Iteration                     │  │  │
│  │   │                                                 │  │  │
│  │   │  prepareMessages → callAPI → processStream     │  │  │
│  │   │        ↓                          ↓            │  │  │
│  │   │  compaction ←── toolExecution ←── parse        │  │  │
│  │   │                                                 │  │  │
│  │   └────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌─────────┐   ┌─────────┐   ┌─────────┐
        │ Claude  │   │  Tool   │   │ Compact │
        │  API    │   │ System  │   │ Service │
        └─────────┘   └─────────┘   └─────────┘
```

### 🧠 为什么需要这个"中间人"？

你可能会问：为什么不让 Claude 直接调用工具？

**问题 1：Claude API 不知道你本地有什么工具**
- Claude 运行在云端，它不知道你的电脑上有哪些文件、哪些命令可用
- 必须有人告诉它"你可以用这些工具"，并在它要用时帮它执行

**问题 2：工具执行可能失败**
- 网络超时、文件不存在、权限不够... 这些错误需要处理，可能要重试
- 如果让 Claude 处理这些，对话会变得很乱

**问题 3：有些操作需要用户确认**
- "删除这个文件？" —— 你肯定希望 Claude 先问你一声
- 这个权限确认逻辑必须在 Claude 之外

**问题 4：执行过程需要实时反馈**
- 用户想看到"正在执行..."，而不是干等
- 这需要一个中间层来分发进度事件

**所以**：QueryEngine 作为"中间人"是必要的，不是过度设计。

**💡 设计原则**：当系统涉及多个组件的协调时，引入一个专门的"编排器"往往比让组件直接交互更清晰。

---

### 核心组件关系

```
QueryEngine
    │
    ├─→ query()              # 核心查询函数
    │     │
    │     ├─→ queryLoop()    # 主循环实现
    │     │
    │     └─→ State          # 循环状态
    │           ├─ messages
    │           ├─ toolUseContext
    │           ├─ autoCompactTracking
    │           └─ ...
    │
    ├─→ QueryConfig          # 不可变配置
    │     ├─ systemPrompt
    │     ├─ userContext
    │     └─ ...
    │
    └─→ QueryDeps            # 依赖注入
          ├─ callAPI
          ├─ microcompact
          ├─ autocompact
          └─ uuid
```

**📂 完整代码**：`restored-src/src/query.ts:180-300`

---

## 配置与初始化

### QueryParams 参数结构

```typescript
export type QueryParams = {
  // 核心消息和上下文
  messages: Message[];                    // 消息历史
  systemPrompt: SystemPrompt;             // 系统提示
  userContext: { [k: string]: string };   // 用户上下文
  systemContext: { [k: string]: string }; // 系统上下文
  
  // 工具和权限
  canUseTool: CanUseToolFn;              // 工具权限检查
  toolUseContext: ToolUseContext;         // 工具执行上下文
  
  // 可选配置
  fallbackModel?: string;                 // 降级模型
  querySource: QuerySource;               // 查询来源
  maxOutputTokensOverride?: number;       // 最大输出token
  maxTurns?: number;                      // 最大轮次
  skipCacheWrite?: boolean;               // 跳过缓存写入
  taskBudget?: { total: number };         // 任务token预算
  
  // 依赖注入
  deps?: QueryDeps;                       // 外部依赖
};
```

**📂 完整代码**：`restored-src/src/query.ts:180-200`

### 配置结构图

```
QueryParams
    │
    ├─── 消息层 ───────────────────────────────────┐
    │    messages[]           消息历史               │
    │    systemPrompt         系统提示（不可变）     │
    │    userContext          用户上下文注入         │
    │    systemContext        系统上下文注入         │
    │                                               │
    ├─── 工具层 ───────────────────────────────────┤
    │    toolUseContext       工具执行上下文         │
    │    ├─ tools             可用工具列表          │
    │    ├─ abortController   中止控制器            │
    │    ├─ queryTracking     查询链追踪            │
    │    └─ options           工具选项              │
    │    canUseTool           权限检查函数           │
    │                                               │
    ├─── 控制层 ───────────────────────────────────┤
    │    querySource          查询来源标识           │
    │    maxTurns             最大循环轮次           │
    │    maxOutputTokensOverride  输出token限制     │
    │    taskBudget           任务预算              │
    │                                               │
    └─── 依赖层 ───────────────────────────────────┘
         deps                 可注入的外部依赖
         ├─ callAPI           API调用函数
         ├─ microcompact      微压缩函数
         ├─ autocompact       自动压缩函数
         └─ uuid              UUID生成函数
```

### State 循环状态

```typescript
// 可变状态，在循环迭代间传递
type State = {
  messages: Message[];                    // 当前消息列表
  toolUseContext: ToolUseContext;         // 工具上下文
  autoCompactTracking: AutoCompactTrackingState | undefined;
  maxOutputTokensRecoveryCount: number;   // 重试计数
  hasAttemptedReactiveCompact: boolean;   // 是否已尝试压缩
  maxOutputTokensOverride: number | undefined;
  pendingToolUseSummary: Promise<...> | undefined;
  stopHookActive: boolean | undefined;
  turnCount: number;                      // 当前轮次
  transition: Continue | undefined;       // 上次迭代的继续原因
};
```

### 依赖注入设计

```typescript
// 生产环境依赖
export const productionDeps = (): QueryDeps => ({
  callAPI: queryModelWithStreaming,       // 真实API调用
  microcompact: applyMicrocompact,        // 微压缩
  autocompact: autoCompactIfNeeded,       // 自动压缩
  uuid: randomUUID,                       // UUID生成
});

// 测试环境可注入mock
const testDeps: QueryDeps = {
  callAPI: mockCallAPI,
  microcompact: noopCompact,
  // ...
};
```

**设计亮点**：
- ✅ **依赖注入**：便于测试和mock
- ✅ **状态分离**：不可变配置 vs 可变状态
- ✅ **类型安全**：完整的TypeScript类型定义

---

## 消息流编排引擎

### queryLoop 主循环

这是整个系统最核心的代码，理解它就理解了 Claude Code 的运作原理：

```
                        queryLoop() 主循环
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      while (true)                            │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Step 1: 准备消息                                        │ │
│  │                                                         │ │
│  │  messagesForQuery = getMessagesAfterCompactBoundary()  │ │
│  │  messagesForQuery = applyToolResultBudget()            │ │
│  │  messagesForQuery = snipCompactIfNeeded()              │ │
│  │  messagesForQuery = microcompact()                     │ │
│  │  messagesForQuery = autocompact()                      │ │
│  │                                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                              │                               │
│                              ▼                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Step 2: 调用 Claude API (Streaming)                     │ │
│  │                                                         │ │
│  │  for await (message of callAPI({                       │ │
│  │    messages: messagesForQuery,                         │ │
│  │    system: fullSystemPrompt,                           │ │
│  │    tools: toolDefinitions,                             │ │
│  │    ...                                                 │ │
│  │  })) {                                                 │ │
│  │    yield message;  // 实时推送给调用方                   │ │
│  │  }                                                      │ │
│  │                                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                              │                               │
│                              ▼                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Step 3: 处理工具调用                                    │ │
│  │                                                         │ │
│  │  if (hasToolUse) {                                     │ │
│  │    for (toolBlock of toolUseBlocks) {                  │ │
│  │      streamingToolExecutor.addTool(toolBlock)          │ │
│  │    }                                                    │ │
│  │    for (result of executor.getCompletedResults()) {    │ │
│  │      yield result.message;                             │ │
│  │      toolResults.push(result);                         │ │
│  │    }                                                    │ │
│  │  }                                                      │ │
│  │                                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                              │                               │
│                              ▼                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Step 4: 决定是否继续                                    │ │
│  │                                                         │ │
│  │  if (needsFollowUp && toolResults.length > 0) {        │ │
│  │    state = { ...state, messages: [..., toolResults] }  │ │
│  │    continue;  // 继续循环，让AI处理工具结果              │ │
│  │  }                                                      │ │
│  │                                                         │ │
│  │  if (stopReason === 'end_turn') {                      │ │
│  │    return { reason: 'end_turn' };  // 正常结束          │ │
│  │  }                                                      │ │
│  │                                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 核心代码实现

```typescript
async function* queryLoop(
  params: QueryParams,
  consumedCommandUuids: string[],
): AsyncGenerator<StreamEvent, Terminal> {
  
  // 不可变参数
  const { systemPrompt, userContext, systemContext, canUseTool } = params;
  const deps = params.deps ?? productionDeps();
  
  // 可变状态
  let state: State = {
    messages: params.messages,
    toolUseContext: params.toolUseContext,
    turnCount: 1,
    // ...
  };

  while (true) {
    let { toolUseContext } = state;
    const { messages, turnCount } = state;
    
    // Step 1: 准备消息（压缩、裁剪）
    let messagesForQuery = getMessagesAfterCompactBoundary(messages);
    messagesForQuery = await applyToolResultBudget(messagesForQuery, ...);
    
    // Step 2: 调用API
    for await (const message of deps.callAPI({
      messages: normalizeMessagesForAPI(messagesForQuery),
      system: fullSystemPrompt,
      tools: config.tools,
    })) {
      yield message;  // 实时yield给调用方
      
      if (message.type === 'assistant') {
        assistantMessages.push(message);
        // 提取工具调用
        const toolBlocks = message.message.content
          .filter(c => c.type === 'tool_use');
        if (toolBlocks.length > 0) {
          needsFollowUp = true;
        }
      }
    }
    
    // Step 3: 执行工具（如果有）
    if (streamingToolExecutor) {
      for (const result of streamingToolExecutor.getCompletedResults()) {
        yield result.message;
        toolResults.push(result);
      }
    }
    
    // Step 4: 决定继续或终止
    if (needsFollowUp && toolResults.length > 0) {
      state = {
        ...state,
        messages: [...messages, ...assistantMessages, ...toolResults],
        turnCount: turnCount + 1,
      };
      continue;  // 继续循环
    }
    
    return { reason: 'end_turn' };  // 结束
  }
}
```

**📂 完整代码**：`restored-src/src/query.ts:241-500`

### 🗣️ 大白话说这段代码

上面的代码看起来很长，但核心逻辑其实很简单：

```
while (true) {
  1. 整理一下要发给 Claude 的消息（太长就压缩）
  2. 发给 Claude，一边发一边把回复推给 UI
  3. Claude 说"我要用工具"？帮它执行，把结果塞回消息列表
  4. Claude 说"我回答完了"？退出循环
  5. 否则继续循环
}
```

**就这么简单。** 其他代码都是在处理边界情况：
- 消息太长怎么办？→ 压缩
- API 出错怎么办？→ 重试
- 用户中途取消怎么办？→ 检查 AbortSignal
- token 预算用完怎么办？→ 提前结束

**🗣️ 阶段小结**：到这里，你已经理解了 QueryEngine 的核心循环。
它本质上是一个 `while (true)` 循环，不断地：发消息 → 收回复 → 执行工具 → 发消息...
直到 AI 说"我完成了"。

---

### 消息准备流程

在发送给 API 之前，消息经过多层处理：

```
原始消息列表 (messages)
         │
         ▼
┌─────────────────────────────────────┐
│ 1. getMessagesAfterCompactBoundary │
│    跳过已压缩的历史消息               │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ 2. applyToolResultBudget           │
│    限制工具结果的大小                 │
│    超大结果替换为占位符               │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ 3. snipCompactIfNeeded             │
│    Snip压缩：移除中间不重要的消息     │
│    保留头部和尾部                    │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ 4. microcompact                    │
│    微压缩：合并小工具结果             │
│    减少消息数量                      │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ 5. autocompact                     │
│    自动压缩：上下文过长时触发         │
│    生成摘要替代原始消息              │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ 6. normalizeMessagesForAPI         │
│    格式化为API期望的格式             │
└─────────────────────────────────────┘
                 │
                 ▼
          发送给 Claude API
```

### StreamingToolExecutor

工具执行采用流式处理器，支持并发执行：

```typescript
class StreamingToolExecutor {
  private tools: Tools;
  private pendingTools: Map<string, ToolExecution>;
  private completedResults: ToolResult[];
  
  // 添加工具到执行队列
  addTool(toolBlock: ToolUseBlock, message: AssistantMessage) {
    const tool = findToolByName(this.tools, toolBlock.name);
    
    // 检查并发安全性
    if (tool.isConcurrencySafe) {
      // 立即开始执行（并行）
      this.startExecution(toolBlock, tool);
    } else {
      // 加入队列等待（串行）
      this.pendingTools.set(toolBlock.id, { toolBlock, tool });
    }
  }
  
  // 获取已完成的结果
  *getCompletedResults(): Generator<ToolResult> {
    while (this.completedResults.length > 0) {
      yield this.completedResults.shift()!;
    }
  }
  
  // 丢弃所有待处理（用于错误恢复）
  discard() {
    this.pendingTools.clear();
    this.completedResults.length = 0;
  }
}
```

**📂 完整代码**：`restored-src/src/services/tools/StreamingToolExecutor.ts`

---

## 状态管理机制

### State 生命周期

```
Query开始
    │
    ▼
┌─────────────────────────────────────┐
│ 初始化 State                        │
│                                      │
│ state = {                           │
│   messages: params.messages,        │
│   toolUseContext: params.context,   │
│   turnCount: 1,                     │
│   maxOutputTokensRecoveryCount: 0,  │
│   hasAttemptedReactiveCompact: false│
│ }                                   │
└────────────────┬────────────────────┘
                 │
    ┌────────────┴────────────┐
    │      Loop Iteration      │
    │                          │
    │  ┌────────────────────┐ │
    │  │ 读取 state         │ │
    │  │ 执行操作           │ │
    │  │ 更新 state         │ │
    │  └────────────────────┘ │
    │            │             │
    │      [continue?]         │
    │       ↙     ↘           │
    │    Yes      No          │
    │     │        │          │
    │     └────────┴──────────┘
    │                          │
    └──────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│ 返回 Terminal                       │
│ { reason: 'end_turn' | 'error' }   │
└─────────────────────────────────────┘
```

### Continue 条件

循环继续的条件有多种：

```typescript
// Continue 类型定义
type Continue = 
  | { type: 'tool_results' }           // 有工具结果需要处理
  | { type: 'prompt_too_long' }        // 触发压缩后重试
  | { type: 'max_output_tokens' }      // 输出截断后重试
  | { type: 'reactive_compact' }       // 响应式压缩后重试
  | { type: 'model_fallback' }         // 模型降级后重试
  | { type: 'context_collapse' };      // 上下文折叠后重试
```

### 状态更新模式

```typescript
// 状态更新采用不可变模式
// 每次 continue 创建新的 state 对象

// 工具结果后继续
state = {
  ...state,
  messages: [...messages, ...assistantMessages, ...toolResults],
  toolUseContext: updatedContext,
  turnCount: turnCount + 1,
  transition: { type: 'tool_results' },
};
continue;

// 压缩后继续
state = {
  ...state,
  messages: compactionResult.summaryMessages,
  hasAttemptedReactiveCompact: true,
  transition: { type: 'reactive_compact' },
};
continue;
```

### autoCompactTracking

自动压缩追踪状态：

```typescript
type AutoCompactTrackingState = {
  inputTokens: number;         // 输入token数
  outputTokens: number;        // 输出token数
  cacheReadTokens: number;     // 缓存读取token
  cacheCreationTokens: number; // 缓存创建token
  warningState: 'none' | 'warning' | 'critical';
  consecutiveFailures: number; // 连续失败次数
};
```

---

## 错误处理与重试

### 错误分类

```
错误类型
    │
    ├─── 可恢复错误 ─────────────────────────────────┐
    │    │                                           │
    │    ├─ PromptTooLongError                      │
    │    │  → 触发压缩，重试                         │
    │    │                                           │
    │    ├─ MaxOutputTokensError                    │
    │    │  → 增加token限制，重试（最多3次）         │
    │    │                                           │
    │    ├─ FallbackTriggeredError                  │
    │    │  → 切换到降级模型，重试                   │
    │    │                                           │
    │    └─ MediaSizeError                          │
    │       → 移除大文件，重试                       │
    │                                                │
    └─── 不可恢复错误 ───────────────────────────────┘
         │
         ├─ AuthenticationError
         │  → 直接返回错误
         │
         ├─ RateLimitError
         │  → 返回错误（由上层处理）
         │
         └─ NetworkError
            → 返回错误
```

### PromptTooLong 处理

```typescript
// 检测是否为 prompt_too_long 错误
if (reactiveCompact?.isWithheldPromptTooLong(message)) {
  // 1. 暂存错误消息（不立即yield）
  withheld = true;
}

// 循环结束后检查
if (isPromptTooLongMessage(lastMessage)) {
  // 2. 尝试响应式压缩
  if (!hasAttemptedReactiveCompact) {
    const compactResult = await reactiveCompact.compactForPromptTooLong(
      messages,
      toolUseContext
    );
    
    if (compactResult.success) {
      // 3. 压缩成功，继续循环
      state = {
        ...state,
        messages: compactResult.messages,
        hasAttemptedReactiveCompact: true,
        transition: { type: 'reactive_compact' },
      };
      continue;
    }
  }
  
  // 4. 压缩失败，yield错误消息
  yield lastMessage;
  return { reason: 'prompt_too_long' };
}
```

### MaxOutputTokens 恢复

```typescript
const MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3;

// 检测截断错误
if (isWithheldMaxOutputTokens(message)) {
  if (maxOutputTokensRecoveryCount < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT) {
    // 增加输出限制，重试
    state = {
      ...state,
      maxOutputTokensOverride: ESCALATED_MAX_TOKENS,
      maxOutputTokensRecoveryCount: maxOutputTokensRecoveryCount + 1,
      transition: { type: 'max_output_tokens' },
    };
    continue;
  }
  
  // 超过重试次数，放弃
  yield message;
  return { reason: 'max_output_tokens' };
}
```

### 模型降级（Fallback）

```typescript
try {
  for await (const message of callAPI(...)) {
    // ...
  }
} catch (error) {
  if (error instanceof FallbackTriggeredError && fallbackModel) {
    // 1. 清理已产生的消息
    yield* yieldMissingToolResultBlocks(assistantMessages, 'Model fallback');
    assistantMessages.length = 0;
    
    // 2. 切换模型
    currentModel = fallbackModel;
    toolUseContext.options.mainLoopModel = fallbackModel;
    
    // 3. 通知用户
    yield createSystemMessage(
      `Switched to ${fallbackModel} due to high demand`,
      'warning'
    );
    
    // 4. 重试
    continue;
  }
  
  throw error;  // 其他错误直接抛出
}
```

### 错误处理流程图

```
API调用
    │
    ▼
┌─────────────────────────────────────┐
│          try { ... }                │
└────────────────┬────────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
     成功               异常
        │                 │
        ▼                 ▼
┌─────────────┐   ┌─────────────────────────┐
│ 正常处理    │   │ catch (error)           │
│             │   │                         │
│             │   │ switch(error.type) {    │
│             │   │   case 'fallback':      │
│             │   │     → 切换模型，continue │
│             │   │                         │
│             │   │   case 'prompt_long':   │
│             │   │     → 压缩，continue     │
│             │   │                         │
│             │   │   case 'rate_limit':    │
│             │   │     → yield error       │
│             │   │                         │
│             │   │   default:              │
│             │   │     → throw error       │
│             │   │ }                       │
│             │   │                         │
└─────────────┘   └─────────────────────────┘
```

---

## 性能优化设计

### 1. 消息压缩策略

```
┌─────────────────────────────────────────────────────────────┐
│                    压缩策略层次                              │
│                                                              │
│   Layer 1: Snip Compact (条件裁剪)                          │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ 移除中间不重要的消息，保留头尾                         │   │
│   │ 触发条件：消息数量超过阈值                             │   │
│   │ 成本：零（纯客户端操作）                               │   │
│   └─────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│   Layer 2: Micro Compact (微压缩)                           │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ 合并小的工具结果消息                                   │   │
│   │ 触发条件：存在可合并的小消息                           │   │
│   │ 成本：低（客户端合并）                                 │   │
│   └─────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│   Layer 3: Auto Compact (自动压缩)                          │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ 调用AI生成摘要替代原始消息                             │   │
│   │ 触发条件：token数超过上下文窗口80%                     │   │
│   │ 成本：高（需要额外API调用）                            │   │
│   └─────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│   Layer 4: Reactive Compact (响应式压缩)                    │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ prompt_too_long 错误后的紧急压缩                       │   │
│   │ 触发条件：API返回上下文过长错误                        │   │
│   │ 成本：高（紧急压缩 + 重试）                            │   │
│   └─────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 2. Token 预算管理

```typescript
// 工具结果大小限制
messagesForQuery = await applyToolResultBudget(
  messagesForQuery,
  toolUseContext.contentReplacementState,
  persistReplacements ? records => recordContentReplacement(records) : undefined,
  new Set(toolsWithUnlimitedResults.map(t => t.name)),
);

// 超大工具结果替换为占位符
// 例如：大文件内容 → "[Content truncated: 50KB]"
```

### 3. 并发工具执行

```typescript
// 并发安全的工具可以并行执行
if (tool.isConcurrencySafe) {
  // FileReadTool, GrepTool 等只读工具
  // 并行执行，提高效率
  Promise.all([tool1.call(), tool2.call(), tool3.call()]);
} else {
  // FileEditTool, BashTool 等写操作
  // 串行执行，保证一致性
  await tool1.call();
  await tool2.call();
}
```

### 4. 预取优化

```typescript
// 在等待API响应时，预取可能需要的数据
using pendingMemoryPrefetch = startRelevantMemoryPrefetch(
  state.messages,
  state.toolUseContext,
);

// 技能发现预取
const pendingSkillPrefetch = skillPrefetch?.startSkillDiscoveryPrefetch(
  null,
  messages,
  toolUseContext,
);

// API调用...
// 预取在后台进行，API响应后直接使用
```

### 5. 缓存策略

```
┌─────────────────────────────────────────────────────────────┐
│                     Prompt 缓存                              │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ System Prompt                                        │    │
│  │ ┌───────────────────────────────────────────────┐   │    │
│  │ │ cache_control: { type: 'ephemeral' }          │   │    │
│  │ │                                                │   │    │
│  │ │ 系统提示在多轮对话中保持不变                    │   │    │
│  │ │ API会缓存这部分，减少token计费                  │   │    │
│  │ └───────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ 消息历史缓存                                         │    │
│  │                                                      │    │
│  │ 历史消息 [msg1, msg2, msg3, ...]                    │    │
│  │           ↑                                          │    │
│  │           └─ 这些消息可以被API缓存                    │    │
│  │                                                      │    │
│  │ 新消息 [userMessage]                                 │    │
│  │           ↑                                          │    │
│  │           └─ 只有新消息需要完整处理                   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 6. 性能检查点

```typescript
// 在关键位置记录性能检查点
queryCheckpoint('query_fn_entry');
queryCheckpoint('query_snip_start');
queryCheckpoint('query_snip_end');
queryCheckpoint('query_microcompact_start');
queryCheckpoint('query_microcompact_end');
queryCheckpoint('query_autocompact_start');
queryCheckpoint('query_autocompact_end');
queryCheckpoint('query_api_streaming_start');
queryCheckpoint('query_api_streaming_end');

// 用于性能分析和瓶颈定位
```

---

## 关键要点总结

### 核心设计原则

| 原则 | 实现方式 | 好处 |
|------|----------|------|
| **异步流式** | `async function*` + `yield` | 实时响应，背压控制 |
| **状态隔离** | 不可变Config + 可变State | 可预测，易调试 |
| **依赖注入** | QueryDeps 参数 | 易测试，可mock |
| **分层压缩** | 4层压缩策略 | 成本递增，按需触发 |
| **错误恢复** | 分类处理 + 重试 | 高可用，优雅降级 |

### 核心流程

```
1. 初始化
   QueryParams → State + Config + Deps

2. 主循环
   while (true) {
     prepare → callAPI → processStream → executeTools → decide
   }

3. 终止条件
   - end_turn: 正常结束
   - error: 不可恢复错误
   - max_turns: 达到最大轮次
```

### 关键函数

| 函数 | 职责 | 位置 |
|------|------|------|
| `query()` | 入口函数 | `query.ts:219` |
| `queryLoop()` | 主循环 | `query.ts:241` |
| `buildQueryConfig()` | 构建配置 | `query/config.ts` |
| `productionDeps()` | 生产依赖 | `query/deps.ts` |
| `runTools()` | 工具编排 | `services/tools/toolOrchestration.ts` |

### 设计亮点

✅ **AsyncGenerator 模式**：优雅的流式处理  
✅ **状态机思维**：清晰的状态转换  
✅ **分层压缩**：成本敏感的优化策略  
✅ **优雅降级**：多层错误恢复机制  
✅ **依赖注入**：良好的可测试性

---

## 源码索引

| 功能 | 文件路径 | 行号 |
|------|----------|------|
| query入口 | `restored-src/src/query.ts` | 219-240 |
| queryLoop主循环 | `restored-src/src/query.ts` | 241-600 |
| State类型 | `restored-src/src/query.ts` | 204-217 |
| QueryParams | `restored-src/src/query.ts` | 181-199 |
| buildQueryConfig | `restored-src/src/query/config.ts` | - |
| productionDeps | `restored-src/src/query/deps.ts` | - |
| StreamingToolExecutor | `restored-src/src/services/tools/StreamingToolExecutor.ts` | - |
| autocompact | `restored-src/src/services/compact/autoCompact.ts` | - |
| microcompact | `restored-src/src/services/compact/compact.ts` | - |

---

## 🎓 本章小结

### 核心要点

1. **QueryEngine 是编排器**：协调 AI 和工具，自己不做具体工作
2. **核心是 while 循环**：发消息 → 收回复 → 执行工具 → 继续
3. **依赖注入保证可测试**：所有外部依赖都可以 mock
4. **多层压缩应对 token 限制**：每层解决一个具体问题

### 可迁移的设计原则

| 原则 | 说明 | 适用场景 |
|------|------|---------|
| 编排模式 | 用专门组件协调多个子系统 | 任何涉及多组件协作的系统 |
| 状态与配置分离 | 可变状态 vs 不可变配置 | 需要长时间运行的循环 |
| 依赖注入 | 外部依赖作为参数传入 | 任何需要测试的代码 |
| 渐进式压缩 | 多层处理，每层解决一个问题 | 处理有大小限制的数据 |
| AsyncGenerator | 流式处理 + 背压控制 | 长时间运行的异步任务 |

### 🤔 思考题

1. **如果 Claude API 支持本地工具执行**（像 MCP 的 server-side tools），QueryEngine 还需要存在吗？
   - 提示：考虑权限控制、UI 反馈、错误处理这些职责谁来承担

2. **如果让你简化这个设计**，你会去掉哪一层压缩？为什么？
   - 提示：考虑 80/20 原则，哪些情况最常见？

3. **这种"单线程"循环模式有什么局限性？** 什么场景下会成为瓶颈？
   - 提示：考虑用户想同时问多个问题，或者一个工具执行很慢的情况

4. **如果你要设计一个"可暂停/恢复"的 QueryEngine**，需要额外保存哪些状态？
   - 提示：思考 State 里哪些字段是可序列化的，哪些是运行时的

---

## 下一步

现在你已经深入理解了 QueryEngine 的内部实现：
- 架构设计和职责边界
- 配置与初始化流程
- 消息流编排的核心循环
- 状态管理机制
- 错误处理与重试策略
- 性能优化设计

接下来，我们将深入消息系统的设计：

👉 继续阅读 [04-消息系统设计](/claudecode/04-message-system)

了解 Message 类型体系、消息生命周期、以及消息如何在系统中流转和持久化。

---

**学习建议**：
- 重点理解 `queryLoop` 的 while(true) 循环逻辑
- 注意 State 和 Continue 的设计，体会状态机思想
- 思考：为什么要分4层压缩？每层解决什么问题？

**Happy Learning!** 🚀
