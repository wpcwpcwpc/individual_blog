---
title: "Agent协作与Coordinator模式"
summary: "Coordinator不是组件，是主Agent的\"编排者模式\"，通过委托Worker实现复杂工程任务的并行协作。"
publishedAt: 2026-07-16
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 11
seriesGroup: Agent 核心系统
readingMinutes: 60
weight: 3
source: "11-Agent协作与Coordinator模式.md"
sourceSha256: d7ceec90746a
---
> **一句话理解**：Coordinator不是组件，是主Agent的"编排者模式"，通过委托Worker实现复杂工程任务的并行协作。

> **阅读时长**：60分钟  
> **前置阅读**：[09-Agent系统架构](/claudecode/09-agent-system)  
> **重要程度**：⭐️⭐️⭐️（核心必读）  
> **核心价值**：掌握复杂工程任务的多Agent协作和并行编排
> 
> **你将获得**：
> - 理解Coordinator模式的本质和设计理念
> - 掌握Worker Agent的工作机制
> - 学会多Agent并发编排策略
> - 理解Continue vs Spawn的决策逻辑
> - 掌握任务拆解的四阶段工作流

---

## 📖 本章导航

- [Coordinator模式概览](#coordinator模式概览)
- [Normal vs Coordinator对比](#normal-vs-coordinator对比)
- [Coordinator工作流程](#coordinator工作流程)
- [Worker Agent机制](#worker-agent机制)
- [消息通信协议](#消息通信协议)
- [Continue vs Spawn策略](#continue-vs-spawn策略)
- [并发编排模式](#并发编排模式)
- [实战案例分析](#实战案例分析)
- [设计模式总结](#设计模式总结)
- [最佳实践](#最佳实践)

---

## Coordinator模式概览

### 核心洞察：模式而非组件

**重要发现**：通过深入源码分析，Coordinator不是一个独立的协调器组件或中间件，而是**主Agent的一种工作模式切换**。

```
❌ 常见误解：
┌──────────────────────────────────────────────┐
│  认为有一个Coordinator组件                    │
│  ┌──────────────┐                            │
│  │ Coordinator  │ ← 管理多个Agent            │
│  │  Component   │                            │
│  └──────┬───────┘                            │
│         │                                    │
│    ┌────┼────┬────┐                          │
│    ▼    ▼    ▼    ▼                          │
│  Agent Agent Agent Agent                     │
└──────────────────────────────────────────────┘

✅ 实际设计：
┌──────────────────────────────────────────────┐
│  Main Agent 有两种工作模式                    │
│                                              │
│  ┌─────────────┐      ┌─────────────┐       │
│  │ Normal Mode │  或  │Coordinator  │       │
│  │  (执行者)    │      │  Mode       │       │
│  │             │      │  (编排者)    │       │
│  │ 自己动手    │      │ 委托Worker  │       │
│  │ 使用所有工具 │      │ 只用编排工具 │       │
│  └─────────────┘      └─────────────┘       │
│                                              │
│  切换方式：环境变量 CLAUDE_CODE_COORDINATOR_MODE=1 │
└──────────────────────────────────────────────┘
```

### 激活机制

**源码位置**：`restored-src/src/coordinator/coordinatorMode.ts:36-41`

```typescript
export function isCoordinatorMode(): boolean {
  if (feature('COORDINATOR_MODE')) {
    return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)
  }
  return false
}
```

当Coordinator模式激活时，三个关键变化同时发生：

1. **System Prompt切换** → 使用`getCoordinatorSystemPrompt()`（369行专属提示词）
2. **工具集限制** → 只保留`Agent`、`SendMessage`、`TaskStop`三个编排工具
3. **User Context注入** → 通过`getCoordinatorUserContext()`告知Worker的工具列表

**📂 完整代码**：`restored-src/src/coordinator/coordinatorMode.ts`

### 设计理念

**来自System Prompt的核心哲学**（第213行）：

> "Parallelism is your superpower. Workers are async. Launch independent workers concurrently whenever possible — don't serialize work that can run simultaneously."

三大支柱：

```
┌─────────────────────────────────────────────────┐
│  1. 职责分离（Separation of Concerns）          │
│     Coordinator: 理解需求、制定计划、综合结果    │
│     Worker: 执行具体任务、使用工具、报告完成     │
│                                                 │
│  2. 并行能力（Parallelism as Superpower）       │
│     - 研究阶段：并行探索多个角度                 │
│     - 实施阶段：独立文件可并行修改               │
│     - 验证阶段：可与实施并行（不同区域）          │
│                                                 │
│  3. 上下文管理（Context Management）            │
│     Coordinator: 维护全局视图和任务状态          │
│     Worker: 专注细节，带上下文执行              │
└─────────────────────────────────────────────────┘
```

### 适用场景分析

**✅ 适合Coordinator模式**：

| 场景类型 | 为什么适合 | 示例 |
|---------|-----------|------|
| 复杂多步骤工程任务 | 自然拆解为多阶段 | 重构认证系统 |
| 需要并行探索 | 多个研究方向同时进行 | 调查性能瓶颈 |
| 长时间运行任务 | Worker后台执行不阻塞 | 运行完整测试套件 |
| 需要独立验证 | Verifier独立于Implementer | 代码审查+验证 |
| 多模块协同 | 不同模块可并行处理 | 全栈功能开发 |

**❌ 不适合Coordinator模式**：

| 场景类型 | 为什么不适合 | 替代方案 |
|---------|-------------|---------|
| 简单单一任务 | 编排开销大于收益 | Normal模式直接执行 |
| 快速修复 | 来回通信耗时 | Normal模式快速响应 |
| 交互式探索 | 用户需要实时反馈 | Normal模式交互 |
| 单文件小改动 | 无需拆解 | Normal模式简单直接 |

### 模式切换时机

项目在以下情况下自动切换或恢复Coordinator模式：

1. **启动时**：检查环境变量`CLAUDE_CODE_COORDINATOR_MODE`
2. **恢复会话时**：通过`matchSessionMode()`匹配会话存储的模式
3. **用户切换时**：通过命令或配置改变环境变量

**源码位置**：`restored-src/src/coordinator/coordinatorMode.ts:43-78`

```typescript
export function matchSessionMode(
  sessionMode: 'coordinator' | 'normal' | undefined,
): string | undefined {
  if (!sessionMode) return undefined
  
  const currentIsCoordinator = isCoordinatorMode()
  const sessionIsCoordinator = sessionMode === 'coordinator'
  
  if (currentIsCoordinator === sessionIsCoordinator) {
    return undefined
  }
  
  // 翻转环境变量以匹配会话模式
  if (sessionIsCoordinator) {
    process.env.CLAUDE_CODE_COORDINATOR_MODE = '1'
  } else {
    delete process.env.CLAUDE_CODE_COORDINATOR_MODE
  }
  
  logEvent('tengu_coordinator_mode_switched', { to: sessionMode })
  
  return sessionIsCoordinator
    ? 'Entered coordinator mode to match resumed session.'
    : 'Exited coordinator mode to match resumed session.'
}
```

---

## Normal vs Coordinator对比

### 架构对比图

```
Normal Mode（传统执行模式）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                User
                  ↕
            Main Agent
                  ↓
         ┌────────┴─────────┬─────────┬─────────┐
         ▼                  ▼         ▼         ▼
      Bash    FileRead   FileEdit   Grep    Glob  ...
       (30+ tools 直接可用)
         │        │          │         │        │
         └────────┴──────────┴─────────┴────────┘
                   串行执行为主

特点：
✓ Agent自己动手使用工具
✓ 单一执行流（受限于顺序调用）
✓ 工具调用直接且快速
✓ 适合简单明确的任务


Coordinator Mode（编排协作模式）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                   User
                     ↕
          Main Agent (Coordinator)
                     ↓
              ┌──────┼──────┐
              ▼      ▼      ▼
           Agent SendMsg TaskStop  ← 仅3个编排工具
              │
              └─────────────┐
                            ▼
                ┌───────────────────────┐
                │    Worker Pool        │
                │                       │
                │  Worker A  Worker B  Worker C  ← 并行执行
                │     ↓         ↓         ↓     │
                │  ┌─────┐  ┌─────┐  ┌─────┐  │
                │  │Tools│  │Tools│  │Tools│  │
                │  │(30+)│  │(30+)│  │(30+)│  │
                │  └─────┘  └─────┘  └─────┘  │
                └───────────────────────────────┘
                      并行执行

特点：
✓ Coordinator编排和综合
✓ Worker执行具体工作
✓ 天然支持并行
✓ 适合复杂工程任务
```

### 完整对比表

| 维度 | Normal Mode | Coordinator Mode |
|------|-------------|------------------|
| **Agent角色** | 执行者（Doer） | 编排者（Orchestrator） |
| **工作方式** | 自己使用工具完成任务 | 委托Worker完成任务 |
| **可用工具** | 全部工具（30+） | Agent + SendMessage + TaskStop |
| **并发能力** | 受限（单执行流） | 强大（多Worker并行） |
| **System Prompt** | 通用提示词（~2000行） | Coordinator专属（369行） |
| **适用任务** | 简单单一任务 | 复杂多步骤工程 |
| **上下文管理** | 单一上下文 | Coordinator全局 + Worker局部 |
| **错误处理** | 直接处理 | 继续或重启Worker |
| **可扩展性** | 线性 | 并行扩展 |
| **典型用时** | 分钟级 | 小时级 |

### System Prompt对比

**Normal Mode System Prompt结构**：
```
1. 身份介绍（你是Claude Code）
2. 工具使用指南（30+工具详细说明）
3. 文件操作规范
4. Git操作规范
5. 权限处理策略
6. 错误处理机制
7. 最佳实践建议
```

**Coordinator Mode System Prompt结构**（源码第111-369行）：
```
1. 角色定位（你是coordinator，编排者不是执行者）
2. 你的工具（只有3个编排工具）
3. Worker能力说明（工具集和限制）
4. 任务工作流（Research → Synthesis → Implementation → Verification）
5. 并发管理原则（何时并行、何时串行）
6. Worker Prompt编写指南 ⭐️（最重要的部分）
7. Continue vs Spawn决策逻辑
8. 示例会话（完整交互演示）
```

**关键差异**：
- Normal模式：教你"如何做" → 技能传授
- Coordinator模式：教你"如何委托" → 管理艺术

### 工具集对比

**Normal Mode可用工具**：

```typescript
const NORMAL_TOOLS = [
  // 编排工具
  'Agent', 'SendMessage', 'TaskStop',
  // 文件操作
  'FileRead', 'FileEdit', 'FileWrite', 'Grep', 'Glob', 'ListDirectory',
  // 命令执行
  'Bash',
  // Git操作
  'Git*',  // GitStatus, GitDiff, GitCommit等
  // MCP工具
  'MCP*',  // 所有MCP服务器的工具
  // 其他
  'Skill', 'WebFetch', ...
]
// 总计：30+ 工具
```

**Coordinator Mode可用工具**：

```typescript
const COORDINATOR_TOOLS = [
  'Agent',        // 启动新Worker
  'SendMessage',  // 继续已有Worker
  'TaskStop',     // 停止Worker
  // 可能包含（如果可用）:
  'subscribe_pr_activity',    // 订阅PR事件
  'unsubscribe_pr_activity',  // 取消订阅
]
// 总计：3-5 工具（极简）
```

**工具限制实现**：

**源码位置**：`restored-src/src/utils/toolPool.ts:28-44`

```typescript
/**
 * Filters a tool array to the set allowed in coordinator mode.
 * Coordinator只能调用编排动作，不能直接操作文件或运行命令。
 */
export function applyCoordinatorToolFilter(tools: Tools): Tools {
  return tools.filter(t => 
    t.name === AGENT_TOOL_NAME ||
    t.name === SEND_MESSAGE_TOOL_NAME ||
    t.name === TASK_STOP_TOOL_NAME ||
    t.name.startsWith('subscribe_') ||
    t.name.startsWith('unsubscribe_')
  )
}
```

**为什么这么严格限制？**

1. **强制职责分离** - Coordinator不应该自己动手
2. **避免混淆** - 清晰区分编排层和执行层
3. **简化Prompt** - 减少Coordinator需要理解的工具
4. **提升安全性** - Worker的权限由其配置控制

---

## Coordinator工作流程

### 标准四阶段工作流

Coordinator System Prompt（第199-210行）明确定义了任务拆解的标准流程：

```
┌────────────────────────────────────────────────────────────┐
│                Coordinator标准工作流                        │
│                                                            │
│  Phase 1: Research（研究阶段）                              │
│  ┌──────────────────────────────────────────────┐          │
│  │ Coordinator分析任务                           │          │
│  │   ↓                                          │          │
│  │ 并行启动多个Worker探索不同角度                 │          │
│  │   ↓ Agent(Worker A: 搜索认证代码)            │          │
│  │   ↓ Agent(Worker B: 查找测试覆盖)            │          │
│  │   ↓ Agent(Worker C: 检查配置文件)            │          │
│  │                                              │          │
│  │ 目标：收集信息，理解现状                      │          │
│  │ 原则：广泛并行，只读操作                      │          │
│  └──────────────────────────────────────────────┘          │
│           ↓ <task-notification>                            │
│                                                            │
│  Phase 2: Synthesis（综合阶段）⭐️ 最重要                   │
│  ┌──────────────────────────────────────────────┐          │
│  │ Coordinator读取所有Worker返回                 │          │
│  │   ↓                                          │          │
│  │ 理解问题根因和上下文                          │          │
│  │   ↓                                          │          │
│  │ 制定详细实施计划                              │          │
│  │   - 具体文件路径                              │          │
│  │   - 具体行号                                  │          │
│  │   - 具体修改内容                              │          │
│  │                                              │          │
│  │ ⚠️ 禁止偷懒：                                 │          │
│  │ ❌ "基于你的发现修复"                         │          │
│  │ ❌ "根据研究结果实施"                         │          │
│  │ ✅ "在src/auth.ts:42添加null检查..."         │          │
│  └──────────────────────────────────────────────┘          │
│           ↓                                                │
│                                                            │
│  Phase 3: Implementation（实施阶段）                       │
│  ┌──────────────────────────────────────────────┐          │
│  │ Coordinator发送详细指令                       │          │
│  │   ↓ SendMessage(Worker A) 或 Agent(新Worker) │          │
│  │                                              │          │
│  │ Worker执行具体代码修改                        │          │
│  │   - 读取文件                                  │          │
│  │   - 应用修改                                  │          │
│  │   - 运行测试                                  │          │
│  │   - 提交代码                                  │          │
│  │   - 报告完成                                  │          │
│  │                                              │          │
│  │ 原则：                                        │          │
│  │ - 独立文件可并行                              │          │
│  │ - 相关文件需串行                              │          │
│  └──────────────────────────────────────────────┘          │
│           ↓ <task-notification>                            │
│                                                            │
│  Phase 4: Verification（验证阶段）                         │
│  ┌──────────────────────────────────────────────┐          │
│  │ Coordinator启动独立Verification Worker        │          │
│  │   ↓ Agent(Verification Agent)                │          │
│  │                                              │          │
│  │ Verifier独立验证实现                          │          │
│  │   - 运行构建                                  │          │
│  │   - 运行测试                                  │          │
│  │   - 运行linter                               │          │
│  │   - 试图破坏功能（对抗性测试）                 │          │
│  │   - 返回PASS/FAIL/PARTIAL                    │          │
│  │                                              │          │
│  │ 原则：fresh eyes，不信任实施者                 │          │
│  └──────────────────────────────────────────────┘          │
│           ↓                                                │
│                                                            │
│  Coordinator综合结果，向用户报告                            │
└────────────────────────────────────────────────────────────┘
```

### 完整会话示例（带源码注释）

让我们看一个真实的Coordinator会话，理解每个阶段的细节：

**场景**：用户报告auth模块有null pointer bug

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Turn 1: Research Phase - Coordinator并行派发研究任务
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

User: "auth模块有null pointer，能修复吗？"

Coordinator:
  让我先从两个角度调查一下。
  
  Agent({
    description: "调查auth bug",
    subagent_type: "worker",
    prompt: `调查src/auth/目录。找出在session处理和token验证
            中可能发生null pointer exception的位置。
            
            重点关注：
            - Session对象的字段访问
            - Token缓存逻辑
            - 过期处理
            
            报告具体文件路径、行号和涉及的类型。
            不要修改任何文件。`
  })
  
  Agent({
    description: "研究auth测试覆盖",
    subagent_type: "worker",
    prompt: `找出所有与src/auth/相关的测试文件。
            
            分析：
            - 测试结构和组织方式
            - 已覆盖的场景
            - session过期相关的测试gap
            
            报告测试文件路径和覆盖情况。
            不要修改任何文件。`
  })
  
  两个Worker并行调查中 - 我会报告发现。

【源码分析】：
1. 两个Agent()调用在同一turn → 并行执行
2. subagent_type: "worker" → 使用Worker Agent配置
3. 每个prompt都是self-contained（Worker看不到主对话）
4. description用于UI显示（CoordinatorTaskPanel）
5. 明确"不要修改文件" → 保证只读探索
6. Coordinator不预测结果 → 等待实际返回

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Turn 2: Worker A完成 - 系统返回task-notification
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

User:  # 注意：这是系统生成的user-role消息，不是真实用户
  <task-notification>
  <task-id>agent-a1b</task-id>
  <status>completed</status>
  <summary>Agent "调查auth bug" completed</summary>
  <result>
  在src/auth/validate.ts:42发现null pointer。
  
  问题分析：
  - Session接口定义在src/auth/types.ts:15
  - user字段类型为 User | undefined
  - 在session过期但token仍缓存时，user字段为undefined
  - validate.ts:42直接访问user.id，未检查null
  - 导致TypeError: Cannot read property 'id' of undefined
  
  相关代码：
  ```typescript
  // src/auth/validate.ts:42
  function validateToken(session: Session): boolean {
    return session.user.id === getCachedUserId();  // ← Bug
  }
  ```
  
  建议：在访问user.id前添加null检查。
  </result>
  <usage>
    <total_tokens>15234</total_tokens>
    <tool_uses>8</tool_uses>
    <duration_ms>12450</duration_ms>
  </usage>
  </task-notification>

【源码分析】：
1. <task-notification>是Worker完成的标准XML格式
2. 由runAgent()的finally块生成（runAgent.ts:746-860）
3. 系统将其wrap为user-role消息传递给Coordinator
4. Coordinator通过<task-id>识别是哪个Worker
5. <result>包含Worker的最终文本响应
6. <usage>提供性能指标

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Turn 3: Synthesis Phase - Coordinator理解并制定计划
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Coordinator:
  找到问题了 - validate.ts:42的null pointer。Session的user
  字段在过期时为undefined，但代码直接访问user.id。
  
  SendMessage({
    to: "agent-a1b",
    message: `修复src/auth/validate.ts:42的null pointer。
    
    具体步骤：
    1. 在validateToken函数中，访问user.id之前添加检查：
       if (!session.user) {
         return false;  // 或返回适当的错误
       }
    
    2. 考虑返回值：如果user为null，token应该被视为无效。
    
    3. 运行相关测试：
       npm test src/auth/validate.test.ts
    
    4. 如果测试通过，提交代码：
       git add src/auth/validate.ts
       git commit -m "fix: add null check for session.user in validateToken"
    
    5. 报告commit hash。`
  })
  
  修复进行中。

【源码分析】：
1. SendMessage而非新Agent → 继续Worker A（复用上下文）
2. to: "agent-a1b" → 指定Worker的task_id
3. message是self-contained → 具体文件、行号、做什么、如何验证
4. ⚠️ 注意没有"基于你的发现"这种偷懒表述
5. Coordinator已经**理解**了问题（Synthesis完成）
6. 提供清晰的"完成"标准（commit hash）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Turn 4: Implementation Complete - Worker A报告完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

User:
  <task-notification>
  <task-id>agent-a1b</task-id>
  <status>completed</status>
  <summary>Agent "调查auth bug" completed</summary>
  <result>
  已修复null pointer bug。
  
  修改内容：
  - 在validateToken函数中添加了user字段检查
  - user为null时，函数返回false（token invalid）
  
  测试结果：
  - 所有auth相关测试通过 ✓
  - 新增了session过期场景的测试用例
  
  提交信息：
  Commit: abc123def456
  Message: "fix: add null check for session.user in validateToken"
  </result>
  </task-notification>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Turn 5: Verification Phase（可选但推荐）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Coordinator:
  修复已提交。让我启动独立验证。
  
  Agent({
    description: "验证auth修复",
    subagent_type: "verification",  # 特殊类型
    prompt: `验证commit abc123def456的auth修复是否正确。
    
    原始任务：修复src/auth/validate.ts:42的null pointer
    修改的文件：src/auth/validate.ts
    
    验证清单：
    1. 重现原始bug（应该已修复）
    2. 验证修复逻辑正确
    3. 运行完整auth测试套件
    4. 检查相关代码的回归
    5. 尝试边界情况（undefined, null, expired session）
    
    以PASS/FAIL/PARTIAL结尾。`
  })
  
  验证进行中。

【源码分析】：
1. subagent_type: "verification" → 特殊Agent类型
2. Verification Agent有独立的严格System Prompt
3. 后台运行（background: true）
4. 会进行对抗性测试（尝试破坏）
5. 输出格式严格：每个检查必须有Command+Output+Result

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Turn 6: Final Report - Coordinator综合结果
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

User:
  <task-notification>
  <task-id>agent-v3x</task-id>
  <status>completed</status>
  <summary>Agent "验证auth修复" completed</summary>
  <result>
  ### Check: Reproduce original bug
  **Command run:**
    node scripts/test-auth-bug.js
  **Output observed:**
    Before fix: TypeError: Cannot read property 'id' of undefined
    After fix: validateToken returns false correctly
  **Result: PASS**
  
  ### Check: Run auth test suite
  **Command run:**
    npm test src/auth/
  **Output observed:**
    25 passing (2.3s)
  **Result: PASS**
  
  ### Check: Boundary case - expired session
  **Command run:**
    curl -X POST localhost:3000/api/validate \
      -H 'Authorization: expired-token-abc'
  **Output observed:**
    {"valid": false, "reason": "session expired"}
    (HTTP 200)
  **Result: PASS**
  
  VERDICT: PASS
  </result>
  </task-notification>

Coordinator:
  修复完成并验证通过！
  
  总结：
  ✓ 修复了src/auth/validate.ts:42的null pointer
  ✓ 添加了session.user的null检查
  ✓ 所有测试通过
  ✓ 独立验证确认修复正确
  
  Commit: abc123def456
```

### 并发管理原则

**来自System Prompt的指导**（第211-219行）：

```
Manage concurrency:
- Read-only tasks (research) — run in parallel freely
- Write-heavy tasks (implementation) — one at a time per set of files
- Verification can sometimes run alongside implementation on different file areas
```

**并发决策矩阵**：

| 任务类型 | 并发策略 | 原因 | 示例 |
|---------|---------|------|------|
| **只读研究** | ✅ 自由并行 | 无竞争，安全 | 搜索认证代码 + 搜索支付代码 |
| **独立模块写** | ✅ 可以并行 | 文件不重叠 | 修改auth.ts + 修改payment.ts |
| **相关文件写** | ❌ 必须串行 | 可能冲突 | 修改index.ts + 修改它import的模块 |
| **验证** | ⚠️ 选择性并行 | 看是否共享资源 | 可以在另一模块实施时验证 |
| **同一文件** | ❌ 严格串行 | 编辑冲突 | 不能两个Worker改同一文件 |

**并发示例对比**：

```typescript
// ✅ 好的并发 - 完全独立的只读搜索
Agent({ prompt: "搜索src/auth/中的认证逻辑" })  // Worker A
Agent({ prompt: "搜索src/payment/中的支付逻辑" }) // Worker B
Agent({ prompt: "搜索src/logging/中的日志配置" }) // Worker C
// 三个完全独立 → 并行运行，节省时间

// ✅ 好的串行 - 相关模块的写操作
Agent({ prompt: "修改src/auth/validate.ts添加null检查" })
// 等待完成...
SendMessage({ 
  to: "agent-a1b",
  message: "现在修改src/auth/index.ts导出新的validate函数"
})
// 同一模块内有依赖 → 串行避免不一致

// ❌ 危险的并发 - 可能冲突
Agent({ prompt: "修改src/auth/validate.ts" })
Agent({ prompt: "修改src/auth/index.ts (imports validate.ts)" })
// 同时修改相关文件 → 可能导入冲突或逻辑不一致

// ✅ 混合策略 - 并行研究后串行实施
// Phase 1: 并行研究
Agent({ prompt: "调查bug A" })  // Worker 1
Agent({ prompt: "调查bug B" })  // Worker 2
// 等待两个都完成...
// Phase 2: 串行修复（如果bug相关）
SendMessage({ to: "worker-1", message: "修复bug A" })
// 等待完成...
SendMessage({ to: "worker-1", message: "修复bug B" })
```


---

## 实战案例分析

### 案例1：大型功能开发（用户头像系统）

**任务**：为应用添加完整的用户头像功能

**Coordinator的任务拆解**：

```
Phase 1: 并行研究（3个Worker同时启动）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
┌─────────────────────────────────────────────┐
│ Worker A: 调查后端                           │
│ - 现有用户模型结构                            │
│ - 文件存储方案（S3配置）                      │
│ - 现有图片处理逻辑                            │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│ Worker B: 调查前端                           │
│ - 现有用户设置页面结构                        │
│ - 已有的图片上传组件                          │
│ - 状态管理方案                               │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│ Worker C: 调查API                           │
│ - 现有用户API endpoint                       │
│ - 认证中间件                                 │
│ - 文件上传限制                               │
└─────────────────────────────────────────────┘

Phase 2: Synthesis（Coordinator综合）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Coordinator收到三份报告，制定实施计划：
├─ 后端：扩展User模型 + S3上传
├─ 前端：复用ImageUpload组件
├─ API：新增 PUT /api/users/:id/avatar

Phase 3: 串行实施（有依赖关系）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SendMessage(Worker A): "实现后端User模型扩展和S3上传"
         ↓ 完成后
SendMessage(Worker B): "实现前端上传组件集成"
         ↓ 完成后
SendMessage(Worker C): "添加API endpoint和测试"

Phase 4: 并行验证
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Agent(type: verification): "验证后端实现"
Agent(type: verification): "验证前端实现"  
Agent(type: verification): "E2E测试"
```

**执行时间对比**：

| 方式 | 时间分布 | 总耗时 |
|------|---------|--------|
| **传统串行** | 研究5min×3 + 实施10min×3 = 45min | ~45分钟 |
| **Coordinator并行** | 研究max(5min) + 实施30min = 35min | ~35分钟 |
| **理想情况** | 研究5min + 串行实施20min | ~25分钟 |

→ **节省约40%时间**

### 案例2：跨模块Bug修复

**任务**：修复影响多个模块的认证bug

**Coordinator的协作策略**：

```typescript
// Turn 1: 并行诊断
Agent({ description: "诊断后端auth", prompt: "..." })  // Worker A
Agent({ description: "诊断前端auth", prompt: "..." })  // Worker B
Agent({ description: "检查日志", prompt: "..." })       // Worker C

// Turn 2: 收到三份报告后，Coordinator识别问题
// "bug根因在后端，但前端也有防御性修复机会"

// Turn 3: 串行修复（避免冲突）
SendMessage({ 
  to: "worker-a",  // 复用后端诊断Worker
  message: "修复validateToken函数，添加null检查"
})

// Turn 4: 修复完成后，并行处理次要问题
Agent({ prompt: "前端添加防御性token验证" })  // 新Worker
Agent({ prompt: "更新API错误处理文档" })       // 新Worker

// Turn 5: 验证
Agent({ 
  type: "verification",
  prompt: "完整验证auth流程..."
})
```

**关键决策点**：

```
Worker A诊断完成 → Coordinator决定：
├─ 是否继续A（SendMessage）？→ A已有上下文，效率高
├─ 还是新建Worker？→ A负载太重时考虑
└─ 需要等其他Worker吗？→ 看是否有依赖

选择SendMessage继续A的原因：
1. A已经读取了相关文件（上下文热）
2. A了解项目的auth结构
3. 新建Worker需要重新探索
```

### 案例3：文档更新任务（高并行）

**任务**：更新6个模块的API文档

这是最适合并行的场景（无依赖）：

```typescript
// 一次性启动6个Worker
Agent({ prompt: "更新User模块文档" })      // Worker 1
Agent({ prompt: "更新Auth模块文档" })      // Worker 2
Agent({ prompt: "更新Product模块文档" })   // Worker 3
Agent({ prompt: "更新Order模块文档" })     // Worker 4
Agent({ prompt: "更新Payment模块文档" })   // Worker 5
Agent({ prompt: "更新Notification模块文档" })  // Worker 6

// Coordinator等待所有完成
// 收到6份完成通知

// 最后合并检查
Agent({ 
  prompt: "检查6份文档的格式一致性，确保无冲突"
})
```

**执行时间**：

- 串行：6 × 10min = 60分钟
- 并行：max(10min) + 5min合并 = 15分钟
- **节省75%！**

---

## 设计模式总结

### 模式1：Scatter-Gather（分散-收集）

**适用场景**：任务可分解为独立子任务

```
         Coordinator
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
  Worker    Worker    Worker
    A         B         C
    │         │         │
    └─────────┼─────────┘
              │
         Coordinator
        (收集&综合)
```

**代码模式**：
```typescript
// Scatter
Agent({ prompt: "子任务A" })
Agent({ prompt: "子任务B" })
Agent({ prompt: "子任务C" })

// 等待所有完成...

// Gather (Coordinator综合)
"综合三个Worker的发现，问题在于..."
```

### 模式2：Pipeline（流水线）

**适用场景**：任务有明确的顺序依赖

```
Coordinator → Worker A → SendMessage → Worker A → SendMessage → Worker A
               (调查)                   (实施)                    (验证)
```

**代码模式**：
```typescript
// Step 1: 启动Worker
Agent({ description: "数据库迁移", prompt: "分析当前schema" })

// Step 2: 收到分析后，继续同一Worker
SendMessage({ to: "worker-id", message: "执行迁移计划" })

// Step 3: 迁移完成后，继续验证
SendMessage({ to: "worker-id", message: "验证迁移结果" })
```

### 模式3：Map-Reduce（映射-归约）

**适用场景**：对多个目标执行相同操作后合并结果

```
         Coordinator
              │
    ┌────┬────┼────┬────┐  (Map)
    ▼    ▼    ▼    ▼    ▼
   W1   W2   W3   W4   W5
    │    │    │    │    │
    └────┴────┼────┴────┘  (Reduce)
              │
         Coordinator
         (合并结果)
```

**代码模式**：
```typescript
// Map: 对每个模块执行相同任务
const modules = ['user', 'auth', 'product', 'order']
modules.forEach(m => {
  Agent({ prompt: `分析${m}模块的测试覆盖率` })
})

// Reduce: Coordinator收集所有结果
"综合所有模块的测试覆盖率报告..."
```

### 模式4：Fork-Join（分叉-合并）

**适用场景**：并行执行后需要同步点

```
              Coordinator
                  │
         ┌───────┴───────┐
         ▼               ▼
     Worker A        Worker B
     (独立任务)      (独立任务)
         │               │
         └───────┬───────┘
                 │ (同步点)
                 ▼
            Worker C
        (依赖A和B的结果)
```

**代码模式**：
```typescript
// Fork
Agent({ prompt: "准备数据库" })   // Worker A
Agent({ prompt: "准备配置文件" }) // Worker B

// 等待两者完成... (Join point)

// 依赖两者的后续任务
Agent({ prompt: "部署应用（使用准备好的数据库和配置）" })
```

### 模式5：Supervisor（监督者）

**适用场景**：需要实时监控和干预

```
    Coordinator (Supervisor)
          │
    ┌─────┴─────┐
    ▼           ▼
 Worker A    Worker B
    │           │
    │←──────────│ (发现问题)
    │           │
Coordinator介入：
"停止A，问题在B"
```

**代码模式**：
```typescript
// 启动Worker
Agent({ prompt: "执行长时间任务A" })
Agent({ prompt: "执行长时间任务B" })

// Worker B报告问题
// Coordinator决定：
SendMessage({ to: "worker-a", message: "暂停当前操作，等待B的问题解决" })

// 问题解决后
SendMessage({ to: "worker-a", message: "继续执行" })
```

---

## 最佳实践

### 实践1：任务描述清晰自包含

**❌ 坏的任务描述**：
```typescript
Agent({
  prompt: "修复那个bug"  // 哪个bug？什么症状？
})
```

**✅ 好的任务描述**：
```typescript
Agent({
  prompt: `修复src/auth/validate.ts:42的null pointer bug。
  
  症状：session过期时validateToken崩溃
  根因：session.user可能为null
  修复：添加null检查
  
  验证：运行npm test -- --grep auth
  完成标准：测试通过 + 提交commit hash`
})
```

### 实践2：合理使用Continue vs Spawn

**决策树**：

```
Worker已完成一个子任务？
├─ 是 → 下一个任务需要之前的上下文吗？
│       ├─ 是 → SendMessage继续该Worker
│       └─ 否 → 新建Worker（更干净）
└─ 否 → 等待Worker完成
```

**具体场景**：

| 场景 | 选择 | 原因 |
|------|------|------|
| 调查完成→修复 | SendMessage | 复用调查的上下文 |
| 修复完成→新功能 | 新Agent | 避免上下文污染 |
| 文件A→文件B（无关） | 新Agent | 各自独立 |
| 同文件多处修改 | SendMessage | 保持一致性 |

### 实践3：避免Worker间直接依赖

**❌ 坏的设计**：
```typescript
Agent({ prompt: "Worker A: 输出文件到/tmp/result.json" })
Agent({ prompt: "Worker B: 读取/tmp/result.json" })
// 如果A还没写完，B就读取 → 失败
```

**✅ 好的设计**：
```typescript
// Worker A完成
// Coordinator提取关键信息
// 传递给Worker B
Agent({ 
  prompt: `Worker B任务：
  
  依赖信息（来自Worker A的结果）：
  - 用户数：12345
  - 最近登录：2026-04-01
  
  你的任务：基于以上信息生成报告`
})
```

### 实践4：并行度控制

**System Prompt指导**（来自源码）：

> "Be careful with modifications involving shared parts: imports, dependencies, overlapping code. One worker per file is safer."

**实践建议**：

```
并行安全：
✅ 不同文件的独立修改
✅ 不同模块的独立任务
✅ 只读操作（调查、分析）

需要串行：
⚠️ 同一文件的多处修改
⚠️ 有import依赖的文件组
⚠️ 涉及全局状态的修改
```

### 实践5：验证策略

**推荐的三层验证**：

```
Layer 1: Worker自验证
├─ Worker完成任务后自己运行测试
└─ 在result中报告测试结果

Layer 2: Coordinator检查
├─ 检查Worker报告的完整性
└─ 确认关键步骤都已执行

Layer 3: Verification Agent（可选）
├─ 独立的对抗性测试
├─ 尝试边界情况
└─ 后台运行，不阻塞主流程
```

---

## 常见问题与故障排除

### Q1：Worker之间如何共享信息？

**答**：Worker之间不直接通信。所有信息都通过Coordinator中转：

```
Worker A → 结果报告 → Coordinator → 提取关键信息 → Worker B的prompt
```

### Q2：Worker失败了怎么办？

**答**：Coordinator会收到失败通知：

```xml
<task-notification>
  <task-id>worker-id</task-id>
  <status>error</status>
  <error>具体错误信息</error>
</task-notification>
```

Coordinator可以：
1. 重试同一个Worker（SendMessage）
2. 启动新Worker
3. 报告失败并终止

### Q3：如何知道所有Worker都完成了？

**答**：Coordinator通过`task-notification`追踪每个Worker的状态。

```typescript
// Coordinator内部逻辑（概念性）
const pendingWorkers = new Set()
pendingWorkers.add('worker-a')
pendingWorkers.add('worker-b')

// 收到通知时
function onTaskNotification(notification) {
  if (notification.status === 'completed') {
    pendingWorkers.delete(notification.taskId)
  }
  if (pendingWorkers.size === 0) {
    // 所有Worker完成，进入下一阶段
  }
}
```

### Q4：Worker可以拒绝任务吗？

**答**：不能直接拒绝，但可以报告无法完成：

```
Worker result:
"无法完成任务。原因：
1. 指定的文件不存在
2. 需要数据库权限（我没有）

建议：检查文件路径，或提供数据库凭证"
```

Coordinator根据这个反馈调整策略。

### Q5：Normal模式和Coordinator模式可以混用吗？

**答**：不可以。环境变量决定整个会话的模式：

```bash
# 整个会话都是Coordinator模式
CLAUDE_CODE_COORDINATOR_MODE=1

# 或整个会话都是Normal模式
# （不设置或设为false）
```

但内置Agent（如Explore、Plan）始终可用。

---

## 性能优化建议

### 优化1：减少Worker启动开销

**问题**：每个新Worker都需要初始化上下文

**解决**：复用Worker（使用SendMessage继续）

```typescript
// ❌ 低效：每个小任务都新建Worker
Agent({ prompt: "任务1" })
Agent({ prompt: "任务2" })
Agent({ prompt: "任务3" })

// ✅ 高效：同一Worker处理相关任务
Agent({ prompt: "任务1" })  // Worker A
// 完成后
SendMessage({ to: "worker-a", message: "任务2" })
// 完成后
SendMessage({ to: "worker-a", message: "任务3" })
```

### 优化2：智能并行度

**原则**：不是越多Worker越好

```
最佳并行度 = min(独立子任务数, 资源限制, 复杂度阈值)

示例：
- 6个独立文档任务 → 6个Worker ✓
- 3个复杂重构任务 → 3个Worker（每个占用大量资源）
- 20个小任务 → 分批处理（避免过载）
```

### 优化3：提前规划依赖

**在Coordinator启动Worker前，分析依赖图**：

```
任务依赖分析：
├─ A: 修改User模型 (独立)
├─ B: 修改Auth模块 (依赖A)
├─ C: 更新API文档 (独立)
└─ D: 添加测试 (依赖A和B)

执行计划：
Round 1: A和C并行
Round 2: B
Round 3: D
```

---

## 与其他模式的对比

### Coordinator vs Normal Mode

| 维度 | Normal Mode | Coordinator Mode |
|------|-------------|------------------|
| **角色** | 执行者（自己干活） | 编排者（委托Worker） |
| **工具** | 所有工具 | 只有Agent、SendMessage、TaskStop |
| **思维** | "我来修改这个文件" | "让Worker A去修改" |
| **并行** | 自己串行执行 | 多Worker并行 |
| **适用** | 简单任务 | 复杂多步任务 |

### Coordinator vs 传统多Agent框架

| 维度 | 传统框架 | Claude Code Coordinator |
|------|----------|------------------------|
| **架构** | 独立Coordinator组件 | 主Agent的模式切换 |
| **通信** | 消息队列/RPC | Task Notification + SendMessage |
| **状态** | 共享状态存储 | 各Worker独立上下文 |
| **恢复** | 依赖外部持久化 | Sidechain Transcript |
| **灵活性** | 预定义工作流 | LLM动态决策 |

---

## 总结

### 核心要点

1. **Coordinator是模式不是组件** - 通过环境变量切换主Agent的角色
2. **Worker是动态Agent** - 通过`Agent`工具按需创建
3. **通信通过Coordinator中转** - Worker之间不直接交互
4. **Continue优于Spawn** - 复用Worker上下文更高效
5. **并行是核心优势** - 利用独立任务并发执行

### 决策树总结

```
需要执行复杂多步任务？
├─ 否 → Normal模式（直接执行）
└─ 是 → Coordinator模式
        │
        ├─ 任务可分解为独立子任务？
        │   ├─ 是 → 并行启动多Worker（Scatter-Gather）
        │   └─ 否 → 串行Pipeline
        │
        ├─ Worker完成后需要继续？
        │   ├─ 需要之前上下文 → SendMessage
        │   └─ 不需要 → 新Agent
        │
        └─ 任务完成后需要验证？
            └─ 是 → 启动Verification Agent
```

### 四阶段工作流回顾

```
┌─────────────────────────────────────────────────┐
│  Phase 1: Research (并行探索)                    │
│  多Worker同时调查不同方面                         │
├─────────────────────────────────────────────────┤
│  Phase 2: Synthesis (Coordinator综合)           │
│  汇总Worker发现，制定实施计划                     │
├─────────────────────────────────────────────────┤
│  Phase 3: Implementation (顺序实施)              │
│  按依赖顺序执行，使用SendMessage继续Worker        │
├─────────────────────────────────────────────────┤
│  Phase 4: Verification (并行验证)                │
│  启动Verification Agent对抗性测试                │
└─────────────────────────────────────────────────┘
```

---

**🎉 恭喜！**

你已经完整掌握了Coordinator模式的：
- 设计哲学和模式本质
- Worker创建和通信机制
- Continue vs Spawn决策
- 五种协作设计模式
- 最佳实践和性能优化

**📚 下一步建议**：

1. **实践验证**：在实际项目中尝试Coordinator模式
2. **深入源码**：阅读`restored-src/src/coordinator/`完整代码
3. **学习Worker**：阅读 [12-内置Agent深度解析](/claudecode/12-builtin-agents) 了解Worker能力
4. **记忆管理**：阅读 [10-Agent记忆与状态管理](/claudecode/10-agent-memory-state) 了解上下文持久化

**🔗 相关文档**：
- [09-Agent系统架构](/claudecode/09-agent-system) - QueryEngine底层实现
- [12-内置Agent深度解析](/claudecode/12-builtin-agents) - 6种内置Agent设计

---

> 文档版本：v1.0  
> 最后更新：2026-04-02  
> 反馈：如发现错误或有改进建议，欢迎提Issue或PR
