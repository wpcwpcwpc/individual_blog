---
title: "可观测性：错误处理、日志与调试"
summary: "可观测性是 Agent 的\"神经系统\"——错误处理是痛觉反射，日志是感知记录，调试是问题诊断。"
publishedAt: 2026-08-04
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 15
seriesGroup: 安全与可观测性
source: "15-可观测性：错误处理、日志与调试.md"
sourceSha256: fb5a68b8f2f8
---
> **一句话理解**：可观测性是 Agent 的"神经系统"——错误处理是痛觉反射，日志是感知记录，调试是问题诊断。

> 📖 **阅读时长**: 约 70 分钟  
> 📂 **核心文件**: `src/services/api/errors.ts`, `src/services/api/withRetry.ts`, `src/utils/debug.ts`, `src/utils/diagLogs.ts`  
> **前置阅读**: 03-QueryEngine深度解析, 10-Agent记忆与状态管理

---

## 📖 本章导航

本章从"系统可观测性"的角度，统一讲解 Claude Code 的错误处理、日志记录和调试机制。这些子系统共同构成了 Agent 的"健康监测体系"。

```
┌────────────────────────────────────────────────────────────────────────────┐
│                      可观测性架构 (Observability Stack)                     │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                       错误处理层 (Error Handling)                    │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│  │  │ 错误分类     │  │  重试机制    │  │  降级策略    │              │   │
│  │  │ classifyAPI  │  │  withRetry   │  │  Fallback    │              │   │
│  │  │    Error     │  │  指数退避    │  │  Model/Mode  │              │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                    │                                       │
│                                    ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                       日志记录层 (Logging)                          │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│  │  │  调试日志    │  │  诊断日志    │  │  错误日志    │              │   │
│  │  │  debug.ts    │  │  diagLogs    │  │ errorLogSink │              │   │
│  │  │  开发时用    │  │  无PII监控   │  │  持久化      │              │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                    │                                       │
│                                    ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                       追踪分析层 (Tracing)                          │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│  │  │ Perfetto     │  │ OpenTelemetry│  │  API Dump    │              │   │
│  │  │ Chrome可视化 │  │ 分布式追踪   │  │  Bug报告     │              │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

本章分为三大模块：
1. **错误处理与恢复** - 错误分类、重试机制、降级策略
2. **日志系统** - 调试日志、诊断日志、错误日志
3. **追踪与调试** - Perfetto、OpenTelemetry、API Dump

---

## 🔮 直觉建设：为什么可观测性如此重要？

### 飞机安全系统类比

错误处理就像飞机的安全系统：

| 错误类型 | 无错误处理 | 有错误处理 |
|---------|-----------|-----------|
| API 超时 | 程序卡死 | 自动重试 3 次 |
| 服务器过载 | 程序崩溃 | 等待后重试 |
| 主模型不可用 | 完全不能用 | 切换到备用模型 |
| 用户按 Ctrl+C | 终端乱码 | 恢复终端状态 |

```
错误处理的三个层次：

1. 重试（Retry）
   └─ "再试一次，可能只是网络抖动"
   └─ 策略：指数退避 + 随机抖动

2. 降级（Fallback）
   └─ "主方案不行，用备用方案"
   └─ 策略：Model → Streaming → Fast Mode

3. 恢复（Recovery）
   └─ "实在不行，干净地退出"
   └─ 策略：保存状态 + 恢复终端
```

### 为什么需要"指数退避 + 抖动"？

```
想象服务器过载了，1000 个用户同时重试：

方案 A：立即重试
  1000 用户同时重发 → 服务器更过载 → 更多失败 → 恶性循环

方案 B：固定等待 1 秒
  1000 用户 1 秒后同时重发 → 还是同时！

方案 C：指数退避 + 抖动
  用户 1：等 1.2 秒后重试
  用户 2：等 0.8 秒后重试
  用户 3：等 2.1 秒后重试
  ...
  请求分散开来 → 服务器有时间恢复
