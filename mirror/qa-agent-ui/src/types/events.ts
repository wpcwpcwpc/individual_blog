// SSE Event Types — normalized events from qa-agent backend stream

export type EventType =
  | 'token'
  | 'reasoning_delta'
  | 'tool_start'
  | 'tool_end'
  | 'tool_error'
  | 'run_started'
  | 'run_complete'
  | 'run_error'
  | 'run_aborted'
  | 'run_aborting'
  | 'interrupt_request'
  | 'review_requested'
  | 'interrupt_timeout'
  | 'session_idle'
  | 'heartbeat'
  | 'approval_pending'
  | 'clarification_request' // ClarifyCard（澄清卡，RunPaused 特化：ask_user / get_user_input）
  | 'compression'
  | 'messages_truncated'

// Base event
export interface BaseEvent {
  event_type: EventType
  session_id: string
  seq_id?: number
  backend_message_id?: string
}

export interface TokenEvent extends BaseEvent {
  event_type: 'token'
  content: string
  agent_name?: string
  model?: string
}

/** 思考增量事件（add-thinking-stream-display）：模型原生 thinking 的逐 delta 透传 */
export interface ReasoningDeltaEvent extends BaseEvent {
  event_type: 'reasoning_delta'
  content: string
  agent_name?: string
  model?: string
}

export interface ToolStartEvent extends BaseEvent {
  event_type: 'tool_start'
  tool_name: string
  tool_call_id: string
  inputs: Record<string, unknown>
  agent_name?: string
}

export interface ToolEndEvent extends BaseEvent {
  event_type: 'tool_end'
  tool_name: string
  tool_call_id: string
  outputs: unknown
  elapsed_ms: number
  agent_name?: string
}

export interface ToolErrorEvent extends BaseEvent {
  event_type: 'tool_error'
  tool_name: string
  tool_call_id: string
  error: string
  agent_name?: string
}

export interface RunStartedEvent extends BaseEvent {
  event_type: 'run_started'
  agent_name: string
}

export interface RunCompleteEvent extends BaseEvent {
  event_type: 'run_complete'
  final_response: string
  agent_name?: string
  /**
   * 本轮 token 消耗（后端 _run_complete_event 从 Agno RunCompletedEvent.metrics 提取，
   * add-context-usage-visibility）。null = 网关未下发 usage —— UI 对缺失维度显示 "—"。
   */
  usage?: UsageMetrics | null
}

/**
 * 单轮 token 消耗指标（对齐后端 UsageMetrics schema）。
 * 缺失维度为 null，MUST NOT 显示为 0。
 */
export interface UsageMetrics {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cache_read_tokens?: number | null
  cache_write_tokens?: number | null
  reasoning_tokens?: number | null
  cost?: number | null
  duration_s?: number | null
}

/**
 * 写回 QAStudio 结果结构（对齐后端 session_state.commit_result）。
 */
export interface RunErrorEvent extends BaseEvent {
  event_type: 'run_error'
  error: string
  agent_name?: string
}

export interface RunAbortedEvent extends BaseEvent {
  event_type: 'run_aborted'
  agent_name?: string
}

/**
 * 中断信号已触发 / 收口未完成的瞬态事件（fix-abort-latency）。
 *
 * 后端 abort watcher 在 `task.cancel()` 同一 tick 推送，**不经 EventStore**：
 * 无 seq_id、不参与断线回放，仅活跃连接可见。`run_aborted` 仍是唯一终态。
 */
export interface RunAbortingEvent extends BaseEvent {
  event_type: 'run_aborting'
  agent_name?: string
}

/**
 * L2 上下文压缩事件（后端 stream_adapter._compression_event 归一化）。
 * add-context-usage-visibility：占用徽章/面板据 stage='completed' 提示压缩已触发。
 */
export interface CompressionEvent extends BaseEvent {
  event_type: 'compression'
  agent_name?: string
  stage: 'started' | 'completed'
  tool_results_compressed?: number | null
  original_size?: number | null
  compressed_size?: number | null
}

