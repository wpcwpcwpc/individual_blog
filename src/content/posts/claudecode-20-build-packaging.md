---
title: "构建与打包系统"
summary: "│                        BUILD TARGETS                                 │"
publishedAt: 2026-09-10
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 20
seriesGroup: 工程支撑
source: "20-构建与打包系统.md"
sourceSha256: 0a55a9f735df
---
> **核心洞察**：Claude Code 使用 **Bun 原生编译**技术，将 TypeScript 项目编译为独立的可执行文件。构建系统的核心创新在于 **`bun:bundle` 特性标志**——一种编译时条件编译机制，能够根据目标构建（内部/外部）自动消除死代码。

---

## 20.1 架构概述

### 20.1.1 构建目标矩阵

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BUILD TARGETS                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────┐    ┌──────────────────┐    ┌───────────────┐  │
│  │   Bun Binary     │    │   npm Package    │    │  Agent SDK    │  │
│  │  (Standalone)    │    │  (@anthropic/    │    │  (sdk.mjs)    │  │
│  │                  │    │   claude-code)   │    │               │  │
│  ├──────────────────┤    ├──────────────────┤    ├───────────────┤  │
│  │ - macOS arm64    │    │ - CommonJS       │    │ - ESM Bundle  │  │
│  │ - macOS x64      │    │ - ESM            │    │ - Slim Core   │  │
│  │ - Linux x64      │    │ - Node.js 20+    │    │ - No UI/CLI   │  │
│  │ - Windows x64    │    │ - All platforms  │    │ - Minimal Deps│  │
│  └──────────────────┘    └──────────────────┘    └───────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     Build Pipeline                            │   │
│  │                                                               │   │
│  │  Source ──► TypeScript ──► bun:bundle ──► Tree-shake ──► Out │   │
│  │             Compile        DCE Pass       Minify              │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 20.1.2 核心技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| **运行时** | Bun | 高速 JS 运行时，原生编译支持 |
| **编译器** | `bun build --compile` | 生成独立二进制 |
| **宏系统** | `MACRO.VERSION` | 构建时常量注入 |
| **特性标志** | `bun:bundle` | 条件编译与 DCE |
| **嵌入文件** | `Bun.embeddedFiles` | 资源打包 |

---

## 20.2 `bun:bundle` 特性标志系统

### 20.2.1 设计原理

`bun:bundle` 是 Bun 构建器的一个虚拟模块，提供编译时条件编译能力：

```typescript
// src/entrypoints/cli.tsx
import { feature } from 'bun:bundle'

// 特性检查 - 构建时评估
if (feature('BRIDGE_MODE') && args[0] === 'remote-control') {
  const { bridgeMain } = await import('../bridge/bridgeMain.js')
  await bridgeMain(args.slice(1))
  return
}

// 条件加载 - 整个分支可被 DCE
if (feature('DAEMON') && args[0] === 'daemon') {
  // 如果 DAEMON=false，这整个块会被删除
  const { daemonMain } = await import('../daemon/main.js')
  await daemonMain(args.slice(1))
}
```

### 20.2.2 特性标志清单

从源码中提取的完整特性列表：

```typescript
// 核心功能开关
const FEATURE_FLAGS = {
  // 远程控制与桥接
  BRIDGE_MODE: true,          // Remote Control 功能
  CCR_AUTO_CONNECT: true,     // 自动连接远程
  CCR_MIRROR: true,           // 镜像模式
  
  // 后台任务与调度
  DAEMON: true,               // 后台守护进程
  BG_SESSIONS: true,          // 后台会话管理
  AGENT_TRIGGERS: true,       // 定时触发器
  AGENT_TRIGGERS_REMOTE: true,// 远程代理调度
  
  // 语音与多模态
  VOICE_MODE: true,           // 语音模式
  CHICAGO_MCP: true,          // Computer Use MCP
  
  // AI 协调
  COORDINATOR_MODE: true,     // 多代理协调
  FORK_SUBAGENT: true,        // 子代理分叉
  
  // 内容处理
  EXTRACT_MEMORIES: true,     // 记忆提取
  HISTORY_SNIP: true,         // 历史裁剪
  CONTEXT_COLLAPSE: true,     // 上下文折叠
  REACTIVE_COMPACT: true,     // 响应式压缩
  CACHED_MICROCOMPACT: true,  // 缓存微压缩
  
  // 实验功能
  KAIROS: false,              // Anthropic 内部
  KAIROS_BRIEF: false,        // Brief 工具
  PROACTIVE: false,           // 主动模式
  TRANSCRIPT_CLASSIFIER: true,// 转录分类器
  BASH_CLASSIFIER: true,      // Bash 分类器
  
  // 构建专用
  DUMP_SYSTEM_PROMPT: false,  // 系统提示导出（内部）
  ABLATION_BASELINE: false,   // A/B 测试基线
} as const
```

