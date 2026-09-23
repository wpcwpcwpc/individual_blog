---
title: "配置与环境管理"
summary: "│                     CONFIGURATION PRIORITY STACK                         │"
publishedAt: 2026-09-16
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 21
seriesGroup: 工程支撑
source: "21-配置与环境管理.md"
sourceSha256: fff8c22aa022
---
> **核心洞察**：Claude Code 实现了一套 **六层配置优先级系统**，从企业策略到运行时标志，每一层都有明确的用途和安全边界。这套设计既满足企业级部署的管控需求，又保留了开发者的灵活性。

---

## 21.1 配置架构总览

### 21.1.1 六层配置模型

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     CONFIGURATION PRIORITY STACK                         │
│                      (Higher = Higher Priority)                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ 6. flagSettings (Highest)                                        │    │
│  │    Source: CLI --settings flag / SDK inline settings             │    │
│  │    Use: Runtime overrides, testing, SDK integrations             │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ▲                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ 5. localSettings                                                 │    │
│  │    Source: .claude/settings.local.json (gitignored)              │    │
│  │    Use: Personal machine-specific config, secrets                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ▲                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ 4. projectSettings                                               │    │
│  │    Source: .claude/settings.json (version controlled)           │    │
│  │    Use: Team-shared project configuration                        │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ▲                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ 3. userSettings                                                  │    │
│  │    Source: ~/.claude/settings.json                               │    │
│  │    Use: Personal global preferences                               │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ▲                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ 2. policySettings (Enterprise)                                   │    │
│  │    Sources (first wins):                                         │    │
│  │    - Remote managed settings (API sync)                          │    │
│  │    - HKLM Registry / macOS plist (MDM)                           │    │
│  │    - /etc/claude/managed-settings.json + .d/                     │    │
│  │    - HKCU Registry (Windows user)                                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ▲                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ 1. GlobalConfig (Lowest)                                         │    │
│  │    Source: ~/.claude.json                                        │    │
│  │    Use: Legacy config, user preferences, session state           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 21.1.2 核心设计原则

| 原则 | 实现 |
|------|------|
| **优先级明确** | 高层覆盖低层，`flagSettings` 总是最高 |
| **企业优先** | `policySettings` 可强制覆盖用户设置 |
| **安全边界** | `projectSettings` 是攻击者可控的，受信任度最低 |
| **合并策略** | 数组字段累加合并，对象字段深度合并 |
| **缓存一致** | 设置变更触发缓存失效，确保实时生效 |

---

## 21.2 GlobalConfig（遗留全局配置）

### 21.2.1 文件位置

```typescript
// src/utils/config.ts
// 默认: ~/.claude.json
// 可通过 CLAUDE_CONFIG_DIR 覆盖
export function getGlobalClaudeFile(): string {
  return join(getClaudeConfigHomeDir(), '.claude.json')
}
```

### 21.2.2 核心字段

```typescript
// src/utils/config.ts
export type GlobalConfig = {
  // 用户身份
  userID?: string
  oauthAccount?: AccountInfo
  primaryApiKey?: string

  // 会话状态
  numStartups: number
  firstStartTime?: string
  lastOnboardingVersion?: string
  
  // UI 偏好
  theme: ThemeSetting
  editorMode?: EditorMode          // 'normal' | 'vim'
  verbose: boolean
  diffTool?: DiffTool              // 'terminal' | 'auto'
  showTurnDuration: boolean
  
  // 功能开关
  autoCompactEnabled: boolean
  todoFeatureEnabled: boolean
  fileCheckpointingEnabled: boolean
  terminalProgressBarEnabled: boolean
  
  // 通知
  preferredNotifChannel: NotificationChannel
  messageIdleNotifThresholdMs: number
  
  // MCP 服务器
  mcpServers?: Record<string, McpServerConfig>
  claudeAiMcpEverConnected?: string[]
  
  // 项目级配置缓存
  projects?: Record<string, ProjectConfig>
  
  // 提示历史
  tipsHistory: Record<string, number>
  
  // @deprecated - 迁移到 settings.json
  apiKeyHelper?: string
  env: Record<string, string>
}
```

### 21.2.3 配置访问与保存

