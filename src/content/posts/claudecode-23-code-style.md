---
title: "代码风格与规范 (Code Style & Conventions)"
summary: "Claude Code CLI 的代码风格体现了大型 TypeScript 项目的工程实践，采用 Biome + ESLint 双重 lint 配置，结合 25+ 自定义规则 保证代码质量。本文档总结源码中观察到的模式和"
publishedAt: 2026-09-21
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 23
seriesGroup: 工程支撑
source: "23-代码风格与规范.md"
sourceSha256: 0dc7363f7841
---
## 概述

Claude Code CLI 的代码风格体现了大型 TypeScript 项目的工程实践，采用 **Biome + ESLint** 双重 lint 配置，结合 **25+ 自定义规则** 保证代码质量。本文档总结源码中观察到的模式和约定。

---

## 1. Lint 工具链

### 1.1 双重 Lint 配置

| 工具 | 职责 | 特点 |
|------|------|------|
| **Biome** | 格式化 + 快速检查 | 性能优先，替代 Prettier |
| **ESLint** | 深度静态分析 | 自定义规则，TypeScript 集成 |

### 1.2 常见 Biome 指令

```typescript
// 禁止 console 输出（但 CLI 输出是故意的）
// biome-ignore lint/suspicious/noConsole:: intentional console output
console.log(output)

// 禁止 Hook 在条件中调用（但 feature() 是编译时常量）
// biome-ignore lint/correctness/useHookAtTopLevel: feature() is a compile-time constant
if (feature('VOICE')) {
  useVoiceIntegration()
}

// 禁止重排 import（ANT-ONLY 标记顺序敏感）
// biome-ignore-all assist/source/organizeImports: ANT-ONLY import markers must not be reordered
```

### 1.3 自定义 ESLint 规则

项目定义了 **25+ 自定义规则**，以强制执行特定约束：

| 规则 | 用途 |
|------|------|
| `custom-rules/no-process-exit` | 禁止直接调用 `process.exit()`，需统一退出点 |
| `custom-rules/no-sync-fs` | 禁止同步 FS 操作，避免阻塞事件循环 |
| `custom-rules/no-top-level-side-effects` | 禁止模块级副作用，利于 DCE |
| `custom-rules/no-process-env-top-level` | 禁止顶层读取环境变量 |
| `custom-rules/no-direct-json-operations` | 禁止直接用 `JSON.parse/stringify`，需用 `jsonParse/jsonStringify` |
| `custom-rules/prefer-use-keybindings` | 优先使用 `useKeybindings` 而非 `useInput` |
| `custom-rules/prefer-use-terminal-size` | 使用 Hook 获取终端尺寸而非直接读 |
| `custom-rules/no-lookbehind-regex` | 禁止 Lookbehind 正则（Safari 兼容性） |
| `custom-rules/bootstrap-isolation` | bootstrap/state.ts 导入隔离 |
| `custom-rules/require-bun-typeof-guard` | 访问 Bun API 前需类型检查 |
| `custom-rules/prompt-spacing` | Prompt 字符串格式规范 |
| `custom-rules/safe-env-boolean-check` | 环境变量布尔检查需用 `isEnvTruthy` |

### 1.4 规则豁免模式

```typescript
// 单行豁免
// eslint-disable-next-line custom-rules/no-process-exit
process.exit(0)

// 块级豁免
/* eslint-disable custom-rules/no-sync-fs -- must be sync; called from signal handler */
fs.writeFileSync(path, content)
/* eslint-enable custom-rules/no-sync-fs */

// 文件级豁免
/* eslint-disable custom-rules/no-process-exit -- CLI subcommand handlers intentionally exit */
```

**最佳实践**：豁免必须附带理由（`--` 后的注释）。

---

## 2. TypeScript 模式

### 2.1 类型导出风格

```typescript
// 类型使用 export type（可被 DCE）
export type DebugLogLevel = 'verbose' | 'debug' | 'info' | 'warn' | 'error'

// 接口也用 export type
export type DiagnosticInfo = {
  installationType: InstallationType
  version: string
  warnings: Array<{ issue: string; fix: string }>
}

// 常量数组派生类型
export const SPECIES = ['duck', 'goose', 'blob', 'cat'] as const
export type Species = (typeof SPECIES)[number]
```

### 2.2 函数签名风格