```

---

# 第一部分：错误处理与恢复

在与 AI API 交互的系统中，错误处理的质量直接决定用户体验。Claude Code 实现了多层次的错误处理架构：

1. **错误分类系统** - 精确识别 30+ 种错误类型
2. **智能重试机制** - 指数退避 + 熔断器
3. **Model Fallback** - 主模型不可用时自动切换
4. **Streaming Fallback** - 流式失败时降级到非流式
5. **Graceful Shutdown** - 终端状态的完整恢复

---

## 1. 错误处理架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Error Handling Architecture                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                │
│  │   API Call   │──▶│  withRetry   │──▶│   Response   │                │
│  └──────────────┘   └──────────────┘   └──────────────┘                │
│         │                  │                                            │
│         ▼                  ▼                                            │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                │
│  │ classifyAPI  │   │ Retry Logic  │   │  Fallback    │                │
│  │    Error     │   │ • Backoff    │   │ • Model      │                │
│  │              │   │ • Circuit    │   │ • Streaming  │                │
│  └──────────────┘   │   Breaker    │   │ • Fast Mode  │                │
│                     └──────────────┘   └──────────────┘                │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Error Types: api_timeout | rate_limit | server_overload | prompt_too_  │
│              long | auth_error | connection_error | ...                 │
├─────────────────────────────────────────────────────────────────────────┤
│  Recovery: gracefulShutdown → cleanupTerminalModes → forceExit         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 错误分类系统

### 2.1 classifyAPIError 函数

```typescript
// restored-src/src/services/api/errors.ts

/**
 * 将 API 错误分类为特定类型用于分析追踪
 * 返回适合 Datadog 标签的标准化错误类型字符串
 */
export function classifyAPIError(error: unknown): string {
  // 中止请求
  if (error instanceof Error && error.message === 'Request was aborted.') {
    return 'aborted'
  }

  // 超时错误
  if (
    error instanceof APIConnectionTimeoutError ||
    (error instanceof APIConnectionError &&
      error.message.toLowerCase().includes('timeout'))
  ) {
    return 'api_timeout'
  }

  // 重复 529 错误
  if (error instanceof Error && error.message.includes(REPEATED_529_ERROR_MESSAGE)) {
    return 'repeated_529'
  }

  // 速率限制
  if (error instanceof APIError && error.status === 429) {
    return 'rate_limit'
  }

  // 服务器过载 (529)
  if (
    error instanceof APIError &&
    (error.status === 529 || error.message?.includes('"type":"overloaded_error"'))
  ) {
    return 'server_overload'
  }

  // Prompt 过长
  if (error instanceof Error && 
      error.message.toLowerCase().includes('prompt is too long')) {
    return 'prompt_too_long'
  }

  // PDF 错误
  if (/maximum of \d+ PDF pages/.test(error.message)) {
    return 'pdf_too_large'
  }

  // 图片错误
  if (error.status === 400 && error.message.includes('image exceeds')) {
    return 'image_too_large'
  }

  // 工具使用错误
  if (error.message.includes('tool_use` ids were found without `tool_result`')) {
    return 'tool_use_mismatch'
  }

  // 认证错误
  if (error.status === 401 || error.status === 403) {
    return 'auth_error'
  }

  // SSL/TLS 错误
  if (error instanceof APIConnectionError) {
    const details = extractConnectionErrorDetails(error)
    if (details?.isSSLError) {
      return 'ssl_cert_error'
    }
    return 'connection_error'
  }

  // 基于状态码的兜底
  if (error instanceof APIError) {
    if (error.status >= 500) return 'server_error'
    if (error.status >= 400) return 'client_error'
  }

  return 'unknown'
}
```

### 2.2 完整的错误类型列表

```typescript
// 分析追踪中使用的错误类型
const ERROR_TYPES = [
  // 用户操作
  'aborted',               // 用户中止请求
  
  // 网络/连接
  'api_timeout',           // 请求超时
  'connection_error',      // 网络连接失败
  'ssl_cert_error',        // SSL/TLS 证书问题
  
  // 速率/容量
  'rate_limit',            // 429 速率限制
  'server_overload',       // 529 服务器过载
  'repeated_529',          // 连续多次 529
  'capacity_off_switch',   // 容量紧急开关
  
  // 内容大小
  'prompt_too_long',       // 上下文超限
  'pdf_too_large',         // PDF 页数超限
  'pdf_password_protected', // PDF 有密码
  'image_too_large',       // 图片尺寸超限
  
  // 工具调用
  'tool_use_mismatch',     // tool_use 缺少 tool_result
  'unexpected_tool_result', // 意外的 tool_result
  'duplicate_tool_use_id', // 重复的 tool_use_id
  
  // 模型/账户
  'invalid_model',         // 无效模型名称
  'credit_balance_low',    // 余额不足
  'invalid_api_key',       // API 密钥无效
  'token_revoked',         // OAuth token 已撤销
  'oauth_org_not_allowed', // 组织未授权
  'auth_error',            // 通用认证错误
  
  // 提供商特定
  'bedrock_model_access',  // AWS Bedrock 访问问题
  
  // 通用
  'server_error',          // 5xx 错误
  'client_error',          // 4xx 错误
  'unknown',               // 未知错误
] as const
```

### 2.3 用户友好的错误消息

```typescript
// 根据交互模式生成不同的错误提示
export function getPdfTooLargeErrorMessage(): string {
  const limits = `max ${API_PDF_MAX_PAGES} pages, ${formatFileSize(PDF_TARGET_RAW_SIZE)}`
  
  return getIsNonInteractiveSession()
    ? `PDF too large (${limits}). Try reading the file a different way.`
    : `PDF too large (${limits}). Double press esc to go back and try again.`
}

