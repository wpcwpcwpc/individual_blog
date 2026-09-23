---
title: "模型上下文协议 (MCP) 深度集成"
summary: "想象 Claude Code 是一个新员工："
publishedAt: 2026-06-23
tags: ["Claude Code", "Agent", "源码解析"]
series: claudecode
seriesOrder: 8
seriesGroup: 工具与能力扩展
source: "08-模型上下文协议(MCP)深度集成.md"
sourceSha256: 55d10add4184
---
> **系列**：Claude Code CLI 深度解构  
> **前置阅读**：07-核心工具实现.md  
> **范围**：MCP 协议集成、服务器管理、动态工具生成、认证流程

---

## 🎯 一句话理解

> **MCP 就像是 AI 的"应用商店"**——让 AI 可以动态安装新能力（连接数据库、调用 API、访问文件系统），而不需要修改核心代码。

---

## 🔮 直觉建设：为什么需要 MCP？

想象 Claude Code 是一个新员工：

| 场景 | 无 MCP | 有 MCP |
|------|--------|--------|
| "查一下数据库" | "对不起，我不会" | 连接 PostgreSQL MCP 服务器 → 查到了 |
| "调用公司 API" | "我没有这个功能" | 连接公司 API MCP 服务器 → 调用成功 |
| "读 Notion 文档" | "我没法访问" | 连接 Notion MCP 服务器 → 读到了 |

**核心洞察**：

```
MCP 解决的核心问题：如何让 AI 能力"可扩展"？

方案 A：硬编码所有功能
  问题：功能无限多，无法全部内置
  
方案 B：让用户写代码
  问题：太复杂，普通用户不会

方案 C：MCP 协议（标准化插件）
  ✓ 定义标准接口：Tools、Resources、Prompts
  ✓ 任何人都可以写 MCP 服务器
  ✓ Claude Code 自动发现和使用
```

### MCP 的"即插即用"原理

```
就像 USB 让设备标准化：

USB 协议：
  设备 → USB 接口 → 电脑
  鼠标、键盘、U盘 都用同样的接口

MCP 协议：
  服务器 → MCP 接口 → Claude Code
  数据库、API、文件系统 都用同样的接口

关键：
  Claude Code 不需要知道"怎么连 PostgreSQL"
  只需要知道"怎么调用 MCP 工具"
  具体实现由 MCP 服务器负责
```

---

## 1. MCP 概述

**模型上下文协议 (Model Context Protocol, MCP)** 是 Anthropic 设计的开放协议，允许 AI 应用与外部数据源和工具进行标准化通信。Claude Code 深度集成了 MCP，使其能够：

1. **动态扩展工具集**：连接 MCP 服务器以获取新工具
2. **访问外部资源**：读取 MCP 服务器提供的资源（文件、数据库等）
3. **执行远程命令**：通过 MCP 服务器执行外部系统的操作

```
┌─────────────────────────────────────────────────────────────────┐
│                     Claude Code MCP 架构                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│   │  MCP Client  │────▶│  Transport   │────▶│  MCP Server  │   │
│   │  (Claude)    │◀────│    Layer     │◀────│  (External)  │   │
│   └──────────────┘     └──────────────┘     └──────────────┘   │
│          │                    │                    │           │
│          │                    ▼                    │           │
│          │         ┌───────────────────┐           │           │
│          │         │  Transport Types  │           │           │
│          │         ├───────────────────┤           │           │
│          │         │ • stdio (子进程)  │           │           │
│          │         │ • sse (HTTP SSE)  │           │           │
│          │         │ • http (REST)     │           │           │
│          │         │ • ws (WebSocket)  │           │           │
│          │         │ • sdk (进程内)    │           │           │
│          │         └───────────────────┘           │           │
│          │                                         │           │
│          ▼                                         ▼           │
│   ┌──────────────┐                         ┌──────────────┐   │
│   │   MCPTool    │◀───── 工具定义 ─────────│    Tools     │   │
│   │   Wrappers   │                         │  Resources   │   │
│   └──────────────┘                         │   Prompts    │   │
│                                            └──────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 传输类型与配置

### 2.1 配置 Schema 定义

```typescript
// 配置作用域
export const ConfigScopeSchema = z.enum([
  'local',      // 本地会话
  'user',       // 用户级（~/.claude/）
  'project',    // 项目级（.mcp.json）
  'dynamic',    // 动态添加
  'enterprise', // 企业管理
  'claudeai',   // claude.ai 连接器
  'managed'     // 托管服务
]);