### 20.2.3 DCE（Dead Code Elimination）机制

```typescript
// ✅ 正确模式 - 会被正确消除
if (feature('VOICE_MODE')) {
  // 如果 VOICE_MODE=false，整个块消失
}

// ✅ 三元表达式也工作
const voiceModule = feature('VOICE_MODE') 
  ? require('./voice/index.js') 
  : null

// ❌ 错误模式 - 无法消除
const flag = feature('VOICE_MODE')
if (flag) {  // 间接引用，DCE 失效
  // ...
}

// ❌ 负向模式不工作
if (!feature('VOICE_MODE')) {
  return  // 无法正确消除
}
```

源码注释中的说明：

```typescript
// src/voice/voiceModeEnabled.ts
export async function isVoiceModeEnabled(): Promise<boolean> {
  // Negative pattern (if (!feature(...)) return) does not eliminate
  // the check from external builds. Use positive pattern instead.
  return feature('VOICE_MODE')
    ? await checkGate_CACHED_OR_BLOCKING('tengu_voice_mode')
    : false
}
```

---

## 20.3 构建时宏注入

### 20.3.1 MACRO 对象

构建系统在编译时注入全局 `MACRO` 对象：

```typescript
// src/entrypoints/cli.tsx
// Fast-path for --version: zero module loading needed
if (args.length === 1 && args[0] === '--version') {
  // MACRO.VERSION is inlined at build time
  console.log(`${MACRO.VERSION} (Claude Code)`)
  return
}
```

### 20.3.2 可用宏列表

```typescript
declare const MACRO: {
  VERSION: string        // 例如 "1.0.34"
  BUILD_TIME: string     // 构建时间戳
  PACKAGE_URL: string    // npm 包名 "@anthropic-ai/claude-code"
  ISSUES_EXPLAINER: string // 反馈说明文本
}

// 使用示例
// src/cli/update.ts
writeToStdout(`Current version: ${MACRO.VERSION}\n`)

// src/commands/version.ts
{
  value: MACRO.BUILD_TIME
    ? `${MACRO.VERSION} (built ${MACRO.BUILD_TIME})`
    : MACRO.VERSION
}
```

---

## 20.4 运行时环境检测

### 20.4.1 Bun 检测

```typescript
// src/utils/bundledMode.ts

/**
 * Detects if the current runtime is Bun.
 * Returns true when:
 * - Running a JS file via the `bun` command
 * - Running a Bun-compiled standalone executable
 */
export function isRunningWithBun(): boolean {
  // https://bun.com/guides/util/detect-bun
  return process.versions.bun !== undefined
}

/**
 * Detects if running as a Bun-compiled standalone executable.
 * This checks for embedded files which are present in compiled binaries.
 */
export function isInBundledMode(): boolean {
  return (
    typeof Bun !== 'undefined' &&
    Array.isArray(Bun.embeddedFiles) &&
    Bun.embeddedFiles.length > 0
  )
}
```

### 20.4.2 可执行路径解析

```typescript
// src/tools/shared/spawnMultiAgent.ts

/**
 * For non-native (node/bun running a script), use process.argv[1].
 * For native binaries, use process.execPath.
 */
export function getClaudeExecutablePath(): string {
  return isInBundledMode() ? process.execPath : process.argv[1]!
}
```

---

## 20.5 高速哈希系统

### 20.5.1 Bun.hash 优化

