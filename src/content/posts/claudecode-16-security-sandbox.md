---
title: "安全模型与沙箱机制"
summary: "安全是 AI 代码助手最关键的设计考量。Claude Code 实现了多层防御架构："
publishedAt: 2026-08-12
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 16
seriesGroup: 安全与可观测性
source: "16-安全模型与沙箱机制.md"
sourceSha256: f048da0b25db
---
> **系列**: Claude Code CLI 深度技术拆解 (16/24)  
> **主题**: 沙箱隔离、路径验证、危险命令拦截、权限规则系统  
> **依赖**: 第9篇(权限系统)、第15篇(错误处理)

## 概述

安全是 AI 代码助手最关键的设计考量。Claude Code 实现了多层防御架构：

1. **沙箱隔离 (Sandbox)** - 使用 bubblewrap/sandbox-exec 限制文件系统和网络
2. **路径验证** - 危险路径检测、符号链接解析、UNC 路径拦截
3. **命令分析** - AST 解析 Bash/PowerShell，检测危险模式
4. **权限规则** - Allow/Deny 规则系统，层级化配置

本篇深入分析这些安全机制的实现细节。

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Security Architecture                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐ │
│  │  Sandbox    │   │    Path     │   │   Command   │   │ Permission  │ │
│  │  Runtime    │   │  Validation │   │   Analysis  │   │   Rules     │ │
│  └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘ │
│        │                │                  │                 │          │
│        ▼                ▼                  ▼                 ▼          │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐ │
│  │ bubblewrap  │   │ isDangerous │   │  AST Parse  │   │ Allow/Deny  │ │
│  │ sandbox-exec│   │ UNC Check   │   │  Pattern    │   │ Inheritance │ │
│  │ (macOS/Lin) │   │ Symlink     │   │  Matching   │   │ (sources)   │ │
│  └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘ │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Protected: ~/.claude/settings*, .claude/skills, /etc/*, C:\Windows    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 沙箱运行时 (Sandbox Runtime)

### 2.1 平台支持

```typescript
// restored-src/src/utils/sandbox/sandbox-adapter.ts

// 支持的平台
// - macOS: sandbox-exec (App Sandbox)
// - Linux: bubblewrap (bwrap)
// - WSL2: bubblewrap (WSL1 不支持)
// - Windows native: 不支持

function isSupportedPlatform(): boolean {
  return BaseSandboxManager.isSupportedPlatform()
}

// 平台限制列表
function isPlatformInEnabledList(): boolean {
  const enabledPlatforms = settings?.sandbox?.enabledPlatforms
  
  if (enabledPlatforms === undefined) {
    return true  // 默认所有平台启用
  }
  
  // 企业可以限制特定平台
  // 例如：enabledPlatforms: ["macos"]
  return enabledPlatforms.includes(getPlatform())
}
```

### 2.2 配置转换

```typescript
// 将 Claude Code 设置转换为 SandboxRuntimeConfig

function convertToSandboxRuntimeConfig(settings: SettingsJson): SandboxRuntimeConfig {
  const allowWrite: string[] = ['.', getClaudeTempDir()]
  const denyWrite: string[] = []
  const allowedDomains: string[] = []
  
  // 1. 永远拒绝写入设置文件（防止沙箱逃逸）
  const settingsPaths = SETTING_SOURCES.map(source =>
    getSettingsFilePathForSource(source)
  ).filter(Boolean)
  denyWrite.push(...settingsPaths)
  
  // 2. 保护 .claude/skills（与 .claude/commands 同级权限）
  denyWrite.push(resolve(originalCwd, '.claude', 'skills'))
  
  // 3. 防止裸 Git 仓库攻击（见下文）
  const bareGitRepoFiles = ['HEAD', 'objects', 'refs', 'hooks', 'config']
  for (const gitFile of bareGitRepoFiles) {
    const p = resolve(cwd, gitFile)
    try {
      statSync(p)
      denyWrite.push(p)  // 存在则只读
    } catch {
      bareGitRepoScrubPaths.push(p)  // 不存在则事后清理
    }
  }
  
  // 4. 处理 Git worktree
  if (worktreeMainRepoPath) {
    allowWrite.push(worktreeMainRepoPath)
  }
  
  // 5. 从权限规则提取路径
  for (const source of SETTING_SOURCES) {
    const sourceSettings = getSettingsForSource(source)
    for (const rule of sourceSettings?.permissions?.allow || []) {
      if (rule.toolName === FILE_EDIT_TOOL_NAME) {
        allowWrite.push(resolvePathPatternForSandbox(rule.ruleContent, source))
      }
    }
  }
  
  return {
    network: { allowedDomains, deniedDomains },
    filesystem: { allowWrite, denyWrite, allowRead, denyRead },
    // ...
  }
}
```

### 2.3 裸 Git 仓库攻击防护

```typescript
// SECURITY: Git 的 is_git_directory() 将 cwd 视为裸仓库
// 如果存在 HEAD + objects/ + refs/
// 攻击者可以放置这些文件 + config(core.fsmonitor) 来逃逸沙箱

// 策略：
// 1. 如果文件已存在 → denyWrite（只读绑定）
// 2. 如果文件不存在 → 事后清理（scrubBareGitRepoFiles）

function scrubBareGitRepoFiles(): void {
  for (const p of bareGitRepoScrubPaths) {
    try {
      rmSync(p, { recursive: true })
      logForDebugging(`[Sandbox] scrubbed planted bare-repo file: ${p}`)
    } catch {
      // ENOENT 是预期情况 — 没有被植入
    }
  }
}
```

### 2.4 沙箱不可用时的处理

```typescript
// #34044: 用户显式启用沙箱但依赖缺失时发出警告

function getSandboxUnavailableReason(): string | undefined {
  if (!getSandboxEnabledSetting()) {
    return undefined  // 用户没有启用，不警告
  }
  
  if (!isSupportedPlatform()) {
    const platform = getPlatform()
    if (platform === 'wsl') {
      return 'sandbox.enabled is set but WSL1 is not supported (requires WSL2)'
    }
    return `sandbox.enabled is set but ${platform} is not supported`
  }
  
  const deps = checkDependencies()
  if (deps.errors.length > 0) {
    const hint = platform === 'macos'
      ? 'run /sandbox or /doctor for details'
      : 'install missing tools (e.g. apt install bubblewrap socat)'
    return `sandbox.enabled is set but dependencies are missing: ${deps.errors.join(', ')} · ${hint}`
  }
  
  return undefined
}
```

---

## 3. 路径验证系统

### 3.1 危险路径检测

```typescript
// restored-src/src/utils/permissions/pathValidation.ts

/**
 * 检查路径是否对删除操作危险
 * 危险路径包括：
 * - 通配符 '*'
 * - 根目录 /
 * - 用户主目录 ~
 * - 根的直接子目录 /usr, /tmp, /etc
 * - Windows 驱动器根 C:\, D:\
 */