// 传输类型
export const TransportSchema = z.enum([
  'stdio',    // 子进程 stdin/stdout
  'sse',      // HTTP Server-Sent Events
  'sse-ide',  // IDE 专用 SSE
  'http',     // HTTP/REST
  'ws',       // WebSocket
  'sdk'       // 进程内 SDK
]);
```

### 2.2 各传输类型配置

```typescript
// stdio: 子进程模式
export const McpStdioServerConfigSchema = z.object({
  type: z.literal('stdio').optional(), // 可选，向后兼容
  command: z.string().min(1),
  args: z.array(z.string()).default([]),
  env: z.record(z.string(), z.string()).optional()
});

// SSE: 服务端事件推送
export const McpSSEServerConfigSchema = z.object({
  type: z.literal('sse'),
  url: z.string(),
  headers: z.record(z.string(), z.string()).optional(),
  headersHelper: z.string().optional(),
  oauth: McpOAuthConfigSchema().optional()
});

// HTTP: REST 风格
export const McpHTTPServerConfigSchema = z.object({
  type: z.literal('http'),
  url: z.string(),
  headers: z.record(z.string(), z.string()).optional(),
  headersHelper: z.string().optional(),
  oauth: McpOAuthConfigSchema().optional()
});

// WebSocket: 双向通信
export const McpWebSocketServerConfigSchema = z.object({
  type: z.literal('ws'),
  url: z.string(),
  headers: z.record(z.string(), z.string()).optional(),
  headersHelper: z.string().optional()
});

// SDK: 进程内直接调用
export const McpSdkServerConfigSchema = z.object({
  type: z.literal('sdk'),
  name: z.string()
});

// claude.ai 代理
export const McpClaudeAIProxyServerConfigSchema = z.object({
  type: z.literal('claudeai-proxy'),
  url: z.string(),
  id: z.string()
});
```

### 2.3 IDE 专用传输

```typescript
// IDE SSE 模式
export const McpSSEIDEServerConfigSchema = z.object({
  type: z.literal('sse-ide'),
  url: z.string(),
  ideName: z.string(),
  ideRunningInWindows: z.boolean().optional()
});

// IDE WebSocket 模式
export const McpWebSocketIDEServerConfigSchema = z.object({
  type: z.literal('ws-ide'),
  url: z.string(),
  ideName: z.string(),
  authToken: z.string().optional(),
  ideRunningInWindows: z.boolean().optional()
});
```

---

## 3. 服务器连接状态机

### 3.1 连接状态类型

```typescript
// 已连接
export type ConnectedMCPServer = {
  client: Client;
  name: string;
  type: 'connected';
  capabilities: ServerCapabilities;
  serverInfo?: { name: string; version: string };
  instructions?: string;
  config: ScopedMcpServerConfig;
  cleanup: () => Promise<void>;
};

// 连接失败
export type FailedMCPServer = {
  name: string;
  type: 'failed';
  config: ScopedMcpServerConfig;
  error?: string;
};

// 需要认证
export type NeedsAuthMCPServer = {
  name: string;
  type: 'needs-auth';
  config: ScopedMcpServerConfig;
};

// 连接中
export type PendingMCPServer = {
  name: string;
  type: 'pending';
  config: ScopedMcpServerConfig;
  reconnectAttempt?: number;
  maxReconnectAttempts?: number;
};

// 已禁用
export type DisabledMCPServer = {
  name: string;
  type: 'disabled';
  config: ScopedMcpServerConfig;
};

// 联合类型
export type MCPServerConnection =
  | ConnectedMCPServer
  | FailedMCPServer
  | NeedsAuthMCPServer
  | PendingMCPServer
  | DisabledMCPServer;