```typescript
// 参数对象解构 + 默认值
export function createBufferedWriter({
  writeFn,
  flushIntervalMs = 1000,
  maxBufferSize = 100,
  immediateMode = false,
}: {
  writeFn: WriteFn
  flushIntervalMs?: number
  maxBufferSize?: number
  immediateMode?: boolean
}): BufferedWriter

// 返回类型显式声明
export function toError(e: unknown): Error {
  return e instanceof Error ? e : new Error(String(e))
}
```

### 2.3 错误类型设计

```typescript
// 基类继承
export class ClaudeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = this.constructor.name  // 保留类名
  }
}

// 带上下文的错误
export class ConfigParseError extends Error {
  filePath: string
  defaultConfig: unknown

  constructor(message: string, filePath: string, defaultConfig: unknown) {
    super(message)
    this.name = 'ConfigParseError'
    this.filePath = filePath
    this.defaultConfig = defaultConfig
  }
}

// 遥测安全错误（长名称强制审查）
export class TelemetrySafeError_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS extends Error {
  readonly telemetryMessage: string
  constructor(message: string, telemetryMessage?: string) {
    super(message)
    this.telemetryMessage = telemetryMessage ?? message
  }
}
```

### 2.4 类型守卫

```typescript
// unknown 类型的安全检查
export function isAbortError(e: unknown): boolean {
  return (
    e instanceof AbortError ||
    e instanceof APIUserAbortError ||
    (e instanceof Error && e.name === 'AbortError')
  )
}

// errno 提取
export function getErrnoCode(e: unknown): string | undefined {
  if (e && typeof e === 'object' && 'code' in e && typeof e.code === 'string') {
    return e.code
  }
  return undefined
}

// 文件系统不可访问检查
export function isFsInaccessible(e: unknown): e is NodeJS.ErrnoException {
  const code = getErrnoCode(e)
  return code === 'ENOENT' || code === 'EACCES' || code === 'EPERM'
}
```

---

## 3. 模块组织

### 3.1 目录结构

```
src/
├── entrypoints/        # 入口点 (cli.tsx, mcp.ts, sdk.ts)
├── screens/            # 顶层 UI 屏幕
├── components/         # React 组件
├── hooks/              # React Hooks
├── commands/           # Slash 命令
├── tools/              # 工具实现
│   └── {ToolName}/     # 每个工具一个目录
│       ├── {ToolName}.tsx
│       ├── prompt.ts
│       └── {feature}.ts
├── services/           # 后端服务
│   ├── api/            # API 客户端
│   ├── mcp/            # MCP 集成
│   └── analytics/      # 遥测
├── utils/              # 工具函数
│   ├── settings/       # 设置管理
│   ├── sandbox/        # 沙箱适配
│   └── telemetry/      # 追踪
├── state/              # 状态管理
├── types/              # 类型定义
├── constants/          # 常量
├── bootstrap/          # 启动相关
└── ink/                # 自定义 Ink fork
```

### 3.2 导入规范

```typescript
// 1. 外部依赖
import { feature } from 'bun:bundle'
import type { BetaMessageStreamParams } from '@anthropic-ai/sdk/resources/beta/messages/messages.mjs'
import { readFile } from 'fs/promises'

// 2. src/ 绝对路径
import { getSessionId } from 'src/bootstrap/state.js'
import type { QuerySource } from 'src/constants/querySource.js'

// 3. 相对路径
import { logForDebugging } from './debug.js'
import { jsonStringify } from '../slowOperations.js'
```

**注意**：所有导入必须带 `.js` 后缀（ES 模块规范）。

### 3.3 命名约定

| 类别 | 约定 | 示例 |
|------|------|------|
| 文件 | camelCase | `errorLogSink.ts`, `bufferedWriter.ts` |
| React 组件 | PascalCase | `ScrollBox.tsx`, `PromptInput.tsx` |
| 类 | PascalCase | `ClaudeError`, `SandboxManager` |
| 接口/类型 | PascalCase | `DiagnosticInfo`, `ErrorLogSink` |
| 函数 | camelCase | `logForDebugging`, `isEnvTruthy` |
| 常量 | SCREAMING_SNAKE_CASE | `MAX_EVENTS`, `CACHE_PATHS` |
| 私有 | 下划线前缀 | `_resetForTesting`, `_flushWriters` |

---

## 4. React/Ink 模式

### 4.1 Hook 命名