```typescript
// src/utils/config.ts

// 获取配置（带缓存）
export function getGlobalConfig(): GlobalConfig {
  // 防止重入：logEvent → GrowthBook → getConfig 循环
  if (insideGetConfig) {
    return cachedGlobalConfig ?? DEFAULT_GLOBAL_CONFIG
  }
  insideGetConfig = true
  try {
    // ... 读取和解析逻辑
  } finally {
    insideGetConfig = false
  }
}

// 保存配置（原子写入）
export function saveGlobalConfig(
  update: Partial<GlobalConfig> | ((prev: GlobalConfig) => GlobalConfig)
): void {
  const current = getGlobalConfig()
  const updated = typeof update === 'function' 
    ? update(current) 
    : { ...current, ...update }
  
  writeFileSyncAndFlush_DEPRECATED(
    getGlobalClaudeFile(),
    JSON.stringify(updated, null, 2)
  )
}
```

---

## 21.3 Settings 系统（现代配置）

### 21.3.1 设置来源类型

```typescript
// src/utils/settings/constants.ts

export type SettingSource = 
  | 'userSettings'      // ~/.claude/settings.json
  | 'projectSettings'   // .claude/settings.json
  | 'localSettings'     // .claude/settings.local.json
  | 'policySettings'    // managed-settings.json / MDM / Remote
  | 'flagSettings'      // CLI --settings / SDK inline

export type EditableSettingSource = 
  | 'userSettings' 
  | 'projectSettings' 
  | 'localSettings'
```

### 21.3.2 SettingsJson Schema

```typescript
// src/utils/settings/types.ts

export const SettingsSchema = () => z.object({
  // API 配置
  apiKeyHelper: z.string().optional(),
  apiBaseUrl: z.string().optional(),
  
  // 环境变量注入
  env: EnvironmentVariablesSchema().optional(),
  
  // 权限规则
  permissions: PermissionsSchema().optional(),
  
  // MCP 服务器
  mcpServers: z.record(McpServerConfigSchema()).optional(),
  enableAllProjectMcpServers: z.boolean().optional(),
  allowedMcpServers: z.array(AllowedMcpServerEntrySchema()).optional(),
  deniedMcpServers: z.array(DeniedMcpServerEntrySchema()).optional(),
  
  // 钩子系统
  hooks: HooksSchema().optional(),
  disableAllHooks: z.boolean().optional(),
  allowManagedHooksOnly: z.boolean().optional(),
  
  // 工作树配置
  worktree: z.object({
    symlinkDirectories: z.array(z.string()).optional(),
    sparsePaths: z.array(z.string()).optional(),
  }).optional(),
  
  // 企业管控
  allowManagedPermissionRulesOnly: z.boolean().optional(),
  allowManagedMcpServersOnly: z.boolean().optional(),
  strictPluginOnlyCustomization: z.union([
    z.boolean(),
    z.array(z.enum(['skills', 'agents', 'hooks', 'mcp']))
  ]).optional(),
  
  // 插件系统
  enabledPlugins: z.record(z.union([
    z.array(z.string()),
    z.boolean()
  ])).optional(),
  extraKnownMarketplaces: z.record(ExtraKnownMarketplaceSchema()).optional(),
  strictKnownMarketplaces: z.array(MarketplaceSourceSchema()).optional(),
  
  // 用户偏好
  language: z.string().optional(),
  outputStyle: z.string().optional(),
  defaultShell: z.enum(['bash', 'powershell']).optional(),
  
  // 沙箱配置
  sandbox: SandboxSettingsSchema().optional(),
  
  // 遥测
  otelHeadersHelper: z.string().optional(),
})
```

### 21.3.3 设置合并策略

```typescript
// src/utils/settings/settings.ts

/**
 * 自定义合并器：数组字段累加，对象字段递归合并
 */
function settingsMergeCustomizer(
  objValue: unknown,
  srcValue: unknown,
  key: string
): unknown {
  // 数组字段：累加（去重由调用方处理）
  if (Array.isArray(objValue) && Array.isArray(srcValue)) {
    return [...objValue, ...srcValue]
  }
  // 对象字段：递归合并
  // undefined：使用 lodash 默认行为
  return undefined
}

// 合并所有来源
export function getInitialSettings(): SettingsJson {
  const sources = getEnabledSettingSources()
  let merged: SettingsJson = {}
  
  for (const source of sources) {
    const settings = getSettingsForSource(source)
    if (settings) {
      merged = mergeWith(merged, settings, settingsMergeCustomizer)
    }
  }
  
  return merged
}
```

