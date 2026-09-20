// API Types — matches qa-agent FastAPI schema definitions

// `creating` 为前端本地 UI 态（harden-create-session-entry）：新建会话提交后、
// 后端 create 响应到达前的占位记录状态，后端不会返回该值。
export type SessionStatus = 'idle' | 'running' | 'interrupt_pending' | 'completed' | 'failed' | 'inactive' | 'aborted' | 'creating'
export type AgentMode = 'normal' | 'coordinator'

export interface ModelSlotOverride {
  model?: string
  base_url?: string
  api_key?: string
}

export interface CreateSessionRequest {
  agent_name?: string
  mode?: AgentMode
  worker_agent_names?: string[]
  permission_mode?: string
  game_version?: string
  module?: string
  model_slots?: Record<string, string | ModelSlotOverride>
}

export interface CreateSessionResponse {
  session_id: string
  status: SessionStatus
  agent_name: string
  mode: string
}

export interface SendMessageRequest {
  content: string
  stream?: boolean
  workspace_context?: WorkspaceContext
  activate_skills?: string[]
  model_override?: string
  /** User-uploaded file references (from POST /sessions/{sid}/uploads). When non-empty,
   *  backend injects a [用户上传文件] block into user_content with abs_path list;
   *  agent reads via file_read. Empty/undefined = no upload context. */
  uploaded_files?: UploadedFileRef[]
}

export interface SendMessageResponse {
  message_id: string
  session_id: string
  status: string
}

export interface ReviewRequest {
  action: string
  modified_content?: string
  notes?: string
  confirmed_by?: string
}

export interface ReviewResponse {
  session_id: string
  action: string
  resolved: boolean
}

export interface InterruptPayload {
  interrupt_id: string
  interrupt_type: 'preview_confirm' | 'result_verify' | 'plan_confirm' | 'review_requested'
  payload: Record<string, unknown>
  created_at: number
  age_seconds: number
  actions: string[]
}

export interface SessionStatusResponse {
  session_id: string
  status: SessionStatus
  agent_name: string
  mode: string
  game_version: string
  module: string
  interrupt_payload?: InterruptPayload
  error?: string
}

/** Response for GET /sessions/{id}/stream/last-seq — backend event progress probe. */
export interface LastSeqResponse {
  session_id: string
  last_seq_id: number
  status: SessionStatus
  /** history_version 探针字段（add-session-history-pagination D14）；null/缺失 = 后端存储不可用 */
  total_runs?: number | null
  total_messages?: number | null
}

/**
 * Response for GET /sessions/{id}/context-usage — 上下文窗口占用分类 + 会话累计消耗
 * （add-context-usage-visibility）。snake_case 对齐后端 ContextUsageResponse。
 * inactive 会话降级：agent 侧字段（system_prompt / tools / mcp_tools / segments）为 null。
 */
export interface ContextUsageResponse {
  session_id: string
  max_context_tokens: number
  categories: {
    system_prompt: number | null
    tools: number | null
    mcp_tools: number | null
    messages: number | null
    free_space: number | null
  }
  segments: Record<string, number> | null
  usage_totals: UsageMetricsPayload
  per_agent: Record<string, UsageMetricsPayload> | null
  recent_runs: RunUsageEntry[]
  updated_at: number
}

/** 会话累计消耗（对齐后端 UsageMetrics；缺失维度为 null，显示 "—"）。 */
export interface UsageMetricsPayload {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cache_read_tokens?: number | null
  cache_write_tokens?: number | null
  reasoning_tokens?: number | null
  cost?: number | null
  duration_s?: number | null
}

/** 单轮消耗行（明细表每轮一行）。 */
export interface RunUsageEntry {
  agent_name: string
  usage: UsageMetricsPayload
  created_at?: number | null
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  milvus: 'connected' | 'disconnected' | 'disabled'
  storage: 'ok' | 'error'
  version: string
}

export interface ConfigResponse {
  llm_model: string
  llm_base_url: string
  storage_backend: string
  milvus_enabled: boolean
  milvus_host: string
  default_max_turns: number
  default_permission_mode: string
  project_skills_dir: string
  mcp_config_path: string
}

export interface AgentInfo {
  name: string
  description: string
  when_to_use: string
  tools: string[]
  permission_mode: string
  agent_type: string
  tags: string[]
  inject_history: boolean
}

export interface SkillInfo {
  name: string
  tool_name: string
  description: string
  when_to_use: string
  tools: string[]
  args: string[]
  effort: number
  agent?: string
  load_mode: string      // "default" | "on-demand"
  summary: string        // Skill 摘要（可能为空字符串）
  source?: 'builtin' | 'user'  // 前端合并两源时填充，后端不返回
}

export interface SlotConfig {
  model: string
  base_url?: string
}

export interface ModelSlotsResponse {
  slots: Record<string, SlotConfig>
  agent_slots: Record<string, { slot: string }>
}

// ---------------------------------------------------------------------------
// Auth types
// ---------------------------------------------------------------------------

export interface UserInfo {
  email: string
  name: string
  role: string
}

export interface GetLoginUserResponse {
  code: number       // 0 = authed, 300 = redirect needed
  msg: string
  data: {
    user_info?: UserInfo
    redirect_url?: string
  }
}

// ---------------------------------------------------------------------------
// User session history types
// ---------------------------------------------------------------------------

