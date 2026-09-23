---
title: "扩展系统与插件架构"
summary: "Claude Code 的扩展系统采用三层架构设计："
publishedAt: 2026-08-22
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 17
seriesGroup: 安全与可观测性
source: "17-扩展系统与插件架构.md"
sourceSha256: 0dcc00f6860c
---
> **摘要**：深入分析 Claude Code 的扩展系统架构，包括 MCP 服务器集成、插件加载机制、生命周期钩子系统、官方注册表和市场化体系的完整实现。

## 目录
- [架构概览](#架构概览)
- [MCP 服务器集成](#mcp-服务器集成)
- [插件系统](#插件系统)
- [生命周期钩子](#生命周期钩子)
- [官方注册表与市场](#官方注册表与市场)
- [设计亮点](#设计亮点)
- [总结](#总结)

---

## 架构概览

Claude Code 的扩展系统采用三层架构设计：

```
┌─────────────────────────────────────────────────────────────────┐
│                        扩展能力层                                │
├─────────────────┬─────────────────┬─────────────────────────────┤
│   MCP 服务器     │   插件系统       │   生命周期钩子              │
│  (工具/资源)     │  (命令/代理)     │  (事件回调)                 │
├─────────────────┴─────────────────┴─────────────────────────────┤
│                        集成层                                    │
│  连接管理 │ 配置加载 │ 权限控制 │ 版本控制 │ 热更新              │
├─────────────────────────────────────────────────────────────────┤
│                        基础设施层                                │
│  传输协议 │ 认证系统 │ 缓存机制 │ 错误处理 │ 遥测分析            │
└─────────────────────────────────────────────────────────────────┘
```

### 核心类型定义

```typescript
// src/services/mcp/types.ts

// 配置作用域层级
export type ConfigScope = 
  | 'local'      // .mcp.json
  | 'user'       // 用户全局
  | 'project'    // 项目级
  | 'dynamic'    // 运行时动态
  | 'enterprise' // 企业托管
  | 'claudeai'   // claude.ai 连接器
  | 'managed'    // 托管配置

// 传输协议类型
export type Transport = 
  | 'stdio'    // 标准输入输出（本地进程）
  | 'sse'      // Server-Sent Events
  | 'sse-ide'  // IDE 专用 SSE
  | 'http'     // Streamable HTTP
  | 'ws'       // WebSocket
  | 'sdk'      // 内部 SDK

// 服务器连接状态
export type MCPServerConnection =
  | ConnectedMCPServer   // 已连接
  | FailedMCPServer      // 连接失败
  | NeedsAuthMCPServer   // 需要认证
  | PendingMCPServer     // 连接中（含重连信息）
  | DisabledMCPServer    // 已禁用
```

---

## MCP 服务器集成

### 连接管理器

连接管理器是 MCP 系统的核心，负责服务器的生命周期管理：

```typescript
// src/services/mcp/useManageMCPConnections.ts

// 重连配置：指数退避
const MAX_RECONNECT_ATTEMPTS = 5
const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000

export function useManageMCPConnections(
  dynamicMcpConfig: Record<string, ScopedMcpServerConfig> | undefined,
  isStrictMcpConfig = false,
) {
  // 批量更新优化：16ms 时间窗口合并更新
  const MCP_BATCH_FLUSH_MS = 16
  
  const flushPendingUpdates = useCallback(() => {
    const updates = pendingUpdatesRef.current
    if (updates.length === 0) return
    pendingUpdatesRef.current = []

    setAppState(prevState => {
      let mcp = prevState.mcp
      for (const update of updates) {
        // 合并工具、命令、资源更新
        mcp = {
          ...mcp,
          clients: updatedClients,
          tools: updatedTools,
          commands: updatedCommands,
          resources: updatedResources,
        }
      }
      return { ...prevState, mcp }
    })
  }, [setAppState])

  // 服务器状态更新（带批处理）
  const updateServer = useCallback((update: PendingUpdate) => {
    pendingUpdatesRef.current.push(update)
    if (flushTimerRef.current === null) {
      flushTimerRef.current = setTimeout(
        flushPendingUpdates,
        MCP_BATCH_FLUSH_MS,
      )
    }
  }, [flushPendingUpdates])
}
```

**设计亮点**：
- **批量更新**：16ms 时间窗口合并多个服务器状态更新，减少 React 重渲染
- **指数退避**：重连失败时使用 1s → 2s → 4s → 8s → 30s 退避策略
- **优雅降级**：SSE/HTTP 传输支持自动重连，stdio/sdk 不支持

### 多传输协议支持

```typescript
// src/services/mcp/client.ts

const connectToServer = async (
  name: string,
  serverRef: ScopedMcpServerConfig,
): Promise<MCPServerConnection> => {
  let transport

  if (serverRef.type === 'sse') {
    // SSE 传输：OAuth + 自定义 Headers
    const authProvider = new ClaudeAuthProvider(name, serverRef)
    const combinedHeaders = await getMcpServerHeaders(name, serverRef)
    
    transport = new SSEClientTransport(
      new URL(serverRef.url),
      {
        authProvider,
        fetch: wrapFetchWithTimeout(
          wrapFetchWithStepUpDetection(createFetchWithInit(), authProvider)
        ),
        // EventSource 长连接不使用超时
        eventSourceInit: { fetch: customSSEFetch },
      }
    )
  } else if (serverRef.type === 'http') {
    // Streamable HTTP：支持双向通信
    transport = new StreamableHTTPClientTransport(
      new URL(serverRef.url),
      { authProvider, fetch: wrapFetchWithTimeout(fetchWithAuth) }
    )
  } else if (serverRef.type === 'ws') {
    // WebSocket：支持代理和 TLS
    const wsClient = await createNodeWsClient(serverRef.url, {
      headers: wsHeaders,
      agent: getWebSocketProxyAgent(serverRef.url),
      ...tlsOptions,
    })
    transport = new WebSocketTransport(wsClient)
  } else if (serverRef.type === 'stdio' || !serverRef.type) {
    // 进程内服务器优化
    if (isClaudeInChromeMCPServer(name)) {
      // Chrome MCP 服务器：进程内运行避免 325MB 子进程
      inProcessServer = createClaudeForChromeMcpServer(context)
      const [clientTransport, serverTransport] = createLinkedTransportPair()
      await inProcessServer.connect(serverTransport)
      transport = clientTransport
    } else {
      // 标准 stdio 传输
      transport = new StdioClientTransport({
        command: finalCommand,
        args: finalArgs,
        env: { ...subprocessEnv(), ...serverRef.env },
        stderr: 'pipe', // 防止错误输出污染 UI
      })
    }
  }

  // 创建客户端并连接
  const client = new Client({
    name: 'claude-code',
    version: MACRO.VERSION,
    capabilities: { roots: {}, elicitation: {} },
  })
  
  await client.connect(transport)
  return { client, name, type: 'connected', ... }
}
```

### 工具动态注册

MCP 工具被封装为 Claude Code 的 `Tool` 接口：

```typescript
// src/services/mcp/client.ts

export const fetchToolsForClient = memoizeWithLRU(
  async (client: MCPServerConnection): Promise<Tool[]> => {
    if (client.type !== 'connected') return []
    
    // 获取工具列表
    const result = await client.client.request(
      { method: 'tools/list' },
      ListToolsResultSchema,
    )
    
    // 清理 Unicode 字符
    const toolsToProcess = recursivelySanitizeUnicode(result.tools)
    
    return toolsToProcess.map((tool): Tool => {
      const fullyQualifiedName = buildMcpToolName(client.name, tool.name)
      // 格式: mcp__servername__toolname
      
      return {
        ...MCPTool,  // 基础 MCP 工具模板
        name: fullyQualifiedName,
        mcpInfo: { serverName: client.name, toolName: tool.name },
        isMcp: true,
        
        // 工具元数据
        searchHint: tool._meta?.['anthropic/searchHint'],
        alwaysLoad: tool._meta?.['anthropic/alwaysLoad'] === true,
        
        // 动态描述（截断到 2048 字符）
        async description() {
          return tool.description ?? ''
        },
        async prompt() {
          const desc = tool.description ?? ''
          return desc.length > MAX_MCP_DESCRIPTION_LENGTH
            ? desc.slice(0, MAX_MCP_DESCRIPTION_LENGTH) + '… [truncated]'
            : desc
        },
        
        // 工具特性（来自 MCP annotations）
        isConcurrencySafe: () => tool.annotations?.readOnlyHint ?? false,
        isReadOnly: () => tool.annotations?.readOnlyHint ?? false,
        isDestructive: () => tool.annotations?.destructiveHint ?? false,
        isOpenWorld: () => tool.annotations?.openWorldHint ?? false,
        
        // 权限检查
        async checkPermissions() {
          return {
            behavior: 'passthrough',
            suggestions: [{
              type: 'addRules',
              rules: [{ toolName: fullyQualifiedName, ruleContent: undefined }],
              behavior: 'allow',
              destination: 'localSettings',
            }],
          }
        },
        
        // 工具调用（含会话恢复重试）
        async call(args, context, _canUseTool, parentMessage, onProgress) {
          const MAX_SESSION_RETRIES = 1
          for (let attempt = 0; ; attempt++) {
            try {
              const connectedClient = await ensureConnectedClient(client)
              const mcpResult = await callMCPToolWithUrlElicitationRetry({
                client: connectedClient,
                tool: tool.name,
                args,
                signal: context.abortController.signal,
              })
              return { data: mcpResult.content }
            } catch (error) {
              if (error instanceof McpSessionExpiredError && 
                  attempt < MAX_SESSION_RETRIES) {
                continue  // 会话过期，重试
              }
              throw error
            }
          }
        },
      }
    }).filter(isIncludedMcpTool)
  },
  (client) => client.name,
  MCP_FETCH_CACHE_SIZE,  // LRU 缓存 20 个服务器
)
```

### 配置来源与合并策略

```typescript
// src/services/mcp/config.ts

// MCP 服务器配置来源优先级
// 1. Enterprise (managed-mcp.json) - 最高优先级
// 2. Dynamic (--mcp-config CLI 参数)
// 3. Claude.ai (连接器)
// 4. Project (.mcp.json)
// 5. User (用户全局配置)

// 服务器签名去重
export function getMcpServerSignature(config: McpServerConfig): string | null {
  const cmd = getServerCommandArray(config)
  if (cmd) {
    return `stdio:${jsonStringify(cmd)}`  // stdio 服务器：命令+参数
  }
  const url = getServerUrl(config)
  if (url) {
    return `url:${unwrapCcrProxyUrl(url)}`  // 远程服务器：解包后的 URL
  }
  return null  // SDK 服务器无签名
}

// 插件 MCP 服务器去重
export function dedupPluginMcpServers(
  pluginServers: Record<string, ScopedMcpServerConfig>,
  manualServers: Record<string, ScopedMcpServerConfig>,
): { servers: Record<string, ScopedMcpServerConfig>; suppressed: Array<...> } {
  // 手动配置优先于插件
  // 先加载的插件优先于后加载的
  const manualSigs = new Map<string, string>()
  for (const [name, config] of Object.entries(manualServers)) {
    const sig = getMcpServerSignature(config)
    if (sig && !manualSigs.has(sig)) manualSigs.set(sig, name)
  }

  const servers: Record<string, ScopedMcpServerConfig> = {}
  const suppressed: Array<{ name: string; duplicateOf: string }> = []
  
  for (const [name, config] of Object.entries(pluginServers)) {
    const sig = getMcpServerSignature(config)
    const manualDup = manualSigs.get(sig)
    if (manualDup !== undefined) {
      // 抑制：与手动配置重复
      suppressed.push({ name, duplicateOf: manualDup })
      continue
    }
    servers[name] = config
  }
  return { servers, suppressed }
}
```

### 认证缓存机制

```typescript
// src/services/mcp/client.ts

const MCP_AUTH_CACHE_TTL_MS = 15 * 60 * 1000  // 15 分钟

// 需要认证的服务器状态缓存
type McpAuthCacheData = Record<string, { timestamp: number }>

// 串行化写入防止竞争条件
let writeChain = Promise.resolve()

function setMcpAuthCacheEntry(serverId: string): void {
  writeChain = writeChain.then(async () => {
    const cache = await getMcpAuthCache()
    cache[serverId] = { timestamp: Date.now() }
    await writeFile(cachePath, jsonStringify(cache))
    authCachePromise = null  // 使读缓存失效
  }).catch(() => {})
}

// 多个 401 响应到达时共享单次文件读取
let authCachePromise: Promise<McpAuthCacheData> | null = null

function getMcpAuthCache(): Promise<McpAuthCacheData> {
  if (!authCachePromise) {
    authCachePromise = readFile(getMcpAuthCachePath(), 'utf-8')
      .then(data => jsonParse(data))
      .catch(() => ({}))
  }
  return authCachePromise
}
```

---

## 插件系统

### 插件目录结构

```
my-plugin/
├── .claude-plugin/       # 新标准位置
│   └── plugin.json       # 插件清单
├── plugin.json           # 旧位置（兼容）
├── commands/             # 自定义斜杠命令
│   ├── build.md
│   └── deploy.md
├── agents/               # 自定义 AI 代理
│   └── test-runner.md
└── hooks/                # 钩子配置
    └── hooks.json
```

### 插件加载流程

```typescript
// src/utils/plugins/pluginLoader.ts

/**
 * 插件发现来源（优先级顺序）：
 * 1. Marketplace 插件（plugin@marketplace 格式）
 * 2. 会话插件（--plugin-dir CLI 参数或 SDK plugins 选项）
 */

export const loadAllPluginsCacheOnly = memoize(
  async (): Promise<PluginLoadResult> => {
    const enabled: LoadedPlugin[] = []
    const disabled: LoadedPlugin[] = []
    const errors: PluginError[] = []

    // 1. 加载内置插件
    const { enabled: builtinEnabled, disabled: builtinDisabled } = getBuiltinPlugins()
    enabled.push(...builtinEnabled)
    disabled.push(...builtinDisabled)

    // 2. 加载 Marketplace 插件
    const enabledPlugins = settings.enabledPlugins ?? {}
    for (const [pluginId, isEnabled] of Object.entries(enabledPlugins)) {
      if (isBuiltinPluginId(pluginId)) continue
      
      // 策略检查
      if (!isSourceAllowedByPolicy(pluginId)) {
        errors.push({ type: 'policy_blocked', ... })
        continue
      }
      
      // 阻止列表检查
      if (isSourceInBlocklist(pluginId)) {
        errors.push({ type: 'blocklist', ... })
        continue
      }

      const plugin = await loadPluginFromMarketplace(pluginId)
      if (isEnabled) {
        enabled.push(plugin)
      } else {
        disabled.push(plugin)
      }
    }

    // 3. 加载会话插件（--plugin-dir）
    for (const dir of getInlinePlugins()) {
      const plugin = await loadPluginFromDirectory(dir)
      enabled.push({ ...plugin, enabled: true })
    }

    return { enabled, disabled, errors }
  }
)
```

### 版本化缓存系统

```typescript
// src/utils/plugins/pluginLoader.ts

// 版本化缓存路径
// 格式: ~/.claude/plugins/cache/{marketplace}/{plugin}/{version}/
export function getVersionedCachePath(pluginId: string, version: string): string {
  const { name, marketplace } = parsePluginIdentifier(pluginId)
  const sanitizedMarketplace = (marketplace || 'unknown').replace(/[^a-zA-Z0-9\-_]/g, '-')
  const sanitizedPlugin = (name || pluginId).replace(/[^a-zA-Z0-9\-_]/g, '-')
  const sanitizedVersion = version.replace(/[^a-zA-Z0-9\-_.]/g, '-')
  
  return join(
    getPluginsDirectory(),
    'cache',
    sanitizedMarketplace,
    sanitizedPlugin,
    sanitizedVersion,
  )
}

// 种子目录探测（BYOC 场景）
async function probeSeedCache(pluginId: string, version: string): Promise<string | null> {
  for (const seedDir of getPluginSeedDirs()) {
    const seedPath = getVersionedCachePathIn(seedDir, pluginId, version)
    try {
      const entries = await readdir(seedPath)
      if (entries.length > 0) return seedPath  // 找到已填充的缓存
    } catch {
      // 继续尝试下一个种子目录
    }
  }
  return null
}

// 复制到版本化缓存（原子操作）
export async function copyPluginToVersionedCache(
  sourcePath: string,
  pluginId: string,
  version: string,
): Promise<string> {
  const cachePath = getVersionedCachePath(pluginId, version)
  
  // 检查是否已缓存
  if (await pathExists(cachePath)) {
    const entries = await readdir(cachePath)
    if (entries.length > 0) return cachePath
  }
  
  // 检查种子缓存
  const seedPath = await probeSeedCache(pluginId, version)
  if (seedPath) return seedPath  // 直接使用种子（只读）
  
  // 复制插件
  await copyDir(sourcePath, cachePath)
  
  // 删除 .git 目录
  await rm(join(cachePath, '.git'), { recursive: true, force: true })
  
  return cachePath
}
```

### 内置插件注册

```typescript
// src/plugins/builtinPlugins.ts

const BUILTIN_PLUGINS: Map<string, BuiltinPluginDefinition> = new Map()
export const BUILTIN_MARKETPLACE_NAME = 'builtin'

// 注册内置插件
export function registerBuiltinPlugin(definition: BuiltinPluginDefinition): void {
  BUILTIN_PLUGINS.set(definition.name, definition)
}

// 获取内置插件状态
export function getBuiltinPlugins(): {
  enabled: LoadedPlugin[]
  disabled: LoadedPlugin[]
} {
  const settings = getSettings_DEPRECATED()
  const enabled: LoadedPlugin[] = []
  const disabled: LoadedPlugin[] = []

  for (const [name, definition] of BUILTIN_PLUGINS) {
    // 可用性检查
    if (definition.isAvailable && !definition.isAvailable()) {
      continue
    }

    const pluginId = `${name}@${BUILTIN_MARKETPLACE_NAME}`
    const userSetting = settings?.enabledPlugins?.[pluginId]
    // 优先级: 用户设置 > 插件默认 > true
    const isEnabled = userSetting !== undefined
      ? userSetting === true
      : (definition.defaultEnabled ?? true)

    const plugin: LoadedPlugin = {
      name,
      manifest: { name, description: definition.description, version: definition.version },
      path: BUILTIN_MARKETPLACE_NAME,  // 哨兵值，无文件系统路径
      source: pluginId,
      enabled: isEnabled,
      isBuiltin: true,
      hooksConfig: definition.hooks,
      mcpServers: definition.mcpServers,
    }

    if (isEnabled) {
      enabled.push(plugin)
    } else {
      disabled.push(plugin)
    }
  }

  return { enabled, disabled }
}
```

---

## 生命周期钩子

### 钩子事件类型

```typescript
// src/utils/plugins/loadPluginHooks.ts

type HookEvent =
  // 工具相关
  | 'PreToolUse'          // 工具执行前
  | 'PostToolUse'         // 工具执行后
  | 'PostToolUseFailure'  // 工具执行失败
  | 'PermissionDenied'    // 权限拒绝
  | 'PermissionRequest'   // 权限请求
  
  // 会话相关
  | 'SessionStart'        // 会话开始
  | 'SessionEnd'          // 会话结束
  | 'UserPromptSubmit'    // 用户提交提示
  
  // 任务相关
  | 'TaskCreated'         // 任务创建
  | 'TaskCompleted'       // 任务完成
  | 'Stop'                // 停止
  | 'StopFailure'         // 停止失败
  
  // 代理相关
  | 'SubagentStart'       // 子代理启动
  | 'SubagentStop'        // 子代理停止
  | 'TeammateIdle'        // 队友空闲
  
  // 上下文相关
  | 'PreCompact'          // 压缩前
  | 'PostCompact'         // 压缩后
  | 'InstructionsLoaded'  // 指令加载
  
  // 环境相关
  | 'Setup'               // 设置
  | 'Notification'        // 通知
  | 'ConfigChange'        // 配置变更
  | 'CwdChanged'          // 工作目录变更
  | 'FileChanged'         // 文件变更
  | 'WorktreeCreate'      // 工作树创建
  | 'WorktreeRemove'      // 工作树移除
  
  // 交互相关
  | 'Elicitation'         // 引出请求
  | 'ElicitationResult'   // 引出结果
```

### 钩子加载与注册

```typescript
// src/utils/plugins/loadPluginHooks.ts

// 将插件钩子配置转换为原生匹配器
function convertPluginHooksToMatchers(plugin: LoadedPlugin): Record<HookEvent, PluginHookMatcher[]> {
  const pluginMatchers: Record<HookEvent, PluginHookMatcher[]> = {
    PreToolUse: [],
    PostToolUse: [],
    // ... 所有事件类型
  }

  if (!plugin.hooksConfig) return pluginMatchers

  for (const [event, matchers] of Object.entries(plugin.hooksConfig)) {
    const hookEvent = event as HookEvent
    for (const matcher of matchers) {
      if (matcher.hooks.length > 0) {
        pluginMatchers[hookEvent].push({
          matcher: matcher.matcher,
          hooks: matcher.hooks,
          pluginRoot: plugin.path,
          pluginName: plugin.name,
          pluginId: plugin.source,
        })
      }
    }
  }

  return pluginMatchers
}

// 加载并注册所有启用插件的钩子
export const loadPluginHooks = memoize(async (): Promise<void> => {
  const { enabled } = await loadAllPluginsCacheOnly()
  const allPluginHooks: Record<HookEvent, PluginHookMatcher[]> = { ... }

  for (const plugin of enabled) {
    if (!plugin.hooksConfig) continue
    const pluginMatchers = convertPluginHooksToMatchers(plugin)
    
    // 合并到主集合
    for (const event of Object.keys(pluginMatchers) as HookEvent[]) {
      allPluginHooks[event].push(...pluginMatchers[event])
    }
  }

  // 原子交换：先清除再注册
  clearRegisteredPluginHooks()
  registerHookCallbacks(allPluginHooks)
})
```

### 钩子执行引擎

```typescript
// src/utils/hooks.ts

async function* executeHooks({
  hookInput,
  toolUseID,
  matchQuery,
  signal,
  timeoutMs = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
  toolUseContext,
  messages,
}: ExecuteHooksParams): AsyncGenerator<AggregatedHookResult> {
  // 安全检查：所有钩子需要工作区信任
  if (shouldSkipHookDueToTrust()) {
    logForDebugging(`Skipping ${hookName} hook - workspace trust not accepted`)
    return
  }

  const matchingHooks = await getMatchingHooks(
    appState, sessionId, hookEvent, hookInput, tools
  )
  if (matchingHooks.length === 0) return

  // 快速路径：纯回调钩子（无需 JSON 序列化）
  const userHooks = matchingHooks.filter(h => !isInternalHook(h))
  if (userHooks.length === 0) {
    // 内部回调直接执行，跳过 span/progress/序列化
    for (const [i, { hook }] of matchingHooks.entries()) {
      if (hook.type === 'callback') {
        await hook.callback(hookInput, toolUseID, signal, i, context)
      }
    }
    return
  }

  // 惰性 JSON 序列化（整个批次共享）
  let jsonInputResult: { ok: true; value: string } | { ok: false; error: unknown }
  function getJsonInput() {
    if (jsonInputResult !== undefined) return jsonInputResult
    try {
      return (jsonInputResult = { ok: true, value: jsonStringify(hookInput) })
    } catch (error) {
      return (jsonInputResult = { ok: false, error })
    }
  }

  // 并行执行所有钩子（各自独立超时）
  const hookPromises = matchingHooks.map(async function* (
    { hook, pluginRoot, pluginId },
    hookIndex,
  ): AsyncGenerator<HookResult> {
    const commandTimeoutMs = hook.timeout ? hook.timeout * 1000 : timeoutMs
    const { signal: abortSignal, cleanup } = createCombinedAbortSignal(signal, {
      timeoutMs: commandTimeoutMs,
    })

    try {
      if (hook.type === 'callback') {
        yield executeHookCallback({ hook, hookInput, signal: abortSignal, ... })
      } else if (hook.type === 'prompt') {
        yield await execPromptHook(hook, hookName, hookEvent, getJsonInput(), ...)
      } else if (hook.type === 'command') {
        yield await execCommandHook(hook, getJsonInput(), abortSignal, pluginRoot, ...)
      } else if (hook.type === 'http') {
        yield await execHttpHook(hook, getJsonInput(), abortSignal, ...)
      }
    } finally {
      cleanup()
    }
  })

  // 聚合结果
  for await (const result of mergeAsyncGenerators(hookPromises)) {
    yield result
  }
}
```

### 热更新机制

```typescript
// src/utils/plugins/loadPluginHooks.ts

let lastPluginSettingsSnapshot: string | undefined

// 构建稳定的设置快照用于变更检测
export function getPluginAffectingSettingsSnapshot(): string {
  const merged = getSettings_DEPRECATED()
  const policy = getSettingsForSource('policySettings')
  
  // 排序键以确保确定性比较
  const sortKeys = <T extends Record<string, unknown>>(o: T | undefined) =>
    o ? Object.fromEntries(Object.entries(o).sort()) : {}
  
  return jsonStringify({
    enabledPlugins: sortKeys(merged.enabledPlugins),
    extraKnownMarketplaces: sortKeys(merged.extraKnownMarketplaces),
    strictKnownMarketplaces: policy?.strictKnownMarketplaces ?? [],
    blockedMarketplaces: policy?.blockedMarketplaces ?? [],
  })
}

// 设置热更新订阅
export function setupPluginHookHotReload(): void {
  if (hotReloadSubscribed) return
  hotReloadSubscribed = true

  lastPluginSettingsSnapshot = getPluginAffectingSettingsSnapshot()

  settingsChangeDetector.subscribe(source => {
    if (source === 'policySettings') {
      const newSnapshot = getPluginAffectingSettingsSnapshot()
      if (newSnapshot === lastPluginSettingsSnapshot) {
        logForDebugging('Plugin hooks: skipping reload, settings unchanged')
        return
      }

      lastPluginSettingsSnapshot = newSnapshot
      clearPluginCache('plugin-affecting settings changed')
      clearPluginHookCache()
      void loadPluginHooks()  // 异步重载
    }
  })
}
```

---

## 官方注册表与市场

### MCP 官方注册表

```typescript
// src/services/mcp/officialRegistry.ts

// 规范化 URL（去除查询字符串和尾斜杠）
let officialUrls: Set<string> | undefined = undefined

export async function prefetchOfficialMcpUrls(): Promise<void> {
  if (process.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC) return

  try {
    const response = await axios.get<RegistryResponse>(
      'https://api.anthropic.com/mcp-registry/v0/servers?version=latest&visibility=commercial',
      { timeout: 5000 },
    )

    const urls = new Set<string>()
    for (const entry of response.data.servers) {
      for (const remote of entry.server.remotes ?? []) {
        const normalized = normalizeUrl(remote.url)
        if (normalized) urls.add(normalized)
      }
    }
    officialUrls = urls
    logForDebugging(`[mcp-registry] Loaded ${urls.size} official MCP URLs`)
  } catch (error) {
    logForDebugging(`Failed to fetch MCP registry: ${errorMessage(error)}`)
  }
}

// 检查 URL 是否在官方注册表中
export function isOfficialMcpUrl(normalizedUrl: string): boolean {
  return officialUrls?.has(normalizedUrl) ?? false  // 未知时 fail-closed
}
```

### 插件市场管理

```typescript
// src/utils/plugins/marketplaceManager.ts

// 市场配置结构
interface KnownMarketplace {
  name: string
  url: string
  type: 'git' | 'api'
  official?: boolean
}

// 获取已知市场列表（合并官方+用户配置+企业策略）
export async function loadKnownMarketplacesConfigSafe(): Promise<KnownMarketplace[]> {
  const official = await fetchOfficialMarketplaces()
  const userExtra = settings.extraKnownMarketplaces ?? {}
  const policyStrict = policySettings?.strictKnownMarketplaces ?? []
  const policyBlocked = policySettings?.blockedMarketplaces ?? []

  // 如果设置了严格市场列表，只使用这些
  if (policyStrict.length > 0) {
    return policyStrict.map(name => ({
      name,
      url: getMarketplaceUrl(name),
      type: 'git',
      official: false,
    }))
  }

  // 否则合并所有来源，排除阻止的
  const all = [...official]
  for (const [name, url] of Object.entries(userExtra)) {
    if (!policyBlocked.includes(name)) {
      all.push({ name, url, type: 'git', official: false })
    }
  }

  return all.filter(m => !policyBlocked.includes(m.name))
}
```

### 企业策略控制

```typescript
// src/services/mcp/config.ts

// 只允许托管 MCP 服务器的策略
function shouldAllowManagedMcpServersOnly(): boolean {
  const policy = getSettingsForSource('policySettings')
  return policy?.allowManagedMcpServersOnly === true
}

// MCP 服务器允许/拒绝列表检查
function isMcpServerAllowedByPolicy(
  serverName: string,
  config?: McpServerConfig,
): boolean {
  // 拒绝列表绝对优先
  if (isMcpServerDenied(serverName, config)) return false

  const settings = getMcpAllowlistSettings()
  if (!settings.allowedMcpServers) return true  // 无限制
  if (settings.allowedMcpServers.length === 0) return false  // 空列表 = 全部阻止

  // 根据服务器类型检查对应的条目
  if (config) {
    const serverCommand = getServerCommandArray(config)
    const serverUrl = getServerUrl(config)

    if (serverCommand) {
      // stdio 服务器：检查命令条目
      for (const entry of settings.allowedMcpServers) {
        if (isMcpServerCommandEntry(entry) &&
            commandArraysMatch(entry.serverCommand, serverCommand)) {
          return true
        }
      }
    } else if (serverUrl) {
      // 远程服务器：检查 URL 条目（支持通配符）
      for (const entry of settings.allowedMcpServers) {
        if (isMcpServerUrlEntry(entry) &&
            urlMatchesPattern(serverUrl, entry.serverUrl)) {
          return true
        }
      }
    }
  }

  // 回退到名称检查
  for (const entry of settings.allowedMcpServers) {
    if (isMcpServerNameEntry(entry) && entry.serverName === serverName) {
      return true
    }
  }

  return false
}

// URL 通配符匹配
function urlMatchesPattern(url: string, pattern: string): boolean {
  // "https://example.com/*" → matches "https://example.com/api/v1"
  // "https://*.example.com/*" → matches "https://api.example.com/path"
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&')
  const regexStr = escaped.replace(/\*/g, '.*')
  return new RegExp(`^${regexStr}$`).test(url)
}
```

---

## 设计亮点

### 1. 进程内 MCP 服务器

```typescript
// 避免 325MB 子进程开销
if (isClaudeInChromeMCPServer(name)) {
  const { createClaudeForChromeMcpServer } = await import('@ant/claude-for-chrome-mcp')
  const { createLinkedTransportPair } = await import('./InProcessTransport.js')
  
  inProcessServer = createClaudeForChromeMcpServer(context)
  const [clientTransport, serverTransport] = createLinkedTransportPair()
  await inProcessServer.connect(serverTransport)
  transport = clientTransport
}
```

### 2. 会话过期自动恢复

```typescript
// 检测 MCP 会话过期（HTTP 404 + JSON-RPC -32001）
export function isMcpSessionExpiredError(error: Error): boolean {
  const httpStatus = 'code' in error ? error.code : undefined
  if (httpStatus !== 404) return false
  
  // MCP 服务器返回: {"error":{"code":-32001,"message":"Session not found"}}
  return error.message.includes('"code":-32001') ||
         error.message.includes('"code": -32001')
}

// 工具调用中自动重试
async call(args, context, ...) {
  const MAX_SESSION_RETRIES = 1
  for (let attempt = 0; ; attempt++) {
    try {
      return await callMCPTool(...)
    } catch (error) {
      if (error instanceof McpSessionExpiredError && attempt < MAX_SESSION_RETRIES) {
        continue  // 缓存已清除，下次调用将重连
      }
      throw error
    }
  }
}
```

### 3. 钩子快速路径

```typescript
// 纯回调钩子跳过序列化和进度追踪
// 测量: 6.01µs → ~1.8µs per PostToolUse (-70%)
const userHooks = matchingHooks.filter(h => !isInternalHook(h))
if (userHooks.length === 0) {
  for (const [i, { hook }] of matchingHooks.entries()) {
    if (hook.type === 'callback') {
      await hook.callback(hookInput, toolUseID, signal, i, context)
    }
  }
  return  // 提前返回
}
```

### 4. CCR 代理 URL 解包

```typescript
// 远程会话中，claude.ai 连接器 URL 被重写为 CCR 代理格式
// 原始 URL 保存在 mcp_url 查询参数中
const CCR_PROXY_PATH_MARKERS = [
  '/v2/session_ingress/shttp/mcp/',
  '/v2/ccr-sessions/',
]

export function unwrapCcrProxyUrl(url: string): string {
  if (!CCR_PROXY_PATH_MARKERS.some(m => url.includes(m))) {
    return url
  }
  try {
    const parsed = new URL(url)
    return parsed.searchParams.get('mcp_url') || url
  } catch {
    return url
  }
}
```

### 5. 原子钩子交换

```typescript
// 防止 clearAllCaches() 后钩子消失
// 之前：clearPluginHookCache() 清除 STATE.registeredHooks
// 现在：清除和注册作为原子对，旧钩子保持有效直到新钩子准备好
export const loadPluginHooks = memoize(async () => {
  // ... 收集所有钩子 ...
  
  // 原子交换
  clearRegisteredPluginHooks()
  registerHookCallbacks(allPluginHooks)
})
```

---

## 总结

Claude Code 的扩展系统展示了企业级插件架构的设计典范：

### 核心能力
1. **MCP 集成**：支持 6 种传输协议，自动重连，会话恢复
2. **插件系统**：版本化缓存，市场支持，内置插件框架
3. **钩子系统**：26 种事件类型，并行执行，热更新

### 性能优化
- 批量状态更新（16ms 窗口）
- LRU 缓存（20 服务器）
- 快速路径（纯回调 -70% 延迟）
- 惰性 JSON 序列化

### 安全机制
- 工作区信任检查
- 企业策略控制
- 允许/拒绝列表
- URL 通配符匹配

### 扩展性
- 多来源配置合并
- 签名去重
- 种子目录支持
- 进程内服务器

---

**相关文件**：
- `src/services/mcp/` - MCP 服务器集成
- `src/utils/plugins/` - 插件系统实现
- `src/utils/hooks.ts` - 钩子执行引擎
- `src/plugins/builtinPlugins.ts` - 内置插件注册

---

> **下一篇预告**：[第18篇：多模态支持](/claudecode/18-multimodal) - 深入分析图像处理、Vision API 集成、内容类型检测和多模态消息处理的实现。