```typescript
// src/utils/hash.ts

/**
 * Hash arbitrary content for change detection. Bun.hash is ~100x faster than
 * crypto hash, but requires Bun runtime. Falls back to portable hash.
 */
export function fastHash(content: string): string {
  if (typeof Bun !== 'undefined') {
    return Bun.hash(content).toString()
  }
  // Fallback for Node.js
  return simpleHash(content)
}

/**
 * Deterministic across runtimes (unlike Bun.hash which uses wyhash). Use as a
 * fallback when Bun.hash isn't available, or when you need on-disk-stable
 * hashes.
 */
export function simpleHash(s: string): string {
  let hash = 0
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash + s.charCodeAt(i)) | 0
  }
  return (hash >>> 0).toString(36)
}

// 双参数组合哈希
export function fastHashPair(a: string, b: string): string {
  if (typeof Bun !== 'undefined') {
    return Bun.hash(b, Bun.hash(a)).toString()
  }
  return simpleHash(a + b)
}
```

### 20.5.2 实际应用

```typescript
// src/services/api/promptCacheBreakDetection.ts
const hash = Bun.hash(str)
// Bun.hash can return bigint for large inputs; convert to number safely

// src/buddy/companion.ts
function buddyHash(s: string): number {
  return Number(BigInt(Bun.hash(s)) & 0xffffffffn)
}

// src/utils/sessionStoragePortable.ts
const pathHash = typeof Bun !== 'undefined' 
  ? Bun.hash(name).toString(36) 
  : simpleHash(name)
```

---

## 20.6 内置技能打包

### 20.6.1 技能注册系统

```typescript
// src/skills/bundledSkills.ts

export type BundledSkillDefinition = {
  name: string
  description: string
  aliases?: string[]
  whenToUse?: string
  allowedTools?: string[]
  /**
   * Additional reference files to extract to disk on first invocation.
   * Keys are relative paths, values are content.
   */
  files?: Record<string, string>
  getPromptForCommand: (
    args: string,
    context: ToolUseContext,
  ) => Promise<ContentBlockParam[]>
}

// Internal registry
const bundledSkills: Command[] = []

export function registerBundledSkill(definition: BundledSkillDefinition): void {
  const command: Command = {
    type: 'prompt',
    name: definition.name,
    source: 'bundled',
    loadedFrom: 'bundled',
    // ... 其他属性
  }
  bundledSkills.push(command)
}
```

### 20.6.2 技能初始化

```typescript
// src/skills/bundled/index.ts
import { feature } from 'bun:bundle'

export function initBundledSkills(): void {
  // 始终注册的核心技能
  registerUpdateConfigSkill()
  registerKeybindingsSkill()
  registerVerifySkill()
  registerDebugSkill()
  
  // 条件注册的功能技能
  if (feature('KAIROS') || feature('KAIROS_DREAM')) {
    const { registerDreamSkill } = require('./dream.js')
    registerDreamSkill()
  }
  
  if (feature('AGENT_TRIGGERS')) {
    const { registerLoopSkill } = require('./loop.js')
    registerLoopSkill()
  }
  
  if (feature('BUILDING_CLAUDE_APPS')) {
    const { registerClaudeApiSkill } = require('./claudeApi.js')
    registerClaudeApiSkill()
  }
}
```

### 20.6.3 资源文件提取

```typescript
// src/skills/bundledSkills.ts

/**
 * Extract bundled skill's reference files to disk for model Read/Grep.
 * Called lazily on first skill invocation.
 */
async function extractBundledSkillFiles(
  skillName: string,
  files: Record<string, string>,
): Promise<string | null> {
  const dir = getBundledSkillExtractDir(skillName)
  try {
    await writeSkillFiles(dir, files)
    return dir
  } catch (e) {
    logForDebugging(
      `Failed to extract bundled skill '${skillName}' to ${dir}: ${e.message}`,
    )
    return null  // 继续工作，只是没有 base-directory 前缀
  }
}

// 安全写入（防止符号链接攻击）
const SAFE_WRITE_FLAGS =
  process.platform === 'win32'
    ? 'wx'  // Windows 使用字符串标志
    : fsConstants.O_WRONLY | fsConstants.O_CREAT | 
      fsConstants.O_EXCL | O_NOFOLLOW
```

---

## 20.7 入口点架构

### 20.7.1 快速路径设计