// 非交互模式：提供可执行的替代方案
// 交互模式：引导用户使用 UI 操作
```

---

## 3. 智能重试机制

### 3.1 withRetry 生成器

```typescript
// restored-src/src/services/api/withRetry.ts

const DEFAULT_MAX_RETRIES = 10
const MAX_529_RETRIES = 3
export const BASE_DELAY_MS = 500

export async function* withRetry<T>(
  getClient: () => Promise<Anthropic>,
  operation: (client: Anthropic, attempt: number, context: RetryContext) => Promise<T>,
  options: RetryOptions,
): AsyncGenerator<SystemAPIErrorMessage, T> {
  const maxRetries = getMaxRetries(options)
  let client: Anthropic | null = null
  let consecutive529Errors = options.initialConsecutive529Errors ?? 0
  let lastError: unknown
  
  for (let attempt = 1; attempt <= maxRetries + 1; attempt++) {
    // 检查中止信号
    if (options.signal?.aborted) {
      throw new APIUserAbortError()
    }
    
    try {
      // 需要时获取新的客户端（首次或认证错误后）
      if (client === null || needsClientRefresh(lastError)) {
        // OAuth token 过期时刷新
        if (isOAuthTokenRevokedError(lastError)) {
          await handleOAuth401Error(getClaudeAIOAuthTokens()?.accessToken)
        }
        client = await getClient()
      }
      
      return await operation(client, attempt, retryContext)
    } catch (error) {
      lastError = error
      
      // 记录调试日志
      logForDebugging(
        `API error (attempt ${attempt}/${maxRetries + 1}): ${formatError(error)}`
      )
      
      // 处理各种重试场景...
    }
  }
  
  // 所有重试都失败
  throw new CannotRetryError(lastError, retryContext)
}
```

### 3.2 指数退避策略

```typescript
// 计算重试延迟
function calculateBackoff(
  attempt: number,
  error: APIError,
  isPersistent: boolean
): number {
  // 优先使用服务器返回的 Retry-After
  const retryAfterMs = getRetryAfterMs(error)
  if (retryAfterMs !== null) {
    return retryAfterMs
  }
  
  // 指数退避: BASE * 2^attempt
  const exponentialDelay = BASE_DELAY_MS * Math.pow(2, attempt - 1)
  
  // 添加抖动防止惊群效应
  const jitter = Math.random() * BASE_DELAY_MS
  
  // 持久模式有更高的上限
  const maxBackoff = isPersistent 
    ? PERSISTENT_MAX_BACKOFF_MS  // 5 分钟
    : DEFAULT_MAX_BACKOFF_MS    // 32 秒
  
  return Math.min(exponentialDelay + jitter, maxBackoff)
}

// 从响应头解析 Retry-After
function getRetryAfterMs(error: APIError): number | null {
  const header = error.headers?.get('retry-after')
  if (!header) return null
  
  // 可能是秒数或 HTTP 日期
  const seconds = parseInt(header, 10)
  if (!isNaN(seconds)) {
    return seconds * 1000
  }
  
  const date = Date.parse(header)
  if (!isNaN(date)) {
    return Math.max(0, date - Date.now())
  }
  
  return null
}
```

### 3.3 前台 vs 后台请求优先级

```typescript
// 只有前台请求才值得重试 529
// 后台请求（摘要、标题生成等）立即失败
const FOREGROUND_529_RETRY_SOURCES = new Set<QuerySource>([
  'repl_main_thread',
  'sdk',
  'agent:custom',
  'agent:default',
  'compact',
  'hook_agent',
  'verification_agent',
  'auto_mode',  // 安全分类器必须完成
])