```typescript
// use 前缀
export function useTerminalSize(): { columns: number; rows: number }
export function useTextInput(options: TextInputOptions): TextInputState
export function useMergedTools(): Tool[]

// 副作用 Hook
export function useAfterFirstRender(callback: () => void): void
export function useScheduledTasks(): void
```

### 4.2 组件结构

```typescript
// 类型优先
type Props = {
  message: string
  onDismiss?: () => void
}

// 函数组件
export function Notification({ message, onDismiss }: Props): React.ReactElement {
  const { theme } = useTheme()
  
  return (
    <Box flexDirection="column">
      <Text color={theme.warning}>{message}</Text>
      {onDismiss && <Text dimColor>Press any key to dismiss</Text>}
    </Box>
  )
}
```

### 4.3 依赖数组注释

```typescript
useEffect(() => {
  // Effect logic
}, [dep1, dep2])
// eslint-disable-next-line react-hooks/exhaustive-deps -- scrollRef is a stable ref

useMemo(() => {
  // Memo logic
}, [store])
// biome-ignore lint/correctness/useExhaustiveDependencies: store is a stable context ref
```

---

## 5. 异步模式

### 5.1 Memoize 变体

```typescript
// lodash memoize（无限缓存）
import memoize from 'lodash-es/memoize.js'
export const isDebugMode = memoize((): boolean => { ... })

// TTL 缓存（5分钟过期，后台刷新）
export function memoizeWithTTL<Args, Result>(
  f: (...args: Args) => Result,
  cacheLifetimeMs = 5 * 60 * 1000,
): MemoizedFunction<Args, Result>

// LRU 缓存（限制大小）
export function memoizeWithLRU<Args, Result>(
  f: (...args: Args) => Result,
  cacheFn: (...args: Args) => string,
  maxCacheSize = 100,
): LRUMemoizedFunction<Args, Result>
```

### 5.2 异步 TTL 缓存

```typescript
// 异步版本 + in-flight 去重
export function memoizeWithTTLAsync<Args, Result>(
  f: (...args: Args) => Promise<Result>,
  cacheLifetimeMs = 5 * 60 * 1000,
): AsyncMemoizedFunction<Args, Result>

// 特点：
// - 并发调用共享同一个 Promise
// - 过期返回旧值 + 后台刷新
// - cache.clear() 清除 inFlight 队列
```

### 5.3 BufferedWriter 模式

```typescript
// 缓冲写入避免频繁 IO
const writer = createBufferedWriter({
  writeFn: (content) => fs.appendFileSync(path, content),
  flushIntervalMs: 1000,
  maxBufferSize: 100,
  immediateMode: false,
})

writer.write('line1\n')
writer.write('line2\n')
// 1秒后或达到 100 条时自动刷新
```

---

## 6. 安全编码规范

### 6.1 环境变量检查

```typescript
// ❌ 错误：直接比较
if (process.env.DEBUG === 'true') { ... }

// ✅ 正确：使用工具函数
import { isEnvTruthy, isEnvDefinedFalsy } from './envUtils.js'
if (isEnvTruthy(process.env.DEBUG)) { ... }
if (isEnvDefinedFalsy(process.env.FEATURE)) { ... }
```

### 6.2 JSON 操作

```typescript
// ❌ 错误：直接使用 JSON
const data = JSON.parse(content)
const str = JSON.stringify(obj)

// ✅ 正确：使用包装函数（带错误处理 + 性能监控）
import { jsonParse, jsonStringify } from './slowOperations.js'
const data = jsonParse(content)
const str = jsonStringify(obj)
```

### 6.3 进程退出

```typescript
// ❌ 错误：直接退出
process.exit(1)

// ✅ 正确：使用统一出口
import { cliError, cliOk } from './cli/exit.js'
cliError('Something failed')
cliOk('Done successfully')

// 或需要豁免时
// eslint-disable-next-line custom-rules/no-process-exit -- CLI handler
process.exit(code)
```

### 6.4 同步 FS 操作

```typescript
// ❌ 错误：默认使用同步
fs.readFileSync(path)

// ✅ 正确：使用异步
await fs.promises.readFile(path)

// 必须同步时需豁免
// eslint-disable-next-line custom-rules/no-sync-fs -- signal handler, async would be dropped
fs.writeFileSync(path, content)
```

---

## 7. 性能规范

### 7.1 顶层副作用禁止

```typescript
// ❌ 错误：模块加载时执行
const config = loadConfig()  // 阻塞
registerHandler()  // 副作用

// ✅ 正确：延迟到需要时
export const getConfig = memoize(() => loadConfig())
export function registerHandler() { ... }
```