---

## 21.4 企业级策略配置

### 21.4.1 策略来源优先级

```typescript
// src/utils/settings/settings.ts

function getSettingsForSourceUncached(source: 'policySettings'): SettingsJson | null {
  // 1. 远程托管设置（最高）
  const remoteSettings = getRemoteManagedSettingsSyncFromCache()
  if (remoteSettings && Object.keys(remoteSettings).length > 0) {
    return remoteSettings
  }

  // 2. MDM 设置（HKLM / macOS plist）
  const mdmResult = getMdmSettings()
  if (Object.keys(mdmResult.settings).length > 0) {
    return mdmResult.settings
  }

  // 3. 文件系统策略
  const { settings: fileSettings } = loadManagedFileSettings()
  if (fileSettings) {
    return fileSettings
  }

  // 4. HKCU 注册表（Windows 用户级）
  const hkcu = getHkcuSettings()
  if (Object.keys(hkcu.settings).length > 0) {
    return hkcu.settings
  }

  return null
}
```

### 21.4.2 Drop-in 配置目录

```typescript
// src/utils/settings/settings.ts

/**
 * 加载 managed-settings.json + managed-settings.d/*.json
 * 
 * 合并顺序（从低到高优先级）：
 * 1. managed-settings.json（基础配置）
 * 2. managed-settings.d/10-otel.json
 * 3. managed-settings.d/20-security.json
 * 4. ...（按字母顺序）
 * 
 * 遵循 systemd/sudoers drop-in 约定
 */
export function loadManagedFileSettings(): {
  settings: SettingsJson | null
  errors: ValidationError[]
} {
  let merged: SettingsJson = {}
  
  // 基础文件
  const { settings: base } = parseSettingsFile(getManagedSettingsFilePath())
  if (base) {
    merged = mergeWith(merged, base, settingsMergeCustomizer)
  }
  
  // drop-in 目录
  const dropInDir = getManagedSettingsDropInDir()
  const entries = fs.readdirSync(dropInDir)
    .filter(d => d.name.endsWith('.json') && !d.name.startsWith('.'))
    .sort()
  
  for (const name of entries) {
    const { settings } = parseSettingsFile(join(dropInDir, name))
    if (settings) {
      merged = mergeWith(merged, settings, settingsMergeCustomizer)
    }
  }
  
  return { settings: merged, errors: [] }
}
```

### 21.4.3 企业管控开关

```typescript
// 权限规则锁定
{
  "allowManagedPermissionRulesOnly": true
  // 效果：忽略用户/项目级的 permissions.allow/deny/ask
}

// MCP 服务器白名单
{
  "allowManagedMcpServersOnly": true,
  "allowedMcpServers": [
    { "serverName": "approved-server" },
    { "serverUrl": "https://*.company.com/*" }
  ]
}

// 强制插件模式
{
  "strictPluginOnlyCustomization": ["skills", "hooks", "mcp"]
  // 效果：禁用 ~/.claude/skills/、.claude/skills/ 等目录
  // 只允许通过插件系统加载自定义内容
}

// 插件市场白名单
{
  "strictKnownMarketplaces": [
    { "source": "github", "repo": "company/approved-plugins" }
  ]
}
```

---

## 21.5 环境变量系统

### 21.5.1 核心环境变量