function shouldRetry529(querySource: QuerySource | undefined): boolean {
  // undefined → 保守重试
  return querySource === undefined || 
         FOREGROUND_529_RETRY_SOURCES.has(querySource)
}
```

### 3.4 持久会话的无限重试

```typescript
// CLAUDE_CODE_UNATTENDED_RETRY: 无人值守会话
// 无限重试 429/529，带心跳保持连接

const PERSISTENT_MAX_BACKOFF_MS = 5 * 60 * 1000      // 5 分钟
const PERSISTENT_RESET_CAP_MS = 6 * 60 * 60 * 1000  // 6 小时
const HEARTBEAT_INTERVAL_MS = 30_000                 // 30 秒心跳

async function* persistentRetryLoop(
  operation: () => Promise<T>,
  signal: AbortSignal
): AsyncGenerator<SystemAPIErrorMessage, T> {
  let attempt = 0
  
  while (!signal.aborted) {
    try {
      return await operation()
    } catch (error) {
      if (!isTransientCapacityError(error)) {
        throw error
      }
      
      const backoff = calculateBackoff(attempt++, error, true)
      
      // 分段等待，定期发送心跳
      let waited = 0
      while (waited < backoff && !signal.aborted) {
        const chunk = Math.min(HEARTBEAT_INTERVAL_MS, backoff - waited)
        await sleep(chunk, signal)
        waited += chunk
        
        // 发送心跳消息防止主机标记为空闲
        yield createSystemAPIErrorMessage({
          content: `Waiting for capacity... (${Math.ceil((backoff - waited) / 1000)}s)`,
          error: 'rate_limit'
        })
      }
    }
  }
}
```

---

## 4. Model Fallback 策略

### 4.1 主模型 → 备用模型切换

当主模型（如 Claude 4 Opus）因高需求返回 529 错误时，系统自动切换到备用模型：

```typescript
// restored-src/src/query.ts

// 当主模型返回 529 达到阈值时，切换到备用模型
if (innerError instanceof FallbackTriggeredError && fallbackModel) {
  // 切换模型
  currentModel = fallbackModel
  toolUseContext.options.mainLoopModel = fallbackModel
  
  // 清理可能导致备用模型 400 的 beta 标志
  messages = stripBetaBlocksFromHistory(messages)
  
  // 记录事件
  logEvent('tengu_model_fallback_triggered', {
    original_model: originalModel,
    fallback_model: fallbackModel,
  })
  
  // 通知用户
  yield createSystemMessage(
    `Switched to ${renderModelName(fallbackModel)} due to high demand for ${renderModelName(originalModel)}`,
    'warning'
  )
  
  // 在备用模型上重试
  continue
}
```

### 4.2 FallbackTriggeredError

```typescript
// restored-src/src/services/api/withRetry.ts

export class FallbackTriggeredError extends Error {
  constructor(
    public readonly originalModel: string,
    public readonly fallbackModel: string,
  ) {
    super(`Model fallback triggered: ${originalModel} -> ${fallbackModel}`)
    this.name = 'FallbackTriggeredError'
  }
}

// 在 withRetry 中检测并抛出
if (consecutive529Errors >= MAX_529_RETRIES && options.fallbackModel) {
  logEvent('tengu_api_opus_fallback_triggered', {
    original_model: options.model,
    fallback_model: options.fallbackModel,
  })
  
  throw new FallbackTriggeredError(options.model, options.fallbackModel)
}
```

---

## 5. Streaming Fallback 策略

### 5.1 流式 → 非流式降级

流式传输可能因网络问题部分失败，系统会自动切换到非流式模式：

```typescript
// restored-src/src/query.ts