```

### 3.2 状态转换图

```
                    ┌─────────────┐
                    │   pending   │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │  connected │   │   failed   │   │ needs-auth │
   └─────┬──────┘   └──────┬─────┘   └──────┬─────┘
         │                 │                │
         │                 │                │
         ▼                 ▼                │
   ┌────────────┐   ┌────────────┐          │
   │  (正常工作) │   │ (重试/放弃) │◀─────────┘
   └────────────┘   └────────────┘
         │
         ▼
   ┌────────────┐
   │  disabled  │  (用户禁用)
   └────────────┘
```

---

## 4. 客户端实现

### 4.1 连接管理器

```typescript
// MCPConnectionManager.tsx - React Context 管理
interface MCPConnectionContextValue {
  reconnectMcpServer: (serverName: string) => Promise<{
    client: MCPServerConnection;
    tools: Tool[];
    commands: Command[];
    resources?: ServerResource[];
  }>;
  toggleMcpServer: (serverName: string) => Promise<void>;
}

// Hook: 管理 MCP 连接
export function useManageMCPConnections(
  dynamicMcpConfig: Record<string, ScopedMcpServerConfig> | undefined,
  isStrictMcpConfig: boolean
) {
  // 连接逻辑实现
  return { reconnectMcpServer, toggleMcpServer };
}
```

### 4.2 MCP SDK 客户端初始化

```typescript
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { SSEClientTransport } from '@modelcontextprotocol/sdk/client/sse.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

// 根据配置类型创建传输层
async function createTransport(
  config: ScopedMcpServerConfig
): Promise<Transport> {
  switch (config.type) {
    case 'stdio':
      return new StdioClientTransport({
        command: config.command,
        args: config.args,
        env: { ...subprocessEnv(), ...config.env }
      });
      
    case 'sse':
      return new SSEClientTransport(config.url, {
        headers: await getMcpServerHeaders(config),
        fetch: createFetchWithInit(getProxyFetchOptions())
      });
      
    case 'http':
      return new StreamableHTTPClientTransport(config.url, {
        headers: await getMcpServerHeaders(config),
        fetch: createFetchWithInit(getProxyFetchOptions())
      });
      
    case 'ws':
      return new WebSocketTransport(config.url, {
        headers: await getMcpServerHeaders(config),
        agent: getWebSocketProxyAgent(),
        tlsOptions: getWebSocketTLSOptions()
      });
      
    case 'sdk':
      return new SdkControlClientTransport(config.name);
      
    default:
      throw new Error(`Unknown transport type: ${config.type}`);
  }
}

// 创建并连接客户端
async function connectMcpServer(
  name: string,
  config: ScopedMcpServerConfig
): Promise<MCPServerConnection> {
  try {
    const transport = await createTransport(config);
    const client = new Client(
      { name: 'claude-code', version: '1.0.0' },
      { capabilities: { tools: {}, resources: {} } }
    );
    
    await client.connect(transport);
    
    // 获取服务器能力
    const capabilities = client.getServerCapabilities();
    const serverInfo = client.getServerVersion();
    
    // 注册清理函数
    registerCleanup(async () => {
      await client.close();
    });
    
    return {
      client,
      name,
      type: 'connected',
      capabilities,
      serverInfo,
      config,
      cleanup: async () => await client.close()
    };
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return { name, type: 'needs-auth', config };
    }
    return { name, type: 'failed', config, error: errorMessage(error) };
  }
}
```

### 4.3 会话过期处理

```typescript
/**
 * 检测 MCP 会话过期错误
 * MCP 规范：服务器返回 404 + JSON-RPC 代码 -32001 表示会话失效
 */
export function isMcpSessionExpiredError(error: Error): boolean {
  const httpStatus = 'code' in error 
    ? (error as Error & { code?: number }).code 
    : undefined;
    
  if (httpStatus !== 404) {
    return false;
  }
  
  // 检查 JSON-RPC 错误码
  return (
    error.message.includes('"code":-32001') ||
    error.message.includes('"code": -32001')
  );
}