```typescript
// src/entrypoints/cli.tsx

async function main(): Promise<void> {
  const args = process.argv.slice(2)

  // 🚀 FAST PATH 1: --version（零模块加载）
  if (args.length === 1 && args[0] === '--version') {
    console.log(`${MACRO.VERSION} (Claude Code)`)
    return
  }

  // 🚀 FAST PATH 2: 桥接模式
  if (feature('BRIDGE_MODE') && args[0] === 'remote-control') {
    const { bridgeMain } = await import('../bridge/bridgeMain.js')
    await bridgeMain(args.slice(1))
    return
  }

  // 🚀 FAST PATH 3: 后台守护进程
  if (feature('DAEMON') && args[0] === 'daemon') {
    const { daemonMain } = await import('../daemon/main.js')
    await daemonMain(args.slice(1))
    return
  }

  // 🚀 FAST PATH 4: 后台会话
  if (feature('BG_SESSIONS') && ['ps', 'logs', 'attach', 'kill'].includes(args[0])) {
    const bg = await import('../cli/bg.js')
    // ... 处理命令
    return
  }

  // 完整 CLI 加载（最后才执行）
  const { main: cliMain } = await import('../main.js')
  await cliMain()
}
```

### 20.7.2 启动性能剖析

```typescript
// src/utils/startupProfiler.js（概念）

// 1. cli_entry            - 0ms   (入口点)
// 2. cli_before_main      - ~5ms  (快速路径检查)
// 3. cli_after_main_import - ~150ms (完整导入)
// 4. cli_after_main       - ~300ms (初始化完成)

profileCheckpoint('cli_entry')
// ... 快速路径检查
profileCheckpoint('cli_before_main_import')
const { main } = await import('../main.js')
profileCheckpoint('cli_after_main_import')
await main()
profileCheckpoint('cli_after_main_complete')
```

---

## 20.8 SDK 打包

### 20.8.1 Agent SDK 特殊处理

```typescript
// src/bridge/replBridge.ts

/**
 * REPL Bridge methods are isolated from full command registry
 * to prevent bloating the Agent SDK bundle.
 */
class ReplBridge {
  // 精简的方法集，避免拉入整个 React 树
}

// src/bridge/sessionIdCompat.ts
/**
 * This module is banned from the sdk.mjs bundle
 * (scripts/build-agent-sdk.sh). Callers that need session IDs
 * in the SDK path should use different approaches.
 */
```

### 20.8.2 打包排除策略

```typescript
// 概念：scripts/build-agent-sdk.sh

# 排除清单（简化）
EXCLUDE_PATTERNS=(
  'src/screens/*'          # 终端 UI
  'src/components/*'       # React 组件
  'src/commands/*'         # CLI 命令
  'src/ink/*'              # Ink 渲染器
  'sessionIdCompat.ts'     # 会话 ID 兼容层
)

# 结果：~0.4MB vs ~10.8MB（完整构建）
```

---

## 20.9 平台特定构建

### 20.9.1 跨平台支持

```typescript
// src/skills/bundledSkills.ts

// Windows 特殊处理
const SAFE_WRITE_FLAGS =
  process.platform === 'win32'
    ? 'wx'  // 字符串标志（避免 EINVAL）
    : fsConstants.O_WRONLY | fsConstants.O_CREAT | 
      fsConstants.O_EXCL | O_NOFOLLOW

// src/keybindings/defaultBindings.ts
// Bun 版本检查
const keybindingsSupported = isRunningWithBun()
  ? satisfies(process.versions.bun, '>=1.2.23')
  : true
```

### 20.9.2 原生依赖处理

```typescript
// src/tools/FileReadTool/imageProcessor.ts

export async function processImage(path: string): Promise<Buffer> {
  // 使用 sharp 用于非打包构建或作为后备
  if (!isInBundledMode()) {
    const sharp = await import('sharp')
    return sharp(path).resize(1024).toBuffer()
  }
  
  // 打包模式下使用内置处理
  // ...
}
```

---

## 20.10 版本管理与更新

### 20.10.1 版本检查

```typescript
// src/cli/update.ts
import { lt, gte, gt } from 'semver'

export async function checkForUpdates(): Promise<void> {
  writeToStdout(`Current version: ${MACRO.VERSION}\n`)
  
  const npmCommand = `npm view ${MACRO.PACKAGE_URL}@latest version`
  const latest = await execCommand(npmCommand)
  
  if (latest && !gte(MACRO.VERSION, latest)) {
    writeToStdout(`Update available: ${MACRO.VERSION} → ${latest}\n`)
  }
}
```

### 20.10.2 最低版本强制