// 检测流式传输失败
if (
  innerError instanceof APIError &&
  innerError.message.includes('Internal server error') &&
  streamingEnabled
) {
  // 第一次尝试非流式
  streamingEnabled = false
  
  yield createSystemMessage(
    'Streaming failed, retrying without streaming...',
    'info'
  )
  
  continue
}
```

### 5.2 Fast Edit Mode 降级

当 Fast Edit 模式（使用小模型的快速编辑）失败时，切换回标准模式：

```typescript
// 检测 Fast Edit 失败
if (
  innerError instanceof MismatchedToolResultError &&
  toolUseContext.options.fastEditMode
) {
  // 禁用 Fast Edit，使用完整模型
  toolUseContext.options.fastEditMode = false
  
  yield createSystemMessage(
    'Fast edit failed, falling back to standard mode...',
    'info'
  )
  
  continue
}
```

---

## 6. Graceful Shutdown 与终端恢复

### 6.1 优雅关闭流程

```typescript
// restored-src/src/gracefulShutdown.ts

export async function gracefulShutdown(
  signal: string,
  exitCode: number = 0
): Promise<never> {
  // 1. 标记关闭状态
  setShuttingDown(true)
  
  // 2. 中止所有正在进行的请求
  abortAllPendingRequests()
  
  // 3. 保存会话状态
  await saveSessionState()
  
  // 4. 清理终端模式
  cleanupTerminalModes()
  
  // 5. 关闭 MCP 连接
  await closeMCPConnections()
  
  // 6. 等待缓冲区刷新
  await flushBufferedWriters()
  
  // 7. 退出
  process.exit(exitCode)
}
```

### 6.2 终端模式清理

```typescript
// restored-src/src/utils/terminalModeTracking.ts

export function cleanupTerminalModes(): void {
  // 恢复光标
  if (cursorHidden) {
    process.stdout.write('\x1B[?25h')
  }
  
  // 退出备用屏幕
  if (inAlternateScreen) {
    process.stdout.write('\x1B[?1049l')
  }
  
  // 禁用鼠标追踪
  if (mouseTrackingEnabled) {
    process.stdout.write('\x1B[?1003l')
    process.stdout.write('\x1B[?1006l')
  }
  
  // 重置文本样式
  process.stdout.write('\x1B[0m')
}
```

### 6.3 信号处理

```typescript
// 注册信号处理器
process.on('SIGINT', () => gracefulShutdown('SIGINT', 130))
process.on('SIGTERM', () => gracefulShutdown('SIGTERM', 143))
process.on('SIGHUP', () => gracefulShutdown('SIGHUP', 129))

// 未捕获异常处理
process.on('uncaughtException', (error) => {
  logError(error)
  gracefulShutdown('uncaughtException', 1)
})

process.on('unhandledRejection', (reason) => {
  logError(reason instanceof Error ? reason : new Error(String(reason)))
  // 不立即退出，让 Node.js 自己处理
})
```

---

# 第二部分：日志系统

Claude Code 的日志系统是一个多层次、多渠道的观测体系：

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        日志系统架构                                       │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                      日志类型矩阵                                   │  │
│  ├───────────────┬───────────────┬──────────────┬───────────────────┤  │
│  │    类型       │    用途       │    隐私      │     存储位置      │  │
│  ├───────────────┼───────────────┼──────────────┼───────────────────┤  │
│  │ 调试日志      │ 开发调试      │ 含 PII      │ ~/.claude/debug/  │  │
│  │ 诊断日志      │ 容器内监控    │ 无 PII      │ 环境变量指定      │  │
│  │ 错误日志      │ Bug 报告      │ 含 PII      │ ~/.cache/claude/  │  │
│  │ MCP 日志      │ 服务器调试    │ 含 PII      │ ~/.cache/claude/  │  │
│  └───────────────┴───────────────┴──────────────┴───────────────────┘  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 7. 调试日志系统 (debug.ts)

### 7.1 核心机制

```typescript
// 日志级别优先级
const LEVEL_ORDER: Record<DebugLogLevel, number> = {
  verbose: 0,  // 最高频，默认不显示
  debug: 1,    // 标准调试信息
  info: 2,     // 重要状态变化
  warn: 3,     // 警告信息
  error: 4,    // 错误信息
}