export interface SessionMeta {
  session_id: string
  agent_name: string
  mode: string
  game_version: string
  module: string
  title: string
  created_at: number
  last_active_at: number
  status?: SessionStatus
  hidden?: boolean
}

export interface UserSessionsResponse {
  sessions: SessionMeta[]
}

export interface UpdateSessionRequest {
  title: string
}

export interface UpdateSessionVisibilityRequest {
  hidden: boolean
}

// ---------------------------------------------------------------------------
// History messages types (Agno message format)
// ---------------------------------------------------------------------------

export interface ToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string   // JSON string
  }
}

export interface HistoryMessage {
  id?: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string | null
  tool_calls?: ToolCall[]
  tool_call_id?: string | null  // present when role='tool', links to tool_calls entry
  name?: string | null          // agent name (assistant) or tool name (tool)
  created_at?: number
  /** LLM 思考内容（后端 reasoning_content + redacted_reasoning_content 合并）；null/缺失 = 无思考 */
  reasoning_content?: string | null
}

/**
 * 分页页元数据（add-session-history-pagination D1/D3/D14）。
 * 附着于 GET /messages 分页响应与 GET /messages/tail 尾页响应。
 */
export interface PageMeta {
  has_more: boolean
  next_before: number
  total_runs: number
  total_messages: number
  anchor?: string | null
}

export interface SessionMessagesResponse {
  session_id: string
  messages: HistoryMessage[]
  /** 仅分页/尾页请求返回；无参全量请求为 null */
  page_meta?: PageMeta | null
  /** 锚校验失败信号（D3 ABA 防护）：丢弃旧页重拉尾页 */
  resync?: boolean
}

export interface TruncateRequest {
  message_id: string
  /** 幂等键（add-session-history-pagination D13）：同一 id 重放返回缓存结果 */
  request_id?: string
}

export interface TruncateResponse {
  truncated_count: number
  remaining_messages: HistoryMessage[]
  /** 请求回显（D13）；缺失 = 旧后端 */
  request_id?: string | null
  /** 截断后版本元组（D14）；缺失 = 旧后端 */
  history_version?: HistoryVersion | null
}

/**
 * POST /sessions/{id}/turns/regenerate — 轮次重答（add-turn-regenerate D4/D5）。
 * 锚二选一：message_id（该轮用户消息位号 id）/ turn_last（服务器侧解析最后
 * 一个 user 消息）。全链路按位号/轮锚，MUST NOT 内容匹配。
 */
export interface RegenerateTurnRequest {
  message_id?: string
  turn_last?: boolean
  /** 幂等键（对齐 truncate D13 槽位约定） */
  request_id?: string
}

export interface RegenerateTurnResponse {
  request_id?: string | null
  truncated_count: number
  remaining_count: number
  history_version?: HistoryVersion | null
}

/**
 * 存储视角历史版本元组（D14）：truncate 必使 total_messages 减少、run 完成必使两者增加，
 * 拉取时比对即可检测失步。对齐后端 api/schemas.HistoryVersion。
 */
export interface HistoryVersion {
  total_runs: number
  total_messages: number
}

// ---------------------------------------------------------------------------
// Workspace types
// ---------------------------------------------------------------------------

export interface TreeEntry {
  name: string
  type: 'file' | 'dir'
  path: string
  size?: number
  has_children?: boolean
}

export interface WorkspaceInfo {
  root_path: string
  tree: TreeEntry[]
}

export interface WorkspaceContext {
  selected_paths: string[]
}

/** Reference to a file uploaded via POST /sessions/{sid}/uploads (multipart).
 *  Carried in SendMessageRequest.uploaded_files so the backend can inject a
 *  [用户上传文件] block into the agent prompt (path injection pattern,
 *  mirroring workspace_context). Field names snake_case to match backend. */
export interface UploadedFileRef {
  file_id: string
  name: string
  size: number
  type: string
  abs_path: string
}

// ---------------------------------------------------------------------------
// Abort / Resume types
// ---------------------------------------------------------------------------

export interface AbortResponse {
  status: 'aborting'
}

export interface ResumeRequest {
  type: string            // 'user_resume' | 'review_approved'
  message?: string
  approved?: boolean
}

export interface ResumeResponse {
  status: string
}

// ---------------------------------------------------------------------------
// MCP types
// ---------------------------------------------------------------------------

export interface MCPToolInfo {
  name: string
  description: string
  permission_level: 'L1' | 'L2'
}

export interface MCPServerInfo {
  name: string
  transport: 'stdio' | 'sse'
  enabled: boolean
  status: 'connected' | 'disconnected' | 'error'
  tool_count: number
  tools: MCPToolInfo[]
  error: string | null
  connected_at: number | null
  config: Record<string, unknown> | null
}

export interface AddMCPServerRequest {
  name: string
  transport?: 'stdio' | 'sse'
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  enabled?: boolean
}

export interface UpdateMCPServerRequest {
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
}

export interface MCPConfigResponse {
  servers: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// Available Models types
// ---------------------------------------------------------------------------

/** Info about one available model for UI selection. ``supports_vision`` is
 *  computed by the backend from settings.llm_vision_models_set and used by
 *  the FileUploader to gate image file upload. */
export interface ModelInfo {
  name: string
  supports_vision: boolean
}

export interface AvailableModelsResponse {
  models: ModelInfo[]
  default: string
}