export interface InterruptRequestEvent extends BaseEvent {
  event_type: 'interrupt_request'
  interrupt_id: string
  interrupt_type: 'preview_confirm' | 'result_verify' | 'plan_confirm'
  payload: Record<string, unknown>
  timestamp: number
  actions: string[]
  agent_name?: string
}

export interface ReviewRequestedEvent extends BaseEvent {
  event_type: 'review_requested'
  interrupt_id: string
  interrupt_type: 'review_requested'
  payload: Record<string, unknown>
  timestamp: number
  actions: string[]
  agent_name?: string
}

export interface InterruptTimeoutEvent extends BaseEvent {
  event_type: 'interrupt_timeout'
  interrupt_id: string
  message?: string
}

export interface SessionIdleEvent extends BaseEvent {
  event_type: 'session_idle'
}

/**
 * 消息截断广播（add-session-history-pagination D12/D13/D14）。
 * 后端 truncate 端点经 push_ws_event 广播：截断已生效，其它标签应收敛视图。
 * request_id / history_version 为可选新字段，旧后端载荷缺省时走 count 对账降级。
 */
export interface MessagesTruncatedEvent extends BaseEvent {
  event_type: 'messages_truncated'
  truncated_count: number
  remaining_count: number
  /** 发起方的幂等键（D13）：本会话在途 truncate 匹配时用于广播抢先确认 */
  request_id?: string
  /** 截断后版本元组（D14）：断线标签下次拉取时比对收敛 */
  history_version?: { total_runs: number; total_messages: number }
}

export interface HeartbeatEvent extends BaseEvent {
  event_type: 'heartbeat'
}

/** 审批暂停事件（RunPausedEvent.tools[0] 特化） */
export interface ApprovalPendingEvent extends BaseEvent {
  event_type: 'approval_pending'
  run_id: string
  approval_id: string | null
  tool_name: string | null
  /** 审批载荷：request_approval → {kind,title,content_md}；case_repl → {code,purpose,expect} 等 */
  tool_args: Record<string, unknown> | null
  approval_type: string | null
  agent_name?: string
}

// ── 澄清卡 ───────────────────────────────────────────────────────────

/** ask_user 单题选项（agno UserFeedbackOption 同构） */
export interface ClarifyOption {
  label: string
  description?: string | null
}

/** ask_user 结构化问题（agno UserFeedbackQuestion 同构） */
export interface ClarifyQuestion {
  question: string
  header?: string | null
  multi_select: boolean
  options: ClarifyOption[]
}

/** get_user_input 表单字段（agno UserInputField 同构；field_type 为 Python 类型名） */
export interface ClarifyField {
  name: string
  field_type: string
  description?: string | null
  value?: unknown
}

/** 澄清暂停事件（RunPausedEvent 无 approval_type 的 HITL 暂停特化，design D3） */
export interface ClarificationRequestEvent extends BaseEvent {
  event_type: 'clarification_request'
  run_id: string
  tool_call_id: string
  tool_name: string | null
  kind: 'feedback' | 'form'
  questions: ClarifyQuestion[]
  fields: ClarifyField[]
  tool_args: Record<string, unknown> | null
  agent_name?: string
}

/** 澄清卡 timeline item（key = run_id + tool_call_id；无 approval_id/DB 权威源） */
export interface ClarifyCardItem {
  type: 'clarify_request'
  id: string
  run_id: string
  tool_call_id: string
  tool_name: string | null
  kind: 'feedback' | 'form'
  questions: ClarifyQuestion[]
  fields: ClarifyField[]
  tool_args: Record<string, unknown> | null
  /** 作答定格：null = 待答；非 null = 已答（回放自 message 历史 / 提交后本地写入） */
  answer: {
    values?: Record<string, unknown>
    selections?: Record<string, string[]>
  } | null
}