// 主日志函数
export function logForDebugging(
  message: string,
  { level }: { level: DebugLogLevel } = { level: 'debug' },
): void
```

### 7.2 启用条件

```typescript
export const isDebugMode = memoize((): boolean => {
  return (
    runtimeDebugEnabled ||                           // /debug 命令启用
    isEnvTruthy(process.env.DEBUG) ||               // DEBUG=1
    isEnvTruthy(process.env.DEBUG_SDK) ||           // DEBUG_SDK=1
    process.argv.includes('--debug') ||              // --debug 参数
    process.argv.includes('-d') ||                   // -d 简写
    isDebugToStdErr() ||                             // --debug-to-stderr
    process.argv.some(arg => arg.startsWith('--debug=')) ||  // --debug=pattern
    getDebugFilePath() !== null                      // --debug-file 参数
  )
})
```

### 7.3 输出目标

| 目标 | 触发条件 | 路径 |
|------|---------|------|
| 文件 | 默认 (ant 用户) | `~/.claude/debug/{session-id}.txt` |
| 文件 | `--debug-file=<path>` | 自定义路径 |
| stderr | `--debug-to-stderr` / `-d2e` | 直接输出到 stderr |
| 缓冲 | 非 debug 模式 | BufferedWriter (1s 刷新) |

### 7.4 日志过滤器

```bash
# 只看 api 和 hooks 类别
claude --debug=api,hooks

# 排除 1p 和 file 类别
claude --debug=!1p,!file
```

**类别提取规则**：

```typescript
// 支持的模式：
// "category: message"       → ["category"]
// "[CATEGORY] message"      → ["category"]
// "MCP server \"name\": message" → ["mcp", "name"]
// "[ANT-ONLY] 1P event:"    → ["ant-only", "1p"]
```

### 7.5 运行时启用

```typescript
// /debug 命令可以在会话中途启用调试
export function enableDebugLogging(): boolean {
  const wasActive = isDebugMode() || process.env.USER_TYPE === 'ant'
  runtimeDebugEnabled = true
  isDebugMode.cache.clear?.()  // 清除 memoize 缓存
  return wasActive
}
```

### 7.6 Latest 符号链接

每次写入调试日志时，自动更新符号链接指向当前会话日志：

```typescript
const updateLatestDebugLogSymlink = memoize(async (): Promise<void> => {
  const debugLogPath = getDebugLogPath()
  const latestSymlinkPath = join(dirname(debugLogPath), 'latest')
  await unlink(latestSymlinkPath).catch(() => {})
  await symlink(debugLogPath, latestSymlinkPath)
})
```

---

## 8. 诊断日志系统 (diagLogs.ts)

### 8.1 设计原则

诊断日志专为**容器内监控**设计，通过 session-ingress 服务上传：

```typescript
/**
 * *Important* - this function MUST NOT be called with any PII, including
 * file paths, project names, repo names, prompts, etc.
 */
export function logForDiagnosticsNoPII(
  level: DiagnosticLogLevel,
  event: string,
  data?: Record<string, unknown>,
): void
```

### 8.2 日志格式

输出为 JSON Lines 格式：

```json
{"timestamp":"2025-01-15T10:30:00.000Z","level":"info","event":"init_started","data":{}}
{"timestamp":"2025-01-15T10:30:01.000Z","level":"info","event":"init_completed","data":{"duration_ms":1000}}
```

### 8.3 启用条件

仅当环境变量 `CLAUDE_CODE_DIAGNOSTICS_FILE` 设置时启用。

### 8.4 计时装饰器

```typescript
// 自动记录操作的开始和完成时间
export async function withDiagnosticsTiming<T>(
  event: string,
  fn: () => Promise<T>,
  getData?: (result: T) => Record<string, unknown>,
): Promise<T> {
  logForDiagnosticsNoPII('info', `${event}_started`)
  try {
    const result = await fn()
    logForDiagnosticsNoPII('info', `${event}_completed`, {
      duration_ms: Date.now() - startTime,
      ...getData?.(result),
    })
    return result
  } catch (error) {
    logForDiagnosticsNoPII('error', `${event}_failed`, { duration_ms: ... })
    throw error
  }
}
```

---

## 9. 错误日志系统 (errorLogSink.ts)

### 9.1 架构设计

错误日志采用 **Sink 模式**，解耦日志收集和存储：

```
┌─────────────┐     ┌───────────────┐     ┌────────────────┐
│ logError()  │────▶│ Event Queue   │────▶│ ErrorLogSink   │
│ logMCPError │     │ (pre-init)    │     │ (attached)     │
│ logMCPDebug │     └───────────────┘     └────────────────┘
└─────────────┘                                   │
                                                  ▼
                                    ┌────────────────────────┐
                                    │ BufferedWriter         │
                                    │ (JSONL files)          │
                                    └────────────────────────┘