### 7.2 动态导入

```typescript
// feature() 启用 DCE
if (feature('VOICE')) {
  /* eslint-disable @typescript-eslint/no-require-imports */
  const { useVoice } = require('./voice.js')
  /* eslint-enable @typescript-eslint/no-require-imports */
}

// 或使用动态 import
if (feature('VOICE')) {
  const { useVoice } = await import('./voice.js')
}
```

### 7.3 Lookbehind 正则限制

```typescript
// ❌ 错误：Lookbehind 在某些环境不支持
/(?<=@)user/

// ✅ 正确：需豁免并说明原因
// eslint-disable-next-line custom-rules/no-lookbehind-regex -- gated by includes('@') check
/(?<=@)user/.test(text)
```

---

## 8. 测试规范

### 8.1 测试文件位置

```
src/
├── utils/
│   ├── memoize.ts
│   └── memoize.test.ts  # 同目录
└── __tests__/           # 集成测试
    └── integration.test.ts
```

### 8.2 测试重置函数

```typescript
// 导出测试专用重置函数
export function _resetForTesting(): void {
  cache.clear()
  inMemoryErrorLog = []
}

export function _flushWritersForTesting(): void {
  for (const writer of writers.values()) {
    writer.flush()
  }
}
```

### 8.3 Mock 边界

```typescript
// 通过 deps 对象注入依赖
export const deps = {
  readFile: fs.readFile,
  fetch: globalThis.fetch,
}

// 测试中替换
deps.readFile = jest.fn().mockResolvedValue('mocked content')
```

---

## 9. 注释规范

### 9.1 JSDoc 风格

```typescript
/**
 * Logs diagnostic information to a logfile.
 *
 * *Important* - this function MUST NOT be called with any PII, including
 * file paths, project names, repo names, prompts, etc.
 *
 * @param level    Log level. Only used for information, not filtering
 * @param event    A specific event: "started", "mcp_connected", etc.
 * @param data     Optional additional data to log
 */
export function logForDiagnosticsNoPII(
  level: DiagnosticLogLevel,
  event: string,
  data?: Record<string, unknown>,
): void
```

### 9.2 内联注释

```typescript
// 解释 WHY，不是 WHAT
const STALE_SPAN_TTL_MS = 30 * 60 * 1000  // 30 minutes - matches sessionTracing.ts

// 标记安全决策
// SECURITY: -N/--net EXCLUDED — performs setns(), unshare(), mount(), umount()

// 标记技术债
// TODO: migrate to new API once stable

// 标记性能考量
// Perf: use sync here to avoid yielding during signal handler
```

### 9.3 代码块注释

```typescript
// ── Section Name ──────────────────────────────────────────────────────
// Related code here
// ──────────────────────────────────────────────────────────────────────
```

---

## 10. 常量组织

### 10.1 集中定义

```typescript
// constants/betas.ts - 所有 Beta 头
export const CLAUDE_CODE_20250219_BETA_HEADER = 'claude-code-20250219'
export const INTERLEAVED_THINKING_BETA_HEADER = 'interleaved-thinking-2025-05-14'

// constants/figures.ts - Unicode 字符
export const BLACK_CIRCLE = env.platform === 'darwin' ? '⏺' : '●'
export const LIGHTNING_BOLT = '↯'

// constants/tools.ts - 工具名称
export const BASH_TOOL_NAME = 'bash'
export const FILE_READ_TOOL_NAME = 'file_read'
```

### 10.2 派生常量

```typescript
// 从数组派生 Set
export const REMOTE_SAFE_COMMANDS: Set<Command> = new Set([
  clear,
  compact,
  config,
  help,
])

// 从对象派生类型
export const LEVEL_ORDER = { verbose: 0, debug: 1, info: 2 } as const
export type DebugLogLevel = keyof typeof LEVEL_ORDER
```

---

## 总结

Claude Code CLI 的代码规范体现了以下原则：

1. **安全第一**：25+ 自定义 lint 规则强制安全编码
2. **性能意识**：禁止顶层副作用，支持 DCE
3. **可维护性**：清晰的模块边界，统一的命名约定
4. **可测试性**：依赖注入，测试重置函数
5. **文档完整**：豁免必须说明理由

这套规范确保了在 200+ 文件、60000+ 行代码的规模下保持一致性和质量。