export type NormalizedEvent =
  | TokenEvent
  | ReasoningDeltaEvent
  | ToolStartEvent
  | ToolEndEvent
  | ToolErrorEvent
  | RunStartedEvent
  | RunCompleteEvent
  | RunErrorEvent
  | RunAbortedEvent
  | RunAbortingEvent
  | InterruptRequestEvent
  | ReviewRequestedEvent
  | InterruptTimeoutEvent
  | SessionIdleEvent
  | HeartbeatEvent
  | ApprovalPendingEvent
  | ClarificationRequestEvent
  | CompressionEvent
  | MessagesTruncatedEvent

// Timeline display items (rendered in ExecutionTimeline)
export type TimelineItemType =
  | 'agent_message'   // Grouped token bubble
  | 'agent_thinking'  // 思考折叠块（add-thinking-stream-display）
  | 'user_message'    // User input bubble
  | 'tool_call'       // ToolCallCard (start+end merged)
  | 'run_started'
  | 'run_complete'
  | 'run_error'
  | 'run_aborted'
  | 'run_aborting'
  | 'interrupt_marker'
  | 'approval_pending' // ApprovalCard（审批卡，RunPaused 特化）
  | 'clarify_request'  // ClarifyCard（澄清卡，HITL user_input / user_feedback）

export interface AgentMessageItem {
  type: 'agent_message'
  id: string
  agent_name: string
  content: string
  isStreaming: boolean
  backendMessageId?: string
}

/** 思考折叠块 timeline item（add-thinking-stream-display，design D4） */
export interface AgentThinkingItem {
  type: 'agent_thinking'
  id: string
  agent_name: string
  content: string
  isStreaming: boolean
  /** 首 delta 到达时间（前端时钟，摘要行时长计算用） */
  startedAt: number
  /** settle 时间（思考结束）；isStreaming 期间为 null */
  endedAt: number | null
}

export interface ToolCallItem {
  type: 'tool_call'
  id: string
  tool_name: string
  tool_call_id: string
  agent_name?: string
  inputs: Record<string, unknown>
  outputs?: unknown
  elapsed_ms?: number
  status: 'running' | 'done' | 'error'
  error?: string
  backendMessageId?: string
}

export interface RunStartedItem {
  type: 'run_started'
  id: string
  agent_name: string
}

export interface RunCompleteItem {
  type: 'run_complete'
  id: string
  final_response: string
}

export interface RunErrorItem {
  type: 'run_error'
  id: string
  error: string
}

export interface RunAbortedItem {
  type: 'run_aborted'
  id: string
  agent_name?: string
}

/**
 * 「中断中」过渡标记（fix-abort-latency D2）：非终态，终态事件到达时清除。
 * meta.status 保持后端真实值（running），展示层据该标记渲染中断中反馈。
 */
export interface RunAbortingItem {
  type: 'run_aborting'
  id: string
  agent_name?: string
}

export interface InterruptMarkerItem {
  type: 'interrupt_marker'
  id: string
  interrupt_type: string
  tool_name?: string
}

/** 审批卡 timeline item（pending 卡常驻直至决议） */
export interface ApprovalPendingItem {
  type: 'approval_pending'
  id: string
  run_id: string
  approval_id: string | null
  tool_name: string | null
  tool_args: Record<string, unknown> | null
  approval_type: string | null
  /** 决议定格：null = pending；决议后写入由 ApprovalCard 展示 */
  resolution: { status: 'approved' | 'rejected'; note?: string } | null
}

export interface UserMessageItem {
  type: 'user_message'
  id: string
  content: string
  username: string
  timestamp: number
  workspace_context?: {
    selected_paths: string[]
  }
  activate_skills?: string[]
  backendMessageId?: string
}

export type TimelineItem =
  | AgentMessageItem
  | AgentThinkingItem
  | UserMessageItem
  | ToolCallItem
  | RunStartedItem
  | RunCompleteItem
  | RunErrorItem
  | RunAbortedItem
  | RunAbortingItem
  | InterruptMarkerItem
  | ApprovalPendingItem
  | ClarifyCardItem