```

### 9.2 Sink 接口

```typescript
export type ErrorLogSink = {
  logError: (error: Error) => void
  logMCPError: (serverName: string, error: unknown) => void
  logMCPDebug: (serverName: string, message: string) => void
  getErrorsPath: () => string
  getMCPLogsPath: (serverName: string) => string
}
```

### 9.3 日志路径

使用 `env-paths` 库确定跨平台路径：

```typescript
// 基于项目目录的路径隔离
export const CACHE_PATHS = {
  errors: () => join(paths.cache, sanitizePath(cwd), 'errors'),
  mcpLogs: (serverName: string) => join(paths.cache, sanitizePath(cwd), `mcp-logs-${sanitizePath(serverName)}`),
}
```

**典型路径**：
- Linux: `~/.cache/claude-cli/<project-hash>/errors/<date>.jsonl`
- macOS: `~/Library/Caches/claude-cli/<project-hash>/errors/<date>.jsonl`
- Windows: `%LOCALAPPDATA%\claude-cli\Cache\<project-hash>\errors\<date>.jsonl`

### 9.4 内存错误环

保留最近 100 个错误供 Bug 报告：

```typescript
const MAX_IN_MEMORY_ERRORS = 100
let inMemoryErrorLog: Array<{ error: string; timestamp: string }> = []

export function getInMemoryErrors() {
  return [...inMemoryErrorLog]
}
```

---

# 第三部分：追踪与调试

---

## 10. Perfetto 追踪系统

Perfetto 是 Chrome 的性能追踪工具，Claude Code 集成了它来可视化执行流程：

### 10.1 追踪事件

```typescript
// restored-src/src/tracing/perfettoTracing.ts

export function traceBegin(name: string, args?: Record<string, unknown>): void {
  if (!perfettoEnabled) return
  
  writeTraceEvent({
    name,
    ph: 'B',  // Begin
    ts: performance.now() * 1000,
    pid: process.pid,
    tid: 0,
    args,
  })
}

export function traceEnd(name: string): void {
  if (!perfettoEnabled) return
  
  writeTraceEvent({
    name,
    ph: 'E',  // End
    ts: performance.now() * 1000,
    pid: process.pid,
    tid: 0,
  })
}
```

### 10.2 使用示例

```typescript
// 追踪工具执行
traceBegin('tool_execution', { tool: toolName })
try {
  const result = await tool.call(params)
  return result
} finally {
  traceEnd('tool_execution')
}
```

### 10.3 查看追踪

```bash
# 启用 Perfetto 追踪
claude --perfetto

# 追踪文件保存在 ~/.claude/traces/
# 在 https://ui.perfetto.dev 中打开
```

---

## 11. OpenTelemetry 分布式追踪

### 11.1 Beta 会话追踪

```typescript
// restored-src/src/tracing/betaSessionTracing.ts

export async function initBetaSessionTracing(sessionId: string): Promise<void> {
  const provider = new NodeTracerProvider({
    resource: new Resource({
      [SEMRESATTRS_SERVICE_NAME]: 'claude-code',
      [SEMRESATTRS_SERVICE_VERSION]: version,
      'session.id': sessionId,
    }),
  })

  provider.addSpanProcessor(
    new BatchSpanProcessor(new OTLPTraceExporter({
      url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT,
    }))
  )

  provider.register()
}
```

### 11.2 Span 创建

```typescript
export function createSpan(name: string, fn: () => Promise<T>): Promise<T> {
  const tracer = trace.getTracer('claude-code')
  
  return tracer.startActiveSpan(name, async (span) => {
    try {
      const result = await fn()
      span.setStatus({ code: SpanStatusCode.OK })
      return result
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: error.message })
      span.recordException(error)
      throw error
    } finally {
      span.end()
    }
  })
}
```

---

## 12. API Dump 功能

### 12.1 用途

记录完整的 API 请求/响应，用于 Bug 报告和问题复现：

```typescript
// restored-src/src/debug/dumpPrompts.ts