```typescript
// 配置目录
CLAUDE_CONFIG_DIR         // 覆盖 ~/.claude
CLAUDE_CODE_REMOTE        // 远程环境标识

// API 配置
ANTHROPIC_API_KEY         // 直接 API 密钥
CLAUDE_API_KEY            // 别名
ANTHROPIC_AUTH_TOKEN      // OAuth token

// API 端点
ANTHROPIC_BASE_URL        // 自定义 API 端点
BEDROCK_MODEL_ID          // AWS Bedrock 模型
VERTEX_MODEL_ID           // GCP Vertex 模型

// 区域配置（Vertex）
CLOUD_ML_REGION           // 默认 Vertex 区域
VERTEX_REGION_CLAUDE_4_0_SONNET  // 模型专用区域覆盖
VERTEX_REGION_CLAUDE_4_5_SONNET
VERTEX_REGION_CLAUDE_4_1_OPUS

// AWS 配置
AWS_REGION                // AWS 区域
AWS_DEFAULT_REGION        // 备用区域
AWS_ACCESS_KEY_ID         // 访问密钥
AWS_SECRET_ACCESS_KEY     // 密钥

// 功能开关
CLAUDE_CODE_SIMPLE        // --bare 模式
DISABLE_COMPACT           // 禁用压缩
DISABLE_AUTO_COMPACT      // 禁用自动压缩
CLAUDE_CODE_DISABLE_AUTO_MEMORY  // 禁用自动记忆
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS  // 禁用后台任务
```

### 21.5.2 环境变量注入

```typescript
// src/utils/settings/types.ts

// settings.json 中的 env 字段
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.internal.company.com",
    "NODE_EXTRA_CA_CERTS": "/etc/ssl/company-ca.pem"
  }
}

// 注入顺序：
// 1. 进程环境变量
// 2. GlobalConfig.env（已弃用）
// 3. settings.json env（按优先级合并）
```

### 21.5.3 环境变量工具函数

```typescript
// src/utils/envUtils.ts

/**
 * 检查环境变量是否为真值
 */
export function isEnvTruthy(envVar: string | undefined): boolean {
  if (!envVar) return false
  const normalized = envVar.toLowerCase().trim()
  return ['1', 'true', 'yes', 'on'].includes(normalized)
}

/**
 * 检查环境变量是否明确为假
 */
export function isEnvDefinedFalsy(envVar: string | undefined): boolean {
  if (envVar === undefined) return false
  const normalized = envVar.toLowerCase().trim()
  return ['0', 'false', 'no', 'off'].includes(normalized)
}

/**
 * --bare / CLAUDE_CODE_SIMPLE 模式检测
 * 跳过 hooks、LSP、插件同步、凭证读取
 */
export function isBareMode(): boolean {
  return (
    isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE) ||
    process.argv.includes('--bare')
  )
}
```

---

## 21.6 项目级配置

### 21.6.1 ProjectConfig 结构

```typescript
// src/utils/config.ts

export type ProjectConfig = {
  // 工具权限
  allowedTools: string[]
  
  // MCP 配置
  mcpContextUris: string[]
  mcpServers?: Record<string, McpServerConfig>
  enabledMcpjsonServers?: string[]
  disabledMcpjsonServers?: string[]
  
  // 会话统计
  lastAPIDuration?: number
  lastCost?: number
  lastSessionId?: string
  lastModelUsage?: Record<string, ModelUsageStats>
  
  // 信任状态
  hasTrustDialogAccepted?: boolean
  hasCompletedProjectOnboarding?: boolean
  hasClaudeMdExternalIncludesApproved?: boolean
  
  // 工作树
  activeWorktreeSession?: {
    originalCwd: string
    worktreePath: string
    worktreeName: string
    sessionId: string
  }
  remoteControlSpawnMode?: 'same-dir' | 'worktree'
}
```

### 21.6.2 项目配置路径

```typescript
// src/utils/config.ts

// 项目配置存储在 ~/.claude.json 的 projects 字段中
// Key 是规范化的项目路径
export function getCurrentProjectConfig(): ProjectConfig {
  const cwd = getCwd()
  const globalConfig = getGlobalConfig()
  const projectKey = normalizePathForConfigKey(cwd)
  
  return globalConfig.projects?.[projectKey] ?? DEFAULT_PROJECT_CONFIG
}

export function saveCurrentProjectConfig(
  update: Partial<ProjectConfig>
): void {
  const cwd = getCwd()
  const projectKey = normalizePathForConfigKey(cwd)
  
  saveGlobalConfig(prev => ({
    ...prev,
    projects: {
      ...prev.projects,
      [projectKey]: {
        ...(prev.projects?.[projectKey] ?? DEFAULT_PROJECT_CONFIG),
        ...update,
      },
    },
  }))
}
```

---

## 21.7 配置文件监视

### 21.7.1 文件变更监控