class McpSessionExpiredError extends Error {
  constructor(serverName: string) {
    super(`MCP server "${serverName}" session expired`);
    this.name = 'McpSessionExpiredError';
  }
}
```

---

## 5. 工具名称规范化

### 5.1 命名约定

MCP 工具在 Claude Code 中采用特殊的命名前缀：

```typescript
// 工具名称格式: mcp__<server>__<tool>
const MCP_TOOL_PREFIX = 'mcp__';

export function buildMcpToolName(serverName: string, toolName: string): string {
  const normalizedServer = normalizeNameForMCP(serverName);
  const normalizedTool = normalizeNameForMCP(toolName);
  return `${MCP_TOOL_PREFIX}${normalizedServer}__${normalizedTool}`;
}

// 示例:
// 服务器: "slack", 工具: "send_message"
// → mcp__slack__send_message

// 服务器: "claude.ai Slack", 工具: "post_message"
// → mcp__claude_ai_Slack__post_message
```

### 5.2 名称规范化函数

```typescript
// Claude.ai 服务器名称前缀
const CLAUDEAI_SERVER_PREFIX = 'claude.ai ';

/**
 * 规范化名称以符合 API 模式 ^[a-zA-Z0-9_-]{1,64}$
 * 将无效字符（包括点和空格）替换为下划线
 */