export async function dumpPrompt(
  request: MessageCreateParams,
  response: Message | Stream<MessageStreamEvent>,
): Promise<void> {
  if (!shouldDumpPrompts()) return
  
  const dumpDir = getDumpDir()
  const filename = `${Date.now()}_${nanoid(8)}.json`
  
  await writeFile(
    join(dumpDir, filename),
    JSON.stringify({
      timestamp: new Date().toISOString(),
      request: sanitizeRequest(request),
      response: await collectResponse(response),
    }, null, 2)
  )
}
```

### 12.2 启用方式

```bash
# 通过环境变量启用
CLAUDE_CODE_DUMP_PROMPTS=1 claude

# 通过 /dump 命令启用（会话内）
> /dump
```

### 12.3 隐私保护

```typescript
function sanitizeRequest(request: MessageCreateParams): MessageCreateParams {
  // 可选：移除或遮罩敏感信息
  return {
    ...request,
    // 保留结构，但可以选择性遮罩内容
  }
}
```

---

## 最佳实践

### 错误处理最佳实践

1. **总是分类错误**
   ```typescript
   const errorType = classifyAPIError(error)
   logEvent('api_error', { type: errorType })
   ```

2. **使用 withRetry 包装 API 调用**
   ```typescript
   const result = yield* withRetry(
     () => getClient(),
     (client) => client.messages.create(params),
     { maxRetries: 3, signal: abortController.signal }
   )
   ```

3. **提供用户友好的错误消息**
   ```typescript
   yield createSystemMessage(
     getUserFriendlyErrorMessage(error),
     'error'
   )
   ```

### 日志最佳实践

1. **使用适当的日志级别**
   ```typescript
   logForDebugging('Starting tool execution', { level: 'info' })
   logForDebugging('Parameter details: ...', { level: 'verbose' })
   logForDebugging('Tool failed!', { level: 'error' })
   ```

2. **诊断日志不含 PII**
   ```typescript
   // ✓ 正确
   logForDiagnosticsNoPII('info', 'tool_executed', { tool_name: 'BashTool' })
   
   // ✗ 错误 - 包含文件路径
   logForDiagnosticsNoPII('info', 'file_read', { path: '/users/john/secret.txt' })
   ```

3. **使用计时装饰器**
   ```typescript
   const result = await withDiagnosticsTiming(
     'expensive_operation',
     () => doExpensiveWork(),
     (result) => ({ items_processed: result.length })
   )
   ```

### 调试最佳实践

1. **使用日志过滤器**
   ```bash
   # 只看 API 相关日志
   claude --debug=api
   
   # 排除噪音
   claude --debug=!verbose,!file
   ```

2. **Perfetto 用于性能分析**
   ```bash
   claude --perfetto
   # 然后在 https://ui.perfetto.dev 分析
   ```

3. **API Dump 用于问题复现**
   ```bash
   CLAUDE_CODE_DUMP_PROMPTS=1 claude
   # 重现问题
   # 将 dump 文件附加到 Bug 报告
   ```

---

## 总结

Claude Code 的可观测性系统是一个三层架构：

```
┌────────────────────────────────────────────────────────────────────────┐
│                         可观测性金字塔                                  │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│                        ┌───────────────┐                               │
│                        │   追踪分析    │   Perfetto / OpenTelemetry    │
│                        │   (最高层)    │   精细的执行流程可视化        │
│                        └───────────────┘                               │
│                              │                                         │
│                    ┌─────────────────────┐                             │
│                    │      日志记录       │   debug / diag / error      │
│                    │      (中间层)       │   事件记录与问题定位        │
│                    └─────────────────────┘                             │
│                              │                                         │
│              ┌───────────────────────────────┐                         │
│              │         错误处理              │   classify / retry      │
│              │         (基础层)              │   / fallback / recover  │
│              └───────────────────────────────┘                         │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

**关键设计原则**：

1. **分层容错**：从重试到降级到恢复，层层保障
2. **隐私分离**：诊断日志无 PII，调试日志本地存储
3. **按需启用**：调试功能通过标志/命令按需启用
4. **标准化接口**：错误分类、日志 Sink 都有统一接口

**下一步阅读**：
- 📖 **16-安全模型与沙箱机制** - 了解安全边界如何防止错误扩散
- 📖 **17-扩展系统与插件架构** - 了解如何为自定义工具添加错误处理