export function isDangerousRemovalPath(resolvedPath: string): boolean {
  // 统一斜杠
  const forwardSlashed = resolvedPath.replace(/[\\/]+/g, '/')
  
  // 通配符删除
  if (forwardSlashed === '*' || forwardSlashed.endsWith('/*')) {
    return true
  }
  
  const normalizedPath = forwardSlashed.replace(/\/$/, '')
  
  // 根目录
  if (normalizedPath === '/') {
    return true
  }
  
  // Windows 驱动器根
  if (WINDOWS_DRIVE_ROOT_REGEX.test(normalizedPath)) {
    return true
  }
  
  // 用户主目录
  const normalizedHome = homedir().replace(/[\\/]+/g, '/')
  if (normalizedPath === normalizedHome) {
    return true
  }
  
  // 根的直接子目录
  const parentDir = dirname(normalizedPath)
  if (parentDir === '/') {
    return true
  }
  
  // Windows 驱动器直接子目录
  if (WINDOWS_DRIVE_CHILD_REGEX.test(normalizedPath)) {
    return true
  }
  
  return false
}
```

### 3.2 POSIX `--` 参数处理

```typescript
/**
 * SECURITY: 正确处理 POSIX `--` 选项终止符
 * 
 * 大多数命令在 `--` 后将所有参数视为位置参数，
 * 即使它们以 `-` 开头。简单的 `!arg.startsWith('-')` 
 * 过滤会遗漏这些，导致路径验证被绕过：
 * 
 *   rm -- -/../.claude/settings.local.json
 * 
 * `-/../.claude/...` 以 `-` 开头，被简单过滤器跳过，
 * 验证看到零路径，返回 passthrough，文件被删除。
 */