export function normalizeNameForMCP(name: string): string {
  let normalized = name.replace(/[^a-zA-Z0-9_-]/g, '_');
  
  // claude.ai 服务器需要额外处理
  if (name.startsWith(CLAUDEAI_SERVER_PREFIX)) {
    // 合并连续下划线，去除首尾下划线
    // 防止与 MCP 工具名中的 __ 分隔符冲突
    normalized = normalized
      .replace(/_+/g, '_')
      .replace(/^_|_$/g, '');
  }
  
  return normalized;
}
```

---

## 6. 动态工具生成

### 6.1 MCPTool 包装器

```typescript
export const MCPTool = buildTool({
  isMcp: true,
  name: 'mcp', // 被 mcpClient.ts 覆盖
  maxResultSizeChars: 100_000,
  
  // 开放世界模式 - 被覆盖
  isOpenWorld() {
    return false;
  },
  
  // Schema 允许任意输入对象
  get inputSchema() {
    return z.object({}).passthrough();
  },
  
  get outputSchema() {
    return z.string().describe('MCP tool execution result');
  },
  
  // 权限检查 - 透传模式
  async checkPermissions(): Promise<PermissionResult> {
    return {
      behavior: 'passthrough',
      message: 'MCPTool requires permission.'
    };
  },
  
  // 实际调用逻辑被覆盖
  async call() {
    return { data: '' };
  },
  
  // 结果映射
  mapToolResultToToolResultBlockParam(content, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content
    };
  }
});
```

### 6.2 从 MCP 服务器获取工具

```typescript
async function getToolsFromMcpServer(
  connection: ConnectedMCPServer
): Promise<Tool[]> {
  const { client, name: serverName } = connection;
  
  // 调用 MCP SDK 获取工具列表
  const toolsResult = await client.request(
    { method: 'tools/list' },
    ListToolsResultSchema
  );
  
  const tools: Tool[] = [];
  
  for (const mcpTool of toolsResult.tools) {
    // 构建规范化的工具名
    const toolName = buildMcpToolName(serverName, mcpTool.name);
    
    // 创建工具包装器
    const wrappedTool = {
      ...MCPTool,
      name: toolName,
      originalToolName: mcpTool.name,
      serverName,
      
      // 使用 MCP 工具的描述
      async description() {
        const desc = mcpTool.description || '';
        // 截断过长描述
        return desc.length > MAX_MCP_DESCRIPTION_LENGTH
          ? desc.slice(0, MAX_MCP_DESCRIPTION_LENGTH) + '...'
          : desc;
      },
      
      // 使用 MCP 工具的输入 Schema
      get inputSchema() {
        return mcpTool.inputSchema || z.object({}).passthrough();
      },
      
      // 实际执行 MCP 调用
      async call(input: unknown, context: ToolUseContext) {
        return callMcpTool(serverName, mcpTool.name, input, context);
      },
      
      // UI 显示名称
      userFacingName: () => `${serverName}: ${mcpTool.name}`
    };
    
    tools.push(wrappedTool);
  }
  
  return tools;
}
```

### 6.3 MCP 工具调用实现

```typescript
async function callMcpTool(
  serverName: string,
  toolName: string,
  input: unknown,
  context: ToolUseContext
): Promise<{ data: string }> {
  const connection = await ensureConnectedClient(serverName);
  
  if (connection.type !== 'connected') {
    throw new Error(`MCP server "${serverName}" is not connected`);
  }
  
  const { client } = connection;
  const timeoutMs = getMcpToolTimeoutMs();
  
  try {
    const result = await client.request(
      {
        method: 'tools/call',
        params: {
          name: toolName,
          arguments: input
        }
      },
      CallToolResultSchema,
      { timeout: timeoutMs }
    );
    
    // 处理错误结果
    if (result.isError) {
      throw new McpToolCallError_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS(
        result.content?.[0]?.text || 'MCP tool call failed',
        'mcp_tool_error',
        { _meta: result._meta }
      );
    }
    
    // 处理结果内容
    const content = processMcpToolResult(result.content);
    
    // 检查是否需要截断
    if (mcpContentNeedsTruncation(content)) {
      return { data: truncateMcpContentIfNeeded(content) };
    }
    
    return { data: content };
    
  } catch (error) {
    // 处理会话过期
    if (isMcpSessionExpiredError(error)) {
      // 清除连接缓存，重试
      clearConnectionCache(serverName);
      throw new McpSessionExpiredError(serverName);
    }
    
    // 处理认证错误
    if (error instanceof UnauthorizedError) {
      setMcpAuthCacheEntry(serverName);
      throw new McpAuthError(serverName, 'Authentication required');
    }
    
    throw error;
  }
}
```

---

## 7. 配置管理

### 7.1 配置文件位置

```typescript
// 配置优先级（从高到低）：
// 1. 企业管理配置
//    ~/.claude/managed/managed-mcp.json
//
// 2. 用户全局配置
//    ~/.claude/settings.json → mcpServers
//
// 3. 项目配置
//    ./.mcp.json
//
// 4. 动态配置（命令行 --mcp-config）
//
// 5. claude.ai 连接器
//    通过 OAuth 从 claude.ai 获取
```

### 7.2 配置合并逻辑

```typescript
export async function getAllMcpConfigs(): Promise<{
  servers: Record<string, ScopedMcpServerConfig>;
  pluginErrors?: PluginError[];
}> {
  // 1. 加载企业配置
  const enterpriseServers = await loadEnterpriseMcpConfig();
  
  // 2. 加载用户配置
  const userServers = addScopeToServers(
    getGlobalConfig().mcpServers,
    'user'
  );
  
  // 3. 加载项目配置
  const projectServers = addScopeToServers(
    await loadProjectMcpConfig(),
    'project'
  );
  
  // 4. 加载插件提供的服务器
  const { servers: pluginServers, errors: pluginErrors } = 
    await loadPluginMcpServers();
  
  // 5. 加载 claude.ai 连接器
  const claudeAiServers = await fetchClaudeAIMcpConfigsIfEligible();
  
  // 合并顺序：企业 > 用户 > 项目 > 插件 > claude.ai
  const manualServers = {
    ...enterpriseServers,
    ...userServers,
    ...projectServers
  };
  
  // 插件去重：与手动配置冲突时，手动配置优先
  const { servers: dedupedPluginServers } = dedupPluginMcpServers(
    pluginServers,
    manualServers
  );
  
  // claude.ai 去重：与手动配置冲突时，手动配置优先
  const { servers: dedupedClaudeAiServers } = dedupClaudeAiMcpServers(
    claudeAiServers,
    manualServers
  );
  
  return {
    servers: {
      ...manualServers,
      ...dedupedPluginServers,
      ...dedupedClaudeAiServers
    },
    pluginErrors
  };
}
```

### 7.3 去重签名机制

```typescript
/**
 * 计算 MCP 服务器配置的去重签名
 * 两个配置签名相同即视为"同一服务器"
 */