```typescript
// src/utils/config.ts

// 监视配置文件变更
let configWatcher: fs.FSWatcher | null = null

export function enableConfigs(): void {
  const configPath = getGlobalClaudeFile()
  
  // 监视文件变更
  configWatcher = watchFile(configPath, { interval: 1000 }, () => {
    // 清除缓存，下次访问时重新加载
    cachedGlobalConfig = null
    logForDebugging('Config file changed, cache invalidated')
  })
  
  // 注册清理回调
  registerCleanup(() => {
    if (configWatcher) {
      unwatchFile(configPath)
      configWatcher = null
    }
  })
}
```

### 21.7.2 设置缓存管理

```typescript
// src/utils/settings/settingsCache.ts

// 每个来源的缓存
const settingsCache = new Map<SettingSource, SettingsJson | null>()

// 解析文件缓存（避免重复解析）
const parsedFileCache = new Map<string, ParsedSettingsFile>()

export function resetSettingsCache(): void {
  settingsCache.clear()
  parsedFileCache.clear()
}

// 会话级设置缓存（SDK 场景）
let sessionSettingsCache: SettingsJson | null = null

export function setSessionSettingsCache(settings: SettingsJson): void {
  sessionSettingsCache = settings
  settingsCache.clear() // 触发重新合并
}
```

---

## 21.8 配置验证系统

### 21.8.1 Zod Schema 验证

```typescript
// src/utils/settings/validation.ts

export function parseSettingsFile(path: string): {
  settings: SettingsJson | null
  errors: ValidationError[]
} {
  const content = readFileSync(path)
  const data = safeParseJSON(content)
  
  // 预处理：过滤无效权限规则
  const ruleWarnings = filterInvalidPermissionRules(data, path)
  
  // Schema 验证
  const result = SettingsSchema().safeParse(data)
  
  if (!result.success) {
    const errors = formatZodError(result.error, path)
    return { settings: null, errors: [...ruleWarnings, ...errors] }
  }
  
  return { settings: result.data, errors: ruleWarnings }
}
```

### 21.8.2 错误报告

```typescript
// src/utils/settings/validation.ts

export type ValidationError = {
  path: string           // 文件路径
  field?: string         // 字段路径（如 "permissions.allow[0]"）
  message: string        // 错误描述
  severity: 'error' | 'warning'
}

export function formatZodError(
  error: z.ZodError,
  filePath: string
): ValidationError[] {
  return error.issues.map(issue => ({
    path: filePath,
    field: issue.path.join('.'),
    message: issue.message,
    severity: 'error',
  }))
}
```

---

## 21.9 配置迁移

### 21.9.1 迁移框架

```typescript
// src/migrations/*.ts

// 迁移示例：将 bypassPermissionsModeAccepted 从 GlobalConfig 迁移到 settings.json
export async function migrateBypassPermissionsAcceptedToSettings(): Promise<void> {
  const config = getGlobalConfig()
  
  if (config.bypassPermissionsModeAccepted) {
    // 写入新位置
    await updateSettingsForSource('userSettings', {
      permissions: {
        skipDangerousModePermissionPrompt: true,
      },
    })
    
    // 删除旧字段
    saveGlobalConfig(prev => {
      const { bypassPermissionsModeAccepted, ...rest } = prev
      return rest
    })
  }
}
```

### 21.9.2 迁移注册

```typescript
// src/migrations/index.ts

const migrations = [
  migrateBypassPermissionsAcceptedToSettings,
  migrateAutoUpdatesToSettings,
  migrateEnableAllProjectMcpServersToSettings,
  migrateSonnet1mToSonnet45,
  migrateSonnet45ToSonnet46,
  // ... 更多迁移
]

export async function runMigrations(): Promise<void> {
  for (const migration of migrations) {
    try {
      await migration()
    } catch (e) {
      logError(e)
    }
  }
}
```

---

## 21.10 安全考量

### 21.10.1 信任边界

```typescript
// 信任级别（从高到低）
// 1. policySettings - 管理员控制，完全信任
// 2. userSettings   - 用户控制，高度信任
// 3. localSettings  - 用户控制，高度信任
// 4. projectSettings - 可能受攻击者控制，低信任

// src/utils/settings/settings.ts
// SECURITY: projectSettings (.claude/settings.json committed to the repo) is
// attacker-controllable in some threat models. Sensitive operations should
// only trust policySettings, userSettings, or localSettings.
```