function filterOutFlags(args: string[]): string[] {
  const result: string[] = []
  let afterDoubleDash = false
  
  for (const arg of args) {
    if (afterDoubleDash) {
      result.push(arg)  // -- 后的所有都是位置参数
    } else if (arg === '--') {
      afterDoubleDash = true
    } else if (!arg?.startsWith('-')) {
      result.push(arg)
    }
  }
  
  return result
}
```

### 3.3 Tilde 变体检测

```typescript
// SECURITY: 拒绝 expandTilde 不处理的 ~ 变体

function validatePath(path: string, cwd: string, ...): ResolvedPathCheckResult {
  const cleanPath = expandTilde(path.replace(/^['"]|['"]$/g, ''))
  
  // expandTilde 只处理 ~ 和 ~/，其他变体保持原样
  // ~root, ~+, ~- 被视为相对路径 (如 /cwd/~root)
  // 但 shell 会将它们展开为不同路径（TOCTOU 漏洞）
  // ~root → /var/root, ~+ → $PWD, ~- → $OLDPWD
  
  if (cleanPath.startsWith('~')) {
    return {
      allowed: false,
      resolvedPath: cleanPath,
      decisionReason: {
        type: 'other',
        reason: 'Tilde expansion variants (~user, ~+, ~-) in paths require manual approval',
      },
    }
  }
  
  // ...
}
```

### 3.4 UNC 路径拦截

```typescript
// SECURITY: 阻止可能泄露凭证的 UNC 路径

if (containsVulnerableUncPath(cleanPath)) {
  return {
    allowed: false,
    resolvedPath: cleanPath,
    decisionReason: {
      type: 'other',
      reason: 'UNC network paths require manual approval',
    },
  }
}

// UNC 路径可能导致 NTLM 哈希泄露
// 例如：\\attacker.com\share\file
```

### 3.5 Claude 配置文件保护

```typescript
// restored-src/src/utils/permissions/filesystem.ts

export const DANGEROUS_FILES = [
  '.bashrc',
  '.zshrc', 
  '.bash_profile',
  '.profile',
  // Shell 配置 - 可注入命令
]

export const DANGEROUS_DIRECTORIES = [
  '.ssh',
  '.gnupg',
  '.claude',
  '.aws',
  '.config/gcloud',
  // 敏感目录 - 凭证和配置
]

function isClaudeConfigFilePath(resolvedPath: string): boolean {
  // 保护所有 .claude/ 下的设置文件
  return resolvedPath.includes('.claude') && (
    resolvedPath.endsWith('settings.json') ||
    resolvedPath.endsWith('settings.local.json') ||
    resolvedPath.includes('.claude/commands') ||
    resolvedPath.includes('.claude/skills')
  )
}
```

---

## 4. 命令分析引擎

### 4.1 Bash AST 解析

```typescript
// restored-src/src/utils/bash/ast.ts

/**
 * 这不是沙箱，它不阻止危险命令运行。
 * 它是一个权限检查器，决定是否需要用户确认。
 */

// 解析命令为 AST
function parseShellCommand(command: string): ShellAST {
  // 使用 bash-parser 或自定义解析器
  // 处理：管道、重定向、子 shell、变量展开
}

// 检查命令操作符权限
export async function checkCommandOperatorPermissions(
  command: string,
  context: ToolPermissionContext
): Promise<PermissionResult> {
  const ast = parseShellCommand(command)
  
  // 检查每个简单命令
  for (const cmd of ast.commands) {
    const result = await checkSimpleCommand(cmd, context)
    if (result.behavior === 'deny') {
      return result
    }
  }
  
  return { behavior: 'passthrough' }
}
```

### 4.2 危险 Bash 模式

```typescript
// restored-src/src/utils/permissions/dangerousPatterns.ts

export const DANGEROUS_BASH_PATTERNS: readonly string[] = [
  // 网络数据执行
  'curl.*\\|.*sh',
  'wget.*\\|.*sh',
  'curl.*\\|.*bash',
  
  // 权限提升
  'sudo',
  'su\\s',
  'doas',
  
  // 系统修改
  'chmod.*777',
  'chown.*root',
  
  // 危险删除
  'rm\\s+-rf\\s+/',
  'rm\\s+-rf\\s+~',
  
  // Shell 配置修改
  'echo.*>>.*\\.bashrc',
  'echo.*>>.*\\.zshrc',
  
  // 反向 shell
  '/dev/tcp/',
  'nc.*-e',
  'ncat.*-e',
]
```

### 4.3 sed 命令白名单

```typescript
// restored-src/src/tools/BashTool/sedValidation.ts

// sed 有强大的命令执行能力，需要特殊处理

function sedCommandIsAllowedByAllowlist(
  command: string,
  context: { cwd: string; allowList: string[] }
): boolean {
  // 只允许安全的 sed 操作：
  // - s/pattern/replacement/ 替换
  // - d 删除行
  // - p 打印
  // - n 禁止自动打印
  
  // 禁止：
  // - e 执行命令
  // - w 写入文件（在白名单路径外）
  // - r 读取文件
}
```

### 4.4 PowerShell 安全检查

```typescript
// restored-src/src/tools/PowerShellTool/powershellSecurity.ts

// PowerShell 有不同的攻击面

// 计划任务持久化
// Register-ScheduledJob 被阻止
const BLOCKED_PERSISTENCE_CMDLETS = [
  'Register-ScheduledJob',
  'Register-ScheduledTask',
  'New-ScheduledTaskAction',
]

// 调用表达式检测
function checkInvokeExpression(ast: PowerShellAST): PermissionResult {
  // Invoke-Expression $untrustedInput 是危险的
  // 但 Invoke-Expression "literal" 可能是安全的
}
```

### 4.5 元素类型白名单

```typescript
// restored-src/src/tools/PowerShellTool/readOnlyValidation.ts

/**
 * PowerShell 只读命令验证：
 * 
 * 1. elementTypes 白名单 — StringConstant（字面量）+ Parameter（标志名）
 * 2. 检查命令是否在只读安全列表
 * 3. 验证参数不会泄露变量
 */

// 安全的元素类型
const ALLOWED_ELEMENT_TYPES = new Set([
  'StringConstant',
  'Parameter',
])

// 阻止的元素类型
// Variable → $env:SECRET 泄露
// ScriptBlock → { dangerous code }
// Other (HashtableAst) → @{N='x';E={}}
```

---

## 5. 权限规则系统

### 5.1 规则格式

```typescript
// 权限规则格式
// ToolName(ruleContent)

// 示例：
// Edit(/path/to/file)     - 允许编辑指定路径
// Edit(/path/**)          - 允许编辑目录下所有文件
// Bash(npm:*)             - 允许所有 npm 命令
// WebFetch(domain:api.*)  - 允许指定域名的网络请求

function permissionRuleValueFromString(ruleString: string): PermissionRuleValue {
  const matches = ruleString.match(/^([^(]+)\(([^)]+)\)$/)
  if (!matches) {
    return { toolName: ruleString }
  }
  return {
    toolName: matches[1],
    ruleContent: matches[2],
  }
}
```

### 5.2 路径模式解析

```typescript
// Claude Code 使用特殊路径前缀

function resolvePathPatternForSandbox(pattern: string, source: SettingSource): string {
  // //path → 文件系统根的绝对路径
  if (pattern.startsWith('//')) {
    return pattern.slice(1)  // "//.aws/**" → "/.aws/**"
  }
  
  // /path → 相对于设置文件目录
  if (pattern.startsWith('/') && !pattern.startsWith('//')) {
    const root = getSettingsRootPathForSource(source)
    return resolve(root, pattern.slice(1))
  }
  
  // ~/path, ./path, path → 透传给 sandbox-runtime
  return pattern
}
```

### 5.3 规则优先级

```typescript
// 设置源优先级（从高到低）
const SETTING_SOURCES = [
  'policySettings',    // 企业策略（最高）
  'userSettings',      // 用户全局
  'projectSettings',   // 项目级
  'localSettings',     // 本地私有（最低）
]

// Deny 规则优先于 Allow
function isPathAllowed(resolvedPath: string, context: ToolPermissionContext): PathCheckResult {
  // 1. 首先检查 deny 规则
  const denyRule = matchingRuleForInput(resolvedPath, context, 'edit', 'deny')
  if (denyRule !== null) {
    return {
      allowed: false,
      decisionReason: { type: 'rule', rule: denyRule },
    }
  }
  
  // 2. 然后检查内部可编辑路径
  // 3. 然后检查安全性验证
  // 4. 最后检查工作目录
}
```

### 5.4 策略模式

```typescript
// 企业可以强制特定配置

// sandbox.network.allowManagedDomainsOnly: true
// 只允许策略设置中的域名
function shouldAllowManagedSandboxDomainsOnly(): boolean {
  return getSettingsForSource('policySettings')
    ?.sandbox?.network?.allowManagedDomainsOnly === true
}

// sandbox.filesystem.allowManagedReadPathsOnly: true
// 只允许策略设置中的读取路径
function shouldAllowManagedReadPathsOnly(): boolean {
  return getSettingsForSource('policySettings')
    ?.sandbox?.filesystem?.allowManagedReadPathsOnly === true
}
```

---

## 6. 自动批准机制

### 6.1 沙箱内自动批准

```typescript
// sandbox.autoAllowBashIfSandboxed: true
// 沙箱内的 Bash 命令自动批准

function isAutoAllowBashIfSandboxedEnabled(): boolean {
  return settings?.sandbox?.autoAllowBashIfSandboxed ?? true  // 默认开启
}

// 检查是否可以自动批准
function shouldAutoAllowBash(command: string, context: ToolPermissionContext): boolean {
  if (!SandboxManager.isSandboxingEnabled()) {
    return false
  }
  
  if (!isAutoAllowBashIfSandboxedEnabled()) {
    return false
  }
  
  // 检查命令是否在排除列表
  if (isCommandExcludedFromSandbox(command)) {
    return false
  }
  
  return true
}
```

### 6.2 排除命令

```typescript
// 用户可以排除特定命令不使用沙箱

// settings.sandbox.excludedCommands: ["npm run test:*"]
function isCommandExcludedFromSandbox(command: string): boolean {
  const excludedCommands = settings.sandbox?.excludedCommands ?? []
  
  for (const pattern of excludedCommands) {
    if (pattern.includes('*')) {
      // 通配符匹配
      if (minimatch(command, pattern)) {
        return true
      }
    } else {
      // 精确匹配
      if (command.startsWith(pattern)) {
        return true
      }
    }
  }
  
  return false
}
```

---

## 7. 网络限制

### 7.1 域名白名单

```typescript
// sandbox.network.allowedDomains
// 从 WebFetch 规则和直接配置提取

const allowedDomains: string[] = []

// 从 WebFetch(domain:xxx) 规则提取
for (const rule of permissions.allow || []) {
  if (rule.toolName === WEB_FETCH_TOOL_NAME && 
      rule.ruleContent?.startsWith('domain:')) {
    allowedDomains.push(rule.ruleContent.substring('domain:'.length))
  }
}

// 从直接配置
for (const domain of settings.sandbox?.network?.allowedDomains || []) {
  allowedDomains.push(domain)
}
```

### 7.2 Unix Socket 控制

```typescript
// Unix socket 访问控制

interface NetworkRestrictionConfig {
  allowedDomains: string[]
  deniedDomains: string[]
  
  // Unix socket 选项
  allowUnixSockets?: string[]      // 允许的 socket 路径
  allowAllUnixSockets?: boolean    // 允许所有 socket
  allowLocalBinding?: boolean      // 允许本地端口绑定
  
  // 代理端口
  httpProxyPort?: number
  socksProxyPort?: number
}
```

### 7.3 沙箱网络权限请求

```typescript
// 当命令尝试访问未授权的网络时

type SandboxAskCallback = (hostPattern: NetworkHostPattern) => Promise<'allow' | 'deny'>

// REPL 中的处理
const sandboxAskCallback: SandboxAskCallback = async (hostPattern) => {
  // 显示权限请求对话框
  // 用户可以选择：
  // - 允许一次
  // - 允许并记住（添加到规则）
  // - 拒绝
  
  return await showNetworkPermissionDialog(hostPattern)
}
```

---

## 8. Windows 特殊处理

### 8.1 Windows 原生沙箱限制

```typescript
// Windows 原生不支持沙箱（bwrap/sandbox-exec 是 POSIX 专用）

const WINDOWS_SANDBOX_POLICY_REFUSAL = 
  'Enterprise policy requires sandboxing, but sandboxing is not available on native Windows. ' +
  'Shell command execution is blocked on this platform by policy.'

function isWindowsSandboxPolicyBlocked(): boolean {
  return getPlatform() === 'windows' && 
         SandboxManager.isSandboxEnabledInSettings() && 
         !SandboxManager.areUnsandboxedCommandsAllowed()
}
```

### 8.2 Windows 驱动器保护

```typescript
// 保护 Windows 系统目录

const WINDOWS_DRIVE_ROOT_REGEX = /^[A-Za-z]:[\\/]?$/
const WINDOWS_DRIVE_CHILD_REGEX = /^[A-Za-z]:[\\/][^\\\/]+[\\/]?$/

// 危险路径示例：
// C:\            - 驱动器根
// C:\Windows     - 系统目录
// C:\Users       - 用户目录根
// C:\Program Files - 程序目录
```

---

## 9. 沙箱诊断

### 9.1 /sandbox 命令

```typescript
// /sandbox 命令提供沙箱状态和配置

// 状态指示器
let statusText = 'sandbox disabled'
if (SandboxManager.isSandboxingEnabled()) {
  statusText = isAutoAllowBashIfSandboxedEnabled()
    ? 'sandbox enabled (auto-allow)'
    : 'sandbox enabled'
    
  // 显示 fallback 状态
  statusText += areUnsandboxedCommandsAllowed() 
    ? ', fallback allowed' 
    : ''
}
```

### 9.2 依赖检查

```typescript
// /doctor 中的沙箱诊断

function checkDependencies(): SandboxDependencyCheck {
  const { rgPath, rgArgs } = ripgrepCommand()
  
  return BaseSandboxManager.checkDependencies({
    command: rgPath,
    args: rgArgs,
  })
  
  // 返回：
  // { errors: string[], warnings: string[] }
  // errors: 阻止沙箱运行的问题
  // warnings: 可能影响功能的问题
}
```

---

## 10. 安全日志

### 10.1 违规事件

```typescript
// 沙箱违规事件记录

interface SandboxViolationEvent {
  timestamp: number
  type: 'network' | 'filesystem'
  target: string    // 被访问的路径或域名
  action: 'read' | 'write' | 'connect'
  command: string   // 触发违规的命令
  allowed: boolean  // 是否被允许
}

// 存储违规记录
const violationStore = new SandboxViolationStore()
```

### 10.2 分析追踪

```typescript
// 记录沙箱相关事件

logEvent('tengu_sandbox_command_executed', {
  sandbox_enabled: SandboxManager.isSandboxingEnabled(),
  auto_allow_enabled: isAutoAllowBashIfSandboxedEnabled(),
  command_excluded: isCommandExcludedFromSandbox(command),
})

logEvent('tengu_sandbox_violation', {
  violation_type: event.type,
  target: sanitizePath(event.target),  // 不记录完整路径
  action: event.action,
  allowed: event.allowed,
})
```

---

## 总结

Claude Code 的安全模型是一个深度防御系统：

### 多层防护

1. **沙箱隔离** - OS 级别的文件系统和网络限制
2. **路径验证** - 危险路径、符号链接、UNC 路径检测
3. **命令分析** - AST 解析检测危险模式
4. **权限规则** - Allow/Deny 规则的层级化管理

### 关键安全设计

- **裸 Git 仓库防护** - 防止通过 `core.fsmonitor` 逃逸
- **POSIX `--` 处理** - 防止通过参数注入绕过
- **Tilde 变体检测** - 防止 TOCTOU 攻击
- **设置文件保护** - 防止沙箱配置被修改

### 企业功能

- **策略优先级** - 企业策略覆盖用户设置
- **平台限制** - 可限制特定平台启用沙箱
- **域名管理** - `allowManagedDomainsOnly` 强制使用白名单

---

## 下一篇预告

[第17篇：扩展系统与插件架构](/claudecode/17-extension-plugins) - 深入分析 MCP 集成、自定义工具注册、生命周期钩子和插件市场的实现。

---