export function getMcpServerSignature(config: McpServerConfig): string | null {
  // stdio: 基于命令数组
  const cmd = getServerCommandArray(config);
  if (cmd) {
    return `stdio:${jsonStringify(cmd)}`;
  }
  
  // 远程: 基于 URL（解包 CCR 代理 URL）
  const url = getServerUrl(config);
  if (url) {
    return `url:${unwrapCcrProxyUrl(url)}`;
  }
  
  // sdk: 无法计算签名
  return null;
}

/**
 * 解包 CCR 代理 URL 以获取原始供应商 URL
 */
export function unwrapCcrProxyUrl(url: string): string {
  const CCR_PROXY_PATH_MARKERS = [
    '/v2/session_ingress/shttp/mcp/',
    '/v2/ccr-sessions/'
  ];
  
  if (!CCR_PROXY_PATH_MARKERS.some(m => url.includes(m))) {
    return url;
  }
  
  try {
    const parsed = new URL(url);
    const original = parsed.searchParams.get('mcp_url');
    return original || url;
  } catch {
    return url;
  }
}
```

---

## 8. 认证流程

### 8.1 OAuth 支持

```typescript
const McpOAuthConfigSchema = z.object({
  clientId: z.string().optional(),
  callbackPort: z.number().int().positive().optional(),
  authServerMetadataUrl: z.string().url()
    .startsWith('https://', {
      message: 'authServerMetadataUrl must use https://'
    }).optional(),
  // 跨应用访问 (XAA)
  xaa: z.boolean().optional()
});
```

### 8.2 认证缓存

```typescript
const MCP_AUTH_CACHE_TTL_MS = 15 * 60 * 1000; // 15分钟

type McpAuthCacheData = Record<string, { timestamp: number }>;

function getMcpAuthCachePath(): string {
  return join(getClaudeConfigHomeDir(), 'mcp-needs-auth-cache.json');
}

// 检查认证是否已缓存（避免重复认证请求）
async function isMcpAuthCached(serverId: string): Promise<boolean> {
  const cache = await getMcpAuthCache();
  const entry = cache[serverId];
  if (!entry) return false;
  return Date.now() - entry.timestamp < MCP_AUTH_CACHE_TTL_MS;
}

// 串行化缓存写入（防止并发读-改-写竞争）
let writeChain = Promise.resolve();

function setMcpAuthCacheEntry(serverId: string): void {
  writeChain = writeChain.then(async () => {
    const cache = await getMcpAuthCache();
    cache[serverId] = { timestamp: Date.now() };
    
    const cachePath = getMcpAuthCachePath();
    await mkdir(dirname(cachePath), { recursive: true });
    await writeFile(cachePath, jsonStringify(cache));
    
    // 使读取缓存失效
    authCachePromise = null;
  }).catch(() => {
    // 最佳努力缓存写入
  });
}
```

### 8.3 claude.ai 代理认证

```typescript
/**
 * claude.ai 代理连接的 Fetch 包装器
 * 附加 OAuth Bearer Token 并在 401 时重试
 */
export function createClaudeAiProxyFetch(innerFetch: FetchLike): FetchLike {
  return async (url, init) => {
    const doRequest = async () => {
      // 确保 Token 有效
      await checkAndRefreshOAuthTokenIfNeeded();
      
      const currentTokens = getClaudeAIOAuthTokens();
      if (!currentTokens) {
        throw new Error('No claude.ai OAuth token available');
      }
      
      const headers = new Headers(init?.headers);
      headers.set('Authorization', `Bearer ${currentTokens.accessToken}`);
      
      const response = await innerFetch(url, { ...init, headers });
      return { response, sentToken: currentTokens.accessToken };
    };
    
    const { response, sentToken } = await doRequest();
    
    if (response.status !== 401) {
      return response;
    }
    
    // 401 时尝试刷新 Token 并重试
    const refreshed = await handleOAuth401Error(sentToken);
    if (refreshed) {
      const { response: retryResponse } = await doRequest();
      return retryResponse;
    }
    
    return response;
  };
}
```

---

## 9. 资源管理

### 9.1 资源类型

```typescript
export type ServerResource = Resource & {
  server: string;  // 所属服务器名称
};

