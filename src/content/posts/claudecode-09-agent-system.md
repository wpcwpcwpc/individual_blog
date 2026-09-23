---
title: "Agent系统架构"
summary: "Agent是带有专用配置的子QueryEngine实例，通过递归调用实现多层级智能协作。"
publishedAt: 2026-07-04
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 9
seriesGroup: Agent 核心系统
readingMinutes: 55
weight: 3
source: "09-Agent系统架构.md"
sourceSha256: 2e8e4096f623
---
> **一句话理解**：Agent是带有专用配置的子QueryEngine实例，通过递归调用实现多层级智能协作。

> **阅读时长**：55分钟  
> **前置阅读**：[03-QueryEngine深度解析](/claudecode/03-query-engine)、[06-工具系统架构](/claudecode/06-tool-system)  
> **重要程度**：⭐️⭐️⭐️（核心必读）  
> **核心价值**：理解AI Agent如何定义、实例化、运行和编排
> 
> **你将获得**：
> - 掌握Agent系统的底层架构原理
> - 理解Agent完整生命周期管理
> - 学会Agent编排和协作模式
> - 了解性能优化和安全隔离策略

---

## 📖 本章导航

- [架构设计](#架构设计)
- [核心原理](#核心原理)
- [Agent定义](#agent定义)
- [生命周期](#生命周期)
- [编排流程](#编排流程)
- [关键设计模式](#关键设计模式)
- [性能优化](#性能优化)
- [关键要点总结](#关键要点总结)

---

## 架构设计

### 整体架构图

```
┌─────────────────────────────────────────────────┐
│            用户交互层 (Terminal UI)              │
│         基于 Ink (React for CLI)                │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│          QueryEngine (AI交互编排引擎)            │
│  ┌──────────────────────────────────────────┐   │
│  │   - 消息流管理                            │   │
│  │   - 工具调用协调                          │   │
│  │   - Agent生命周期控制                     │   │
│  │   - Stream响应处理                        │   │
│  └──────────────────────────────────────────┘   │
└──────────────────┬──────────────────────────────┘
                   │
       ┌───────────┼───────────┐
       │           │           │
       ▼           ▼           ▼
   ┌──────┐   ┌──────┐   ┌──────┐
   │Tools │   │Agents│   │ MCP  │
   │(30+) │   │      │   │Intg. │
   └──────┘   └──┬───┘   └──────┘
                 │
        ┌────────┴────────┐
        │                 │
        ▼                 ▼
    ┌────────┐      ┌────────┐
    │Built-in│      │Custom  │
    │Agents  │      │Agents  │
    └────────┘      └────────┘
```

### 核心技术栈

| 组件 | 实现 | 说明 |
|------|------|------|
| **核心引擎** | QueryEngine | 流式AI交互编排 |
| **消息协议** | Anthropic Messages API | Claude API消息格式 |
| **UI框架** | Ink (React) | 终端界面渲染 |
| **工具系统** | Zod Schema | 类型安全的工具定义 |
| **Agent系统** | 递归QueryEngine | 子Agent实例化 |
| **扩展协议** | MCP | Model Context Protocol |

**📂 核心文件**：
- `src/tools/AgentTool/runAgent.ts` - Agent执行引擎
- `src/tools/AgentTool/loadAgentsDir.ts` - Agent定义和加载
- `src/tools/AgentTool/builtInAgents.ts` - 内置Agent
- `src/components/agents/` - Agent管理UI

---

## 核心原理

### Agent的本质

**Agent = QueryEngine + 定制化配置**

```typescript
Agent = {
  独立的QueryEngine实例,
  隔离的工具集,
  专属的System Prompt,
  独立的上下文管理,
  可配置的权限模式
}
```

### Agent与QueryEngine的关系

```
┌─────────────────────────────────────────┐
│       Main QueryEngine                  │
│  ┌────────────────────────────────┐     │
│  │ Tools: [FileRead, Grep, ... ]  │     │
│  │ System Prompt: "You are..."    │     │
│  │ Messages: [user, assistant...] │     │
│  └────────────────────────────────┘     │
└─────────────────┬───────────────────────┘
                  │
                  │ 调用 AgentTool
                  ▼
┌─────────────────────────────────────────┐
│       Sub QueryEngine (Agent)           │
│  ┌────────────────────────────────┐     │
│  │ Tools: [FileRead, Grep] (限制) │     │
│  │ System Prompt: "You are expert"│     │
│  │ Messages: [task description]   │     │
│  └────────────────────────────────┘     │
└─────────────────────────────────────────┘
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/runAgent.ts:248-860`

### Agent与Tool的递归关系

```
QueryEngine (Main)
    │
    ├─ call tool: FileReadTool
    ├─ call tool: GrepTool  
    └─ call tool: AgentTool  ← 调用子Agent
           │
           └─> runAgent() → 创建新的 QueryEngine
                   │
                   ├─ call tool: FileEditTool
                   └─ return results
```

---

## Agent定义

### Agent类型体系

```typescript
// 基础定义
export type BaseAgentDefinition = {
  agentType: string;              // Agent标识符
  whenToUse: string;              // 使用场景描述
  getSystemPrompt: (context) => string; // 系统提示词
  
  // 工具控制
  tools?: string[];               // 允许的工具列表
  disallowedTools?: string[];     // 禁用的工具
  
  // 运行配置
  model?: string;                 // 使用的模型
  maxTurns?: number;              // 最大轮次
  permissionMode?: PermissionMode; // 权限模式
  
  // 扩展能力
  mcpServers?: AgentMcpServerSpec[]; // 专属MCP服务器
  hooks?: HooksSettings;          // 生命周期钩子
  skills?: string[];              // 预加载的技能
  memory?: AgentMemoryScope;      // 持久化记忆
  
  // 运行模式
  background?: boolean;           // 是否后台运行
  isolation?: 'worktree' | 'remote'; // 隔离模式
}

// 内置Agent
export type BuiltInAgentDefinition = BaseAgentDefinition & {
  source: 'built-in';
  callback?: () => void;
}

// 自定义Agent
export type CustomAgentDefinition = BaseAgentDefinition & {
  source: SettingSource; // 'userSettings' | 'projectSettings'
  filename?: string;
  baseDir?: string;
}

// 插件Agent
export type PluginAgentDefinition = BaseAgentDefinition & {
  source: 'plugin';
  plugin: string;
}
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/loadAgentsDir.ts:106-165`

### Agent定义的三种方式

#### 1. 内置Agent（Built-in）

在代码中硬编码定义，提供核心功能：

```typescript
// 示例：Explore Agent
{
  agentType: 'Explore',
  source: 'built-in',
  whenToUse: '探索代码库、搜索文件、理解项目结构时使用',
  getSystemPrompt: ({ toolUseContext }) => {
    return `You are an expert code explorer...`;
  },
  tools: ['FileRead', 'Grep', 'Glob', 'ListDirectory'],
  omitClaudeMd: true, // 优化：只读Agent不需要CLAUDE.md
  maxTurns: 5
}
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/builtInAgents.ts`

#### 2. 用户/项目Agent（Custom）

通过Markdown文件定义（`.claude/agents/xxx.md`）：

```markdown
---
description: 代码审查专家
tools: ["FileRead", "Grep", "FileEdit"]
model: claude-3-5-sonnet-20241022
effort: 2
permissionMode: approve
maxTurns: 10
memory: project
mcpServers:
  - my-mcp-server
hooks:
  - type: SubagentStop
    command: echo "审查完成"
skills:
  - code-analysis
---

# System Prompt

You are a meticulous code reviewer specializing in...

**Your workflow:**
1. Read the file carefully
2. Identify potential issues
3. Suggest improvements
4. Update your memory with patterns found
```

**存储位置**：
- 用户级：`~/.claude/agents/`
- 项目级：`{projectRoot}/.claude/agents/`

**📂 完整代码**：`restored-src/src/utils/markdownConfigLoader.ts`

#### 3. 插件Agent（Plugin）

通过插件系统动态注册，扩展项目能力：

```typescript
// 插件中定义Agent
export const agents = [
  {
    agentType: 'my-plugin:custom-agent',
    whenToUse: '当需要特定功能时使用',
    // ...
  }
];
```

**📂 完整代码**：`restored-src/src/utils/plugins/loadPluginAgents.ts`

### AI自动生成Agent

项目支持通过AI生成Agent定义：

```typescript
// 用户描述需求
"我需要一个专门做代码审查的Agent，能够检查代码规范和潜在bug"

// AI生成Agent配置
const generated = await generateAgent(userPrompt, model, existingIds);

// 返回结构
{
  identifier: "code-reviewer",
  whenToUse: "Use this agent when reviewing code for...",
  systemPrompt: "You are a meticulous code reviewer..."
}
```

**生成流程**：

```
用户描述
    ↓
调用 generateAgent()
    ↓
构建特殊 System Prompt（教AI如何设计Agent）
    ↓
调用 Claude API（无流式）
    ↓
解析JSON响应
    ↓
验证字段完整性
    ↓
返回Agent定义
```

**📂 完整代码**：`restored-src/src/components/agents/generateAgent.ts:122-197`

---

## 生命周期

### 完整生命周期图

```
┌─────────────────────────────────────────────────┐
│              1. 定义阶段                         │
│  - 编写.md文件 或 代码定义                      │
│  - 配置工具、权限、模型等                        │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│              2. 加载阶段                         │
│  main.tsx 启动时                                │
│  - loadBuiltInAgents()                          │
│  - loadCustomAgents()                           │
│  - loadPluginAgents()                           │
│  - 解析frontmatter                              │
│  - 注册到AgentDefinitions[]                     │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│              3. 实例化阶段                       │
│  AI决定调用Agent时                              │
│  - AgentTool.call()                             │
│  - 创建agentId                                  │
│  - 初始化MCP服务器                               │
│  - 解析工具集                                    │
│  - 构建SystemPrompt                             │
│  - 创建子ToolUseContext                         │
│  - 注册hooks                                    │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│              4. 运行阶段                         │
│  启动Query Loop                                 │
│  ┌─────────────────────────────────────┐        │
│  │  while (turnCount < maxTurns) {     │        │
│  │    - 调用Claude API                 │        │
│  │    - 处理Stream响应                 │        │
│  │    - 执行工具                       │        │
│  │    - 收集结果                       │        │
│  │    - 记录transcript                 │        │
│  │  }                                  │        │
│  └─────────────────────────────────────┘        │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│              5. 清理阶段                         │
│  finally块执行                                  │
│  - 清理MCP连接                                  │
│  - 注销hooks                                    │
│  - 释放文件缓存                                  │
│  - 清理后台任务                                  │
│  - 清理Perfetto追踪                             │
│  - 移除AppState.todos条目                       │
└─────────────────────────────────────────────────┘
```

### 阶段一：定义阶段

**关键任务**：声明Agent的能力、限制和行为

**三种定义方式对比**：

| 特性 | Built-in | Custom | Plugin |
|------|----------|--------|--------|
| 定义方式 | TypeScript代码 | Markdown文件 | 插件导出 |
| 动态System Prompt | ✅ | ❌ | ❌ |
| 用户可编辑 | ❌ | ✅ | 依赖插件 |
| 性能优化选项 | ✅ | 部分 | 部分 |
| 版本控制 | 内置 | 用户/项目 | 插件管理 |

### 阶段二：加载阶段

在`main.tsx`启动时并行加载所有Agent：

```typescript
// 主启动流程
export async function main() {
  // ... 初始化配置 ...
  
  // 并行加载资源
  const [commands, tools, agents] = await Promise.all([
    getCommands(cwd),
    getTools(/* ... */),
    getAgentDefinitions({
      cwd,
      plugins,
      userSettings,
      projectSettings
    })
  ]);
  
  // 创建QueryEngine并传入agents
  const queryEngine = new QueryEngine({
    agents,
    tools,
    commands,
    // ...
  });
  
  // 启动REPL
  await launchRepl(root, appProps, replProps);
}
```

**加载优先级**（后加载的覆盖先加载的）：

```
1. Built-in Agents (最低优先级)
    ↓
2. Plugin Agents
    ↓
3. Policy Settings Agents (企业管理)
    ↓
4. Project Agents
    ↓
5. User Agents (最高优先级)
```

**📂 完整代码**：`restored-src/src/main.tsx:1-300`、`restored-src/src/tools/AgentTool/loadAgentsDir.ts:193-250`

### 阶段三：实例化阶段

当主AI决定需要Agent协助时，通过`AgentTool`触发实例化：

**触发流程**：

```
AI响应包含 tool_use
    ↓
{
  "type": "tool_use",
  "name": "use_agent",
  "input": {
    "agent_type": "Explore",
    "description": "找出处理用户认证的代码",
    "context": "用户报告登录功能有bug"
  }
}
    ↓
AgentTool.call() 接收input
    ↓
调用 runAgent() 创建Agent实例
```

**核心实例化代码**：

```typescript
export async function* runAgent({
  agentDefinition,
  promptMessages,
  toolUseContext,
  isAsync,
  // ...
}): AsyncGenerator<Message> {
  
  // 1. 创建唯一Agent ID
  const agentId = createAgentId();
  
  // 2. 初始化Agent专属MCP服务器
  const { clients: mergedMcpClients, tools: agentMcpTools } = 
    await initializeAgentMcpServers(
      agentDefinition,
      toolUseContext.options.mcpClients
    );
  
  // 3. 解析Agent允许使用的工具
  const resolvedTools = resolveAgentTools(
    agentDefinition,
    availableTools,
    isAsync
  );
  
  // 4. 合并Agent MCP工具和常规工具
  const allTools = agentMcpTools.length > 0
    ? uniqBy([...resolvedTools, ...agentMcpTools], 'name')
    : resolvedTools;
  
  // 5. 构建Agent的System Prompt
  const agentSystemPrompt = await getAgentSystemPrompt(
    agentDefinition,
    toolUseContext,
    resolvedAgentModel,
    additionalWorkingDirectories,
    allTools
  );
  
  // 6. 创建子Agent的上下文（隔离或共享）
  const agentToolUseContext = createSubagentContext(toolUseContext, {
    options: agentOptions,
    agentId,
    agentType: agentDefinition.agentType,
    messages: initialMessages,
    readFileState: agentReadFileState,
    abortController: isAsync ? new AbortController() : parentAbortController,
    shareSetAppState: !isAsync, // 异步Agent完全隔离
  });
  
  // 7. 注册Agent的Frontmatter Hooks
  if (agentDefinition.hooks) {
    registerFrontmatterHooks(
      rootSetAppState,
      agentId,
      agentDefinition.hooks,
      `agent '${agentDefinition.agentType}'`,
      true // isAgent - 转换Stop为SubagentStop
    );
  }
  
  // 8. 预加载Skills
  if (agentDefinition.skills?.length > 0) {
    // 加载技能内容并注入到初始消息
    for (const skillName of agentDefinition.skills) {
      const skill = getCommand(skillName, allSkills);
      const content = await skill.getPromptForCommand('', toolUseContext);
      initialMessages.push(createUserMessage({ content, isMeta: true }));
    }
  }
  
  // 9. 记录初始消息到sidechain transcript
  await recordSidechainTranscript(initialMessages, agentId);
  
  // 10. 写入Agent元数据（用于恢复）
  await writeAgentMetadata(agentId, {
    agentType: agentDefinition.agentType,
    worktreePath,
    description
  });
  
  // Agent实例创建完成，进入运行阶段...
}
```

**关键设计决策**：

| 决策点 | 同步Agent | 异步Agent |
|--------|-----------|-----------|
| **AbortController** | 共享父的 | 独立新建 |
| **setAppState** | 共享（立即同步） | 隔离（通过rootSetAppState） |
| **文件缓存** | 克隆父的 | 克隆父的 |
| **权限提示** | 可显示UI | 自动拒绝（shouldAvoidPrompts） |
| **Response Metrics** | 共享 | 共享（都贡献到指标） |

**📂 完整代码**：`restored-src/src/tools/AgentTool/runAgent.ts:248-746`

### 阶段四：运行阶段

Agent实例化后，启动独立的Query Loop：

```typescript
// 进入Agent的Query Loop
try {
  for await (const message of query({
    messages: initialMessages,
    systemPrompt: agentSystemPrompt,
    userContext: resolvedUserContext,
    systemContext: resolvedSystemContext,
    canUseTool,
    toolUseContext: agentToolUseContext,
    querySource,
    maxTurns: maxTurns ?? agentDefinition.maxTurns,
  })) {
    
    // 回调：用于检测活跃度（长时间thinking时）
    onQueryProgress?.();
    
    // 转发API metrics到父Agent
    if (message.type === 'stream_event' && 
        message.event.type === 'message_start') {
      toolUseContext.pushApiMetricsEntry?.(message.ttftMs);
      continue;
    }
    
    // 处理attachment消息
    if (message.type === 'attachment') {
      if (message.attachment.type === 'max_turns_reached') {
        logForDebugging(`Agent reached max turns limit`);
        break;
      }
      yield message;
      continue;
    }
    
    // 记录可持久化的消息
    if (isRecordableMessage(message)) {
      await recordSidechainTranscript(
        [message],
        agentId,
        lastRecordedUuid
      );
      lastRecordedUuid = message.uuid;
      yield message; // 向父Agent返回
    }
  }
  
  // 正常完成后执行回调
  if (isBuiltInAgent(agentDefinition) && agentDefinition.callback) {
    agentDefinition.callback();
  }
  
} finally {
  // ... 清理阶段 ...
}
```

**Query Loop内部循环**：

```
┌──────────────────────────────────────────┐
│        Agent Query Loop                  │
│                                          │
│  while (turnCount < maxTurns) {          │
│                                          │
│    1. 构建API请求                        │
│       - messages (带上下文)              │
│       - system prompt                    │
│       - tools                            │
│       - model                            │
│                                          │
│    2. 调用 Claude API (streaming)        │
│       response = await callClaudeAPI()   │
│                                          │
│    3. 处理Stream事件                     │
│       for await (const event of response)│
│         ├─ text_delta → 累积文本         │
│         ├─ tool_use → 解析工具调用       │
│         └─ thinking → 思考过程           │
│                                          │
│    4. 遇到tool_use?                      │
│       ├─ Yes: 执行工具                   │
│       │   ├─ 权限检查                    │
│       │   ├─ 工具执行                    │
│       │   ├─ 收集结果                    │
│       │   └─ 将结果加入messages          │
│       │   └─ turnCount++ & continue      │
│       │                                  │
│       └─ No: 检查stop_reason             │
│           └─ end_turn → break            │
│  }                                       │
│                                          │
│  返回最终结果                            │
└──────────────────────────────────────────┘
```

**📂 完整代码**：`restored-src/src/query.ts:180-600`

### 阶段五：清理阶段

无论Agent正常结束、中止还是出错，都会执行清理：

```typescript
} finally {
  // 1. 清理Agent专属MCP服务器连接
  await mcpCleanup();
  
  // 2. 清理Agent注册的Session Hooks
  if (agentDefinition.hooks) {
    clearSessionHooks(rootSetAppState, agentId);
  }
  
  // 3. 清理Prompt Cache追踪状态
  if (feature('PROMPT_CACHE_BREAK_DETECTION')) {
    cleanupAgentTracking(agentId);
  }
  
  // 4. 释放克隆的文件状态缓存内存
  agentToolUseContext.readFileState.clear();
  
  // 5. 释放fork的上下文消息
  initialMessages.length = 0;
  
  // 6. 注销Perfetto追踪条目
  unregisterPerfettoAgent(agentId);
  
  // 7. 清理transcript子目录映射
  clearAgentTranscriptSubdir(agentId);
  
  // 8. 从AppState.todos中移除此Agent
  rootSetAppState(prev => {
    const { [agentId]: _removed, ...todos } = prev.todos;
    return { ...prev, todos };
  });
  
  // 9. 终止此Agent启动的所有后台bash任务
  killShellTasksForAgent(agentId, getAppState, rootSetAppState);
  
  // 10. 终止此Agent启动的MCP监控任务
  if (feature('MONITOR_TOOL')) {
    killMonitorMcpTasksForAgent(agentId, getAppState, rootSetAppState);
  }
}
```

**为什么需要彻底清理？**

1. **内存泄漏防护**：长会话可能spawn数百个Agent，不清理会累积大量孤立状态
2. **资源释放**：MCP连接、文件句柄、网络连接需要及时关闭
3. **状态一致性**：避免已完成Agent的残留状态影响新Agent
4. **进程清理**：后台bash任务不清理会成为PPID=1的僵尸进程

**📂 完整代码**：`restored-src/src/tools/AgentTool/runAgent.ts:816-859`

---

## 编排流程

### 同步Agent vs 异步Agent

项目支持两种编排模式，适应不同场景：

#### 同步Agent（Sync）

**特点**：
- 父Agent **阻塞等待** 子Agent完成
- **共享** AbortController（父中止→子中止）
- **共享** setAppState（状态立即同步）
- **共享** 权限提示UI

**适用场景**：
- 探索类Agent（Explore、Plan）
- 需要立即获取结果的场景
- 需要在父Agent上下文中展示进度

**代码示例**：

```typescript
// 同步调用Explore Agent
const explorerResults = await runAgent({
  agentDefinition: exploreAgent,
  isAsync: false, // 同步模式
  canShowPermissionPrompts: true,
  shareSetAppState: true
});

// 父Agent阻塞在此，直到子Agent完成
console.log('Explorer finished, results:', explorerResults);
```

#### 异步Agent（Async）

**特点**：
- 父Agent **立即继续**，子Agent后台运行
- **独立** AbortController（互不影响）
- **隔离** setAppState（通过rootSetAppState写入）
- **自动拒绝** 权限提示（shouldAvoidPermissionPrompts）

**适用场景**：
- 长时间运行的测试
- 后台监控任务
- 不阻塞主流程的辅助任务

**代码示例**：

```typescript
// 异步启动Test Runner Agent
runAgent({
  agentDefinition: testRunnerAgent,
  isAsync: true, // 异步模式
  background: true,
  canShowPermissionPrompts: false
});

// 父Agent立即继续，不等待子Agent
console.log('Test runner started in background');
// 继续其他工作...
```

**对比表**：

| 特性 | 同步Agent | 异步Agent |
|------|-----------|-----------|
| **父Agent行为** | 阻塞等待 | 立即继续 |
| **AbortController** | 共享父的 | 独立新建 |
| **状态同步** | 实时共享 | 通过rootSetAppState |
| **权限提示** | 可显示UI | 自动拒绝 |
| **UI展示** | 在父Agent下显示 | 独立通知区域 |
| **错误处理** | 抛给父Agent | 自行处理 |
| **适用场景** | 探索、规划 | 测试、监控 |

### Agent层级结构

实际应用中可能形成多层Agent调用：

```
Main Session (QueryEngine)
    │
    ├─ [Sync] Explore Agent
    │     ├─ FileRead: src/auth/login.ts
    │     ├─ Grep: "authenticate"
    │     └─ 返回相关代码位置
    │
    ├─ [Sync] Plan Agent
    │     └─ 返回修复计划
    │
    ├─ FileEdit Tool: 修改src/auth/login.ts
    │
    └─ [Async] Test Runner Agent (后台)
          ├─ Bash: npm test auth
          └─ 10s后返回测试结果
```

### 权限冒泡机制（Bubble Mode）

当子Agent遇到需要权限的操作时，可以"冒泡"到父Agent：

```
┌────────────────────────────────────────┐
│         Main Agent (Terminal)          │
│  permissionMode: 'approve'             │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│      Sub Agent (Background)            │
│  permissionMode: 'bubble'              │
│                                        │
│  尝试: FileEdit dangerous-file.js      │
│                                        │
│  权限检查 → 需要批准                   │
│           ↓                           │
│  冒泡到父Agent的权限上下文              │
└────────────────┬───────────────────────┘
                 │
                 ▼ 在主终端显示
┌────────────────────────────────────────┐
│  ⚠️  Permission Request                │
│                                        │
│  Sub Agent wants to:                   │
│  Edit file: dangerous-file.js          │
│                                        │
│  [Approve] [Reject] [Always Allow]     │
└────────────────┬───────────────────────┘
                 │ 用户决策
                 ▼
         结果返回Sub Agent
                 ↓
         Sub Agent继续执行
```

**实现代码**：

```typescript
// Sub Agent配置
{
  permissionMode: 'bubble' // 关键配置
}

// 权限检查逻辑
if (agentPermissionMode === 'bubble') {
  // 不在子Agent中处理，冒泡到父Agent
  toolPermissionContext = {
    ...toolPermissionContext,
    shouldAvoidPermissionPrompts: false, // 允许显示提示
    awaitAutomatedChecksBeforeDialog: true // 先尝试自动检查
  };
}
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/runAgent.ts:416-463`

### 上下文共享（Fork Context）

父Agent可以选择性地将部分消息历史共享给子Agent：

```typescript
// 场景：父Agent有很长的对话历史
const parentMessages = [...]; // 100条消息

// 只共享最近10条相关消息给子Agent
const relevantContext = parentMessages.slice(-10);

// 启动子Agent，带上fork的上下文
await runAgent({
  agentDefinition: reviewerAgent,
  forkContextMessages: relevantContext, // 子Agent看到的历史
  promptMessages: [
    createUserMessage({ 
      content: "审查刚才修改的文件" 
    })
  ]
});
```

**Fork Context的作用**：

1. **减少Token消耗**：不传递完整历史，只传必要上下文
2. **Prompt Cache优化**：fork点作为缓存边界
3. **避免信息污染**：子Agent不受无关消息干扰
4. **支持并行fork**：多个子Agent可fork同一上下文点

**过滤不完整tool_use**：

```typescript
// fork时会过滤掉没有结果的tool_use
function filterIncompleteToolCalls(messages: Message[]): Message[] {
  const toolUseIdsWithResults = new Set<string>();
  
  // 收集所有有结果的tool_use_id
  for (const message of messages) {
    if (message.type === 'user') {
      for (const block of message.message.content) {
        if (block.type === 'tool_result') {
          toolUseIdsWithResults.add(block.tool_use_id);
        }
      }
    }
  }
  
  // 过滤掉孤立的tool_use
  return messages.filter(message => {
    if (message.type === 'assistant') {
      const hasIncomplete = message.message.content.some(
        block => block.type === 'tool_use' && 
                 !toolUseIdsWithResults.has(block.id)
      );
      return !hasIncomplete;
    }
    return true;
  });
}
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/runAgent.ts:866-904`

### Agent记忆系统

Agent可以跨会话保持记忆：

```markdown
---
description: 代码审查专家
memory: project  # 项目级记忆
---

# System Prompt

You are a code reviewer...

**Update your agent memory** as you discover code patterns, 
style conventions, and architectural decisions.
```

**记忆存储路径**：

| Scope | 路径 | 说明 |
|-------|------|------|
| `user` | `~/.claude/memory/agents/{agentType}/` | 用户全局记忆 |
| `project` | `{projectRoot}/.claude/memory/agents/{agentType}/` | 项目专属记忆 |
| `local` | `.local/memory/agents/{agentType}/` | 本地临时记忆 |

**记忆加载流程**：

```
Agent启动
    ↓
检查 memory 配置
    ↓
loadAgentMemoryPrompt(agentType, scope)
    ↓
读取 {scope}/agents/{agentType}/memory.md
    ↓
追加到 System Prompt
    ↓
Agent看到之前的记忆
```

**记忆更新**：

```typescript
// Agent在运行中更新记忆
// 通过 AgentMemoryWrite Tool
{
  "type": "tool_use",
  "name": "update_agent_memory",
  "input": {
    "content": "发现项目使用 Zod 进行 schema 验证"
  }
}

// 自动追加到 memory.md
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/agentMemory.ts`

---

## 关键设计模式

### 1. 递归QueryEngine模式

**本质**：Agent本身就是QueryEngine的递归实例

```typescript
class QueryEngine {
  async *submitMessage(message) {
    // ...
    for (const toolCall of response.tool_uses) {
      if (toolCall.name === 'use_agent') {
        // 创建子QueryEngine实例
        const subEngine = new QueryEngine({
          systemPrompt: agentSystemPrompt,
          tools: agentTools,
          // ...
        });
        yield* subEngine.submitMessage(agentTask);
      }
    }
  }
}
```

**优势**：
- 统一的AI交互循环
- 自然的递归调用
- 易于理解和维护

### 2. 工具隔离模式

**本质**：每个Agent有独立的工具白名单/黑名单

```typescript
// 父Agent的工具集
const parentTools = [
  'FileRead', 'FileWrite', 'FileEdit',
  'Bash', 'Grep', 'Glob', 'MCP', 'Agent'
];

// 子Agent的受限工具集
const subAgentTools = resolveAgentTools(agentDef, parentTools);
// 结果：['FileRead', 'Grep', 'Glob'] （只读工具）
```

**实现**：

```typescript
function resolveAgentTools(
  agentDef: AgentDefinition,
  availableTools: Tools,
  isAsync: boolean
): Tools {
  let tools = availableTools;
  
  // 1. 应用白名单
  if (agentDef.tools) {
    tools = tools.filter(t => agentDef.tools!.includes(t.name));
  }
  
  // 2. 应用黑名单
  if (agentDef.disallowedTools) {
    tools = tools.filter(t => !agentDef.disallowedTools!.includes(t.name));
  }
  
  // 3. 异步Agent自动移除危险工具
  if (isAsync) {
    tools = tools.filter(t => !DANGEROUS_TOOLS.includes(t.name));
  }
  
  return tools;
}
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/agentToolUtils.ts`

### 3. 上下文传递模式

**本质**：通过`createSubagentContext`创建隔离但可控的子上下文

```typescript
function createSubagentContext(
  parentContext: ToolUseContext,
  options: {
    agentId: AgentId;
    shareSetAppState: boolean; // 关键参数
    // ...
  }
): ToolUseContext {
  return {
    // 共享的部分
    getAppState: options.getAppState,
    options: options.options,
    
    // 隔离的部分
    setAppState: options.shareSetAppState 
      ? parentContext.setAppState  // 同步Agent：共享
      : () => {}, // 异步Agent：隔离（no-op）
    
    agentId: options.agentId,
    messages: options.messages, // 独立消息列表
    abortController: options.abortController, // 可能独立
    readFileState: cloneFileStateCache(parentContext.readFileState),
  };
}
```

**📂 完整代码**：`restored-src/src/utils/forkedAgent.ts:52-150`

### 4. 流式Yield模式

**本质**：Agent使用AsyncGenerator，父Agent可实时接收输出

```typescript
export async function* runAgent(
  // ...
): AsyncGenerator<Message> {
  for await (const message of query({...})) {
    // 实时yield消息给父Agent
    yield message;
  }
}

// 父Agent消费子Agent的输出
for await (const message of runAgent({...})) {
  console.log('Sub agent message:', message);
  // 可以立即展示在UI上
}
```

**优势**：
- 实时反馈，不需等待子Agent完成
- 支持流式UI更新
- 天然支持取消（通过AbortController）

### 5. 清理链模式

**本质**：通过try-finally确保资源清理

```typescript
async function* runAgent() {
  let mcpCleanup: () => Promise<void>;
  
  try {
    // 初始化资源
    const mcp = await initMcp();
    mcpCleanup = mcp.cleanup;
    
    // 执行Agent逻辑
    yield* query({...});
    
  } finally {
    // 无论如何都清理
    await mcpCleanup();
    clearHooks();
    releaseMemory();
    // ...
  }
}
```

**清理顺序设计**：

```
1. MCP连接清理（最外层依赖）
2. Hooks清理（可能触发命令）
3. Prompt Cache清理
4. 文件缓存释放
5. 上下文消息释放
6. Perfetto追踪注销
7. Transcript映射清理
8. AppState条目清理（最核心状态）
9. 后台任务终止（最底层进程）
```

### 6. 权限冒泡模式

**本质**：子Agent的权限请求可穿透到父Agent的UI

```typescript
// 权限检查时
if (permissionMode === 'bubble') {
  // 不在当前层级处理，向上冒泡
  const decision = await parentContext.requestPermission(toolCall);
  return decision;
}
```

### 7. Transcript Sidechain模式

**本质**：每个Agent的消息历史独立记录，支持恢复

```
session/
  ├─ messages.jsonl          # 主会话消息
  └─ subagents/
      ├─ agent-{id}-1/
      │   ├─ messages.jsonl  # 子Agent 1的消息
      │   └─ metadata.json   # 元数据
      └─ agent-{id}-2/
          ├─ messages.jsonl
          └─ metadata.json
```

**用途**：
- 调试子Agent行为
- 恢复中断的子Agent
- 分析Agent执行轨迹
- 性能分析和优化

**📂 完整代码**：`restored-src/src/utils/sessionStorage.ts`

---

## 性能优化

### 优化策略清单

| 优化点 | 策略 | 效果 |
|--------|------|------|
| **Prompt Cache** | 子Agent的System Prompt设计为cache-friendly | 减少API延迟 |
| **CLAUDE.md选择性加载** | 只读Agent跳过`claudeMd` | 节省5-15 Gtok/周 |
| **Git Status延迟加载** | Explore/Plan不加载`gitStatus` | 节省1-3 Gtok/周 |
| **并行工具执行** | 并发安全的工具批量执行 | 减少等待时间 |
| **文件缓存克隆** | 子Agent克隆父缓存 | 避免重复读取 |
| **MCP连接复用** | 引用方式共享父的MCP连接 | 减少连接开销 |
| **Tool结果压缩** | 大型工具结果自动truncate | 减少Token消耗 |

### 1. Prompt Cache优化

**策略**：将Agent的System Prompt设计为可缓存的结构

```typescript
// 缓存友好的结构
const cacheablePrompt = [
  staticInstructions,      // 不变部分，可缓存
  dynamicContext,          // 变化部分
];

// API请求时标记缓存点
{
  system: [
    {
      type: "text",
      text: staticInstructions,
      cache_control: { type: "ephemeral" } // 标记为可缓存
    },
    {
      type: "text",
      text: dynamicContext
    }
  ]
}
```

**📂 完整代码**：查看Prompt工程相关文档

### 2. 上下文选择性加载

**CLAUDE.md优化**：

```typescript
// 对于只读Agent，omitClaudeMd = true
const shouldOmitClaudeMd = agentDefinition.omitClaudeMd &&
  getFeatureValue('tengu_slim_subagent_claudemd', true);

const resolvedUserContext = shouldOmitClaudeMd
  ? { ...userContext, claudeMd: undefined }
  : userContext;
```

**效果**：Explore Agent每次spawn节省约2KB上下文

**📂 完整代码**：`restored-src/src/tools/AgentTool/runAgent.ts:390-398`

### 3. 文件缓存策略

```typescript
// 子Agent克隆父Agent的文件缓存
const agentReadFileState = forkContextMessages
  ? cloneFileStateCache(toolUseContext.readFileState)
  : createFileStateCacheWithSizeLimit(READ_FILE_STATE_CACHE_SIZE);
```

**优势**：
- 父Agent读过的文件，子Agent直接复用
- 避免重复文件IO
- 缓存size限制防止内存爆炸

### 4. 并行执行优化

```typescript
// 检查工具是否支持并发
const isConcurrencySafe = allToolsAreConcurrencySafe(toolCalls);

if (isConcurrencySafe) {
  // 并行执行所有工具
  await Promise.all(toolCalls.map(tc => executeTool(tc)));
} else {
  // 串行执行
  for (const tc of toolCalls) {
    await executeTool(tc);
  }
}
```

**并发安全的工具**：
- FileRead
- Grep
- Glob
- ListDirectory

**必须串行的工具**：
- FileWrite、FileEdit（可能冲突）
- Bash（依赖执行顺序）

### 5. MCP连接复用

```typescript
// Agent通过引用名复用父的MCP连接
mcpServers:
  - "existing-server-name"  # 引用，不创建新连接

// vs 内联定义（创建新连接）
mcpServers:
  - my-server:
      command: "node"
      args: ["server.js"]
```

**📂 完整代码**：`restored-src/src/tools/AgentTool/runAgent.ts:95-218`

---

## 关键要点总结

### 核心概念

✅ **Agent = QueryEngine + 配置**
   - Agent本质是递归的QueryEngine实例
   - 通过工具、权限、上下文隔离实现专业化

✅ **同步 vs 异步**
   - 同步Agent阻塞等待，适合探索规划
   - 异步Agent后台运行，适合长时间任务

✅ **权限冒泡**
   - 子Agent可将权限决策冒泡到父Agent
   - 实现后台Agent与用户交互

✅ **上下文Fork**
   - 选择性共享消息历史
   - 优化Token消耗和Prompt Cache

### 设计模式

1. **递归QueryEngine模式** - 统一AI交互循环
2. **工具隔离模式** - 防止权限泄露
3. **上下文传递模式** - 隔离但可控
4. **流式Yield模式** - 实时反馈
5. **清理链模式** - 资源管理
6. **权限冒泡模式** - 穿透式权限
7. **Transcript Sidechain模式** - 独立记录

### 性能优化

- Prompt Cache优化（cache-friendly结构）
- 选择性上下文加载（omitClaudeMd、omitGitStatus）
- 文件缓存克隆（避免重复IO）
- 并行工具执行（并发安全检查）
- MCP连接复用（引用vs内联）

### 最佳实践

1. **合理选择同步/异步**
   - 需要结果的：同步
   - 后台任务：异步

2. **工具权限最小化**
   - 只给Agent必要的工具
   - 使用disallowedTools禁用危险操作

3. **Memory合理配置**
   - 长期知识：user
   - 项目特定：project
   - 临时实验：local

4. **maxTurns限制**
   - 探索类Agent：3-5轮
   - 执行类Agent：10-15轮
   - 防止无限循环

5. **清理资源**
   - 依赖finally块
   - 按正确顺序清理
   - 特别注意MCP和后台任务

### 下一步阅读建议

- 📖 [03-QueryEngine深度解析](/claudecode/03-query-engine) - 理解Query Loop
- 📖 [06-工具系统架构](/claudecode/06-tool-system) - 理解工具机制
- 📖 [08-模型上下文协议(MCP)深度集成](/claudecode/08-mcp-integration) - MCP与Agent的集成

---

**🎓 学习检查点**：

- [ ] 理解Agent与QueryEngine的关系
- [ ] 掌握Agent的完整生命周期
- [ ] 区分同步Agent和异步Agent
- [ ] 理解权限冒泡机制
- [ ] 掌握上下文Fork和记忆系统
- [ ] 了解7种关键设计模式
- [ ] 知晓性能优化策略

**💡 实践建议**：

1. 尝试创建一个简单的Custom Agent
2. 观察内置Explore Agent的执行流程
3. 实验同步和异步Agent的差异
4. 配置Agent Memory并观察跨会话记忆
5. 阅读`runAgent.ts`源码，理解实现细节