```typescript
// src/bridge/bridgeEnabled.ts

export function checkBridgeMinVersion(): string | null {
  if (feature('BRIDGE_MODE')) {
    const config = await fetchBridgeConfig()
    if (config.minVersion && lt(MACRO.VERSION, config.minVersion)) {
      return `Your version (${MACRO.VERSION}) is too old for Remote Control.
Version ${config.minVersion} or higher is required.
Run \`claude update\` to update.`
    }
  }
  return null
}
```

---

## 20.11 构建优化技巧

### 20.11.1 懒加载大模块

```typescript
// src/skills/bundled/claudeApi.ts

// claudeApiContent.js bundles 247KB of .md strings. Lazy-load inside
// the skill invocation, not at module init.
export function registerClaudeApiSkill(): void {
  registerBundledSkill({
    name: 'claude-api',
    getPromptForCommand: async (args, ctx) => {
      // 只在实际调用时加载 247KB 内容
      const { CLAUDE_API_CONTENT } = await import('./claudeApiContent.js')
      return [{ type: 'text', text: CLAUDE_API_CONTENT }]
    }
  })
}
```

### 20.11.2 条件 require

```typescript
// src/components/Messages.tsx

// 模块级条件加载，支持 tree-shaking
const proactiveModule = feature('PROACTIVE') || feature('KAIROS') 
  ? require('../proactive/index.js') 
  : null

const BRIEF_TOOL_NAME: string | null = 
  feature('KAIROS') || feature('KAIROS_BRIEF') 
    ? require('../tools/BriefTool/prompt.js').BRIEF_TOOL_NAME 
    : null
```

### 20.11.3 生成文件识别

```typescript
// src/utils/generatedFiles.ts

// 在文件搜索中跳过生成/打包产物
const GENERATED_EXTENSIONS = [
  '.bundle.js',
  '.bundle.css',
  '.min.js',
  '.min.css',
]

const GENERATED_PATTERNS = [
  /^.*\.bundle\.[a-z]+$/i,  // *.bundle.*
  /^.*\.min\.[a-z]+$/i,     // *.min.*
  /^dist\//,                // dist/
  /^build\//,               // build/
]
```

---

## 20.12 最佳实践总结

### 20.12.1 特性标志规范

```typescript
// ✅ 正确：正向模式
if (feature('MY_FEATURE')) {
  // 功能代码
}

// ✅ 正确：三元表达式
const module = feature('MY_FEATURE') ? require('./feature.js') : null

// ✅ 正确：动态导入
if (feature('MY_FEATURE')) {
  const { impl } = await import('./feature.js')
}

// ❌ 错误：负向模式
if (!feature('MY_FEATURE')) return

// ❌ 错误：间接引用
const flag = feature('MY_FEATURE')
if (flag) { /* ... */ }
```

### 20.12.2 宏使用规范

```typescript
// 版本显示
console.log(`v${MACRO.VERSION}`)

// 构建信息
const buildInfo = MACRO.BUILD_TIME 
  ? `${MACRO.VERSION} (built ${MACRO.BUILD_TIME})`
  : MACRO.VERSION

// 包引用
const updateCmd = `npm update ${MACRO.PACKAGE_URL}`
```

### 20.12.3 运行时检测

```typescript
// 检测打包模式
if (isInBundledMode()) {
  // 使用嵌入资源
} else {
  // 使用文件系统
}

// 检测 Bun 运行时
if (isRunningWithBun()) {
  // 使用 Bun 特有 API
} else {
  // 使用 Node.js 兼容 API
}
```

---

## 本篇小结

Claude Code 的构建系统展示了现代 TypeScript CLI 应用的工程典范：

1. **`bun:bundle` 特性标志**实现了编译时功能开关，支持同一代码库生成多个不同功能集的构建产物

2. **MACRO 宏注入**将版本号、构建时间等信息在编译时内联，实现零运行时开销的版本查询

3. **快速路径设计**确保常用命令（如 `--version`）几乎零启动延迟

4. **智能打包策略**通过懒加载和条件 require 最小化包体积

5. **跨平台兼容**在保持性能的同时处理 Windows/macOS/Linux 差异

> **下一篇预告**：第 21 篇将深入配置与环境管理系统，探索 Claude Code 如何处理多层配置覆盖、环境变量和企业级配置。