// Resource 来自 MCP SDK
type Resource = {
  uri: string;
  name: string;
  description?: string;
  mimeType?: string;
};
```

### 9.2 资源读取工具

```typescript
// ListMcpResourcesTool - 列出 MCP 资源
export const ListMcpResourcesTool = buildTool({
  name: 'mcp_resource_list',
  
  async call({ server }, context) {
    const connection = await ensureConnectedClient(server);
    
    if (connection.type !== 'connected') {
      throw new Error(`Server ${server} is not connected`);
    }
    
    const result = await connection.client.request(
      { method: 'resources/list' },
      ListResourcesResultSchema
    );
    
    return {
      data: result.resources.map(r => ({
        ...r,
        server
      }))
    };
  }
});

// ReadMcpResourceTool - 读取 MCP 资源
export const ReadMcpResourceTool = buildTool({
  name: 'mcp_resource_read',
  
  async call({ server, uri }, context) {
    const connection = await ensureConnectedClient(server);
    
    if (connection.type !== 'connected') {
      throw new Error(`Server ${server} is not connected`);
    }
    
    const result = await connection.client.request(
      { method: 'resources/read', params: { uri } },
      ReadResourceResultSchema
    );
    
    return { data: result.contents };
  }
});
```

---

## 10. 安全性考虑

### 10.1 服务器允许/拒绝列表

```typescript
/**
 * 检查 MCP 服务器是否被企业策略拒绝
 * 支持名称、命令和 URL 模式匹配
 */
function isMcpServerDenied(
  serverName: string,
  config?: McpServerConfig
): boolean {
  const settings = getMcpDenylistSettings();
  
  if (!settings.deniedMcpServers) {
    return false;
  }
  
  // 1. 检查名称匹配
  for (const entry of settings.deniedMcpServers) {
    if (isMcpServerNameEntry(entry) && entry.serverName === serverName) {
      return true;
    }
  }
  
  if (!config) return false;
  
  // 2. 检查命令匹配 (stdio)
  const serverCommand = getServerCommandArray(config);
  if (serverCommand) {
    for (const entry of settings.deniedMcpServers) {
      if (isMcpServerCommandEntry(entry) &&
          commandArraysMatch(entry.serverCommand, serverCommand)) {
        return true;
      }
    }
  }
  
  // 3. 检查 URL 模式匹配 (远程)
  const serverUrl = getServerUrl(config);
  if (serverUrl) {
    for (const entry of settings.deniedMcpServers) {
      if (isMcpServerUrlEntry(entry) &&
          urlMatchesPattern(serverUrl, entry.serverUrl)) {
        return true;
      }
    }
  }
  
  return false;
}

/**
 * URL 模式转正则表达式
 * 支持 * 作为通配符
 */
function urlPatternToRegex(pattern: string): RegExp {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&');
  const regexStr = escaped.replace(/\*/g, '.*');
  return new RegExp(`^${regexStr}$`);
}
```

### 10.2 描述长度限制

```typescript
/**
 * MCP 工具描述和服务器指令的长度上限
 * 防止 OpenAPI 生成的服务器发送过大文档
 */
const MAX_MCP_DESCRIPTION_LENGTH = 2048;

function truncateDescription(description: string): string {
  if (description.length <= MAX_MCP_DESCRIPTION_LENGTH) {
    return description;
  }
  return description.slice(0, MAX_MCP_DESCRIPTION_LENGTH) + '...';
}
```

### 10.3 超时控制

```typescript
/**
 * MCP 工具调用默认超时（约27.8小时）
 * 可通过 MCP_TOOL_TIMEOUT 环境变量覆盖
 */
const DEFAULT_MCP_TOOL_TIMEOUT_MS = 100_000_000;

function getMcpToolTimeoutMs(): number {
  return parseInt(process.env.MCP_TOOL_TIMEOUT || '', 10) ||
         DEFAULT_MCP_TOOL_TIMEOUT_MS;
}
```

---

## 11. 错误处理

### 11.1 自定义错误类型

```typescript
// 认证错误
export class McpAuthError extends Error {
  serverName: string;
  
  constructor(serverName: string, message: string) {
    super(message);
    this.name = 'McpAuthError';
    this.serverName = serverName;
  }
}