### 21.10.2 敏感设置保护

```typescript
// 只从信任源读取的设置
const TRUSTED_ONLY_SETTINGS = [
  'apiKeyHelper',           // API 密钥获取命令
  'otelHeadersHelper',      // OTEL 头获取命令
  'allowManagedHooksOnly',  // 企业钩子锁定
]

// 实现
function getSettingWithTrust<K extends keyof SettingsJson>(
  key: K,
  trustedSources: SettingSource[] = ['policySettings', 'userSettings', 'localSettings']
): SettingsJson[K] | undefined {
  for (const source of trustedSources) {
    const settings = getSettingsForSource(source)
    if (settings?.[key] !== undefined) {
      return settings[key]
    }
  }
  return undefined
}
```

---

## 21.11 /config 命令实现

### 21.11.1 交互式配置编辑

```typescript
// src/tools/ConfigTool/ConfigTool.ts

const SUPPORTED_SETTINGS = {
  theme: { type: 'enum', values: THEMES },
  language: { type: 'string' },
  outputStyle: { type: 'string' },
  autoCompactEnabled: { type: 'boolean' },
  todoFeatureEnabled: { type: 'boolean' },
  verbose: { type: 'boolean' },
  // ... 更多设置
}

export async function handleConfigCommand(
  args: string,
  context: ToolUseContext
): Promise<void> {
  if (!args) {
    // 显示当前配置
    return showCurrentConfig(context)
  }
  
  const [key, value] = parseConfigArgs(args)
  
  if (!(key in SUPPORTED_SETTINGS)) {
    throw new Error(`Unknown setting: ${key}`)
  }
  
  // 验证并保存
  const validated = validateSetting(key, value)
  await updateSetting(key, validated)
}
```

---

## 21.12 最佳实践

### 21.12.1 企业部署

```json
// /etc/claude/managed-settings.json
{
  "$schema": "https://claude.ai/schemas/settings.json",
  
  "permissions": {
    "defaultMode": "ask",
    "deny": [
      "Bash(rm -rf *)",
      "Bash(curl * | bash)"
    ]
  },
  
  "allowManagedPermissionRulesOnly": true,
  "allowManagedMcpServersOnly": true,
  "strictPluginOnlyCustomization": true,
  
  "allowedMcpServers": [
    { "serverName": "company-tools" },
    { "serverUrl": "https://mcp.company.internal/*" }
  ],
  
  "strictKnownMarketplaces": [
    { "source": "github", "repo": "company/approved-plugins" }
  ]
}
```

### 21.12.2 项目配置

```json
// .claude/settings.json (版本控制)
{
  "permissions": {
    "allow": [
      "Bash(npm test)",
      "Bash(npm run build)"
    ]
  },
  
  "hooks": {
    "PreToolUse": [{
      "matcher": "Edit",
      "hooks": [{ "type": "command", "command": "npm run lint --fix {{file}}" }]
    }]
  },
  
  "worktree": {
    "symlinkDirectories": ["node_modules", ".cache"]
  }
}
```

### 21.12.3 个人配置

```json
// ~/.claude/settings.json
{
  "language": "chinese",
  "outputStyle": "concise",
  
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com"
  },
  
  "mcpServers": {
    "my-tools": {
      "command": "node",
      "args": ["~/.claude/mcp/my-server.js"]
    }
  }
}
```

---

## 本篇小结

Claude Code 的配置系统展示了企业级 CLI 工具的配置设计典范：

1. **六层优先级栈**确保从企业策略到运行时标志都有明确的覆盖规则

2. **策略优先**机制允许企业管理员通过 MDM、远程同步或文件系统锁定关键设置

3. **信任边界**明确区分可信和不可信的配置来源，保护敏感操作

4. **合并策略**通过数组累加和对象深度合并，实现灵活的配置组合

5. **迁移框架**支持配置结构的平滑演进

> **下一篇预告**：第 22 篇将深入日志与调试系统，探索 Claude Code 如何实现多级日志、诊断收集和性能追踪。