// 会话过期错误
class McpSessionExpiredError extends Error {
  constructor(serverName: string) {
    super(`MCP server "${serverName}" session expired`);
    this.name = 'McpSessionExpiredError';
  }
}

// 工具调用错误（携带 _meta）
export class McpToolCallError_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS 
  extends TelemetrySafeError_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS {
  
  constructor(
    message: string,
    telemetryMessage: string,
    readonly mcpMeta?: { _meta?: Record<string, unknown> }
  ) {
    super(message, telemetryMessage);
    this.name = 'McpToolCallError';
  }
}
```

---

## 🎓 本章小结

### 核心要点

1. **MCP 是能力扩展协议**：让 AI 动态获取新工具，无需修改核心代码
2. **多种传输层**：stdio（子进程）、SSE（HTTP）、WebSocket、进程内 SDK
3. **自动工具生成**：MCP 服务器定义 → 自动生成 MCPTool 包装器
4. **OAuth 认证流程**：支持远程 MCP 服务器的安全认证

### 可迁移的设计原则

| 原则 | 说明 | 适用场景 |
|------|------|---------|
| 插件化架构 | 核心功能 + 可扩展插件 | 任何需要扩展的系统 |
| 协议标准化 | 统一接口，不同实现 | 多供应商集成 |
| 动态发现 | 运行时发现新能力 | 插件系统 |
| 传输层抽象 | 同一协议，多种传输 | 跨环境部署 |

### 🧠 为什么这样设计？

**问题：为什么 MCP 要支持多种传输层（stdio/sse/ws）？**
- **stdio**：本地工具，最简单（用子进程）
- **SSE**：远程服务器，单向推送
- **WebSocket**：双向通信，实时交互
- 不同场景用不同传输，灵活适配

**问题：为什么 MCP 工具要包装成 MCPTool？**
- Claude Code 的工具系统有统一接口（Tool 抽象类）
- MCPTool 把外部 MCP 工具"翻译"成内部格式
- AI 不知道工具是内置的还是 MCP 的

**问题：为什么 MCP 描述有 2048 字符限制？**
- 有些 MCP 服务器从 OpenAPI 生成，描述可能非常长
- 过长描述浪费 Token，影响性能
- 2048 足够表达功能，又不会太占空间

### 大白话说 MCP 连接流程

```
想象你要用一个新的 App：

第 1 步：发现服务器（配置）
  "我要用 PostgreSQL MCP 服务器"
  配置写在 .mcp.json 里
  
第 2 步：建立连接（Transport）
  "用 stdio 方式启动子进程"
  或 "用 SSE 连接远程服务器"
  
第 3 步：获取能力（Initialize）
  "你有什么工具？"
  服务器返回：query_db, insert_data, ...
  
第 4 步：生成包装器（MCPTool）
  每个工具生成一个 MCPTool 类
  AI 可以像用内置工具一样使用
  
第 5 步：调用工具
  AI："mcp__postgres__query_db(sql='SELECT ...')"
  Claude Code：转发给 MCP 服务器
  MCP 服务器：执行查询，返回结果
```

### 🤔 思考题

1. **如果 MCP 服务器崩溃**，Claude Code 会怎么处理？
   - 提示：考虑连接状态和错误处理

2. **为什么 MCP 工具名用 `mcp__serverName__toolName` 格式？**
   - 提示：考虑命名冲突

3. **如果用户同时配置了 10 个 MCP 服务器**，会有什么问题？
   - 提示：考虑启动时间和资源占用

4. **OAuth 认证的 Token 存在哪里？** 为什么要存？
   - 提示：考虑跨会话复用

5. **设计题**：如果你要实现"MCP 服务器热更新"（不重启 Claude Code 就能添加新服务器），需要修改哪些组件？

---

## 12. 下一步

本篇覆盖了 MCP 协议在 Claude Code 中的深度集成，包括传输层、连接管理、工具生成和安全机制。

接下来，我们将探索权限系统的完整设计，了解 Claude Code 如何实现细粒度的访问控制：

👉 **继续阅读**：[09-权限系统完整设计.md](/claudecode/09-agent-system)
