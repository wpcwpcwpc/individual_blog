import { create } from 'zustand'
import type { ContextUsageResponse, SessionStatus, InterruptPayload, PageMeta } from '@/types/api'
import type { Approval } from '@/api/approvals'
import {
  truncateMessages as truncateMessagesApi,
  regenerateTurn as regenerateTurnApi,
  restoreSession as restoreSessionApi,
  getSessionMessages as getSessionMessagesApi,
  getSessionMessagesPage as getSessionMessagesPageApi,
  getSessionMessagesTail as getSessionMessagesTailApi,
  getContextUsage as getContextUsageApi,
  getUserSessions as getUserSessionsApi,
} from '@/api/sessions'
import { streamPool } from '@/services/streamPool'
import { useAuthStore } from '@/store/auth'
import { useCodingStore } from '@/stores/codingStore'
import { hydrateMessages, type HistoryEvent, type HistoryToolEndEvent } from '@/utils/hydrateHistory'
import { isHistoryPaginationEnabled } from '@/utils/historyPaging'
import type {
  TimelineItem,
  AgentMessageItem,
  AgentThinkingItem,
  UserMessageItem,
  ToolCallItem,
  NormalizedEvent,
  ReasoningDeltaEvent,
  ToolStartEvent,
  ToolEndEvent,
  ToolErrorEvent,
  RunStartedEvent,
  RunCompleteEvent,
  RunErrorEvent,
  InterruptRequestEvent,
  ReviewRequestedEvent,
  ApprovalPendingEvent,
  ApprovalPendingItem,
  ClarificationRequestEvent,
  ClarifyCardItem,
  ClarifyField,
  ClarifyQuestion,
  MessagesTruncatedEvent,
} from '@/types/events'

// ── Session metadata ──────────────────────────────────────────────
export interface SessionMeta {
  session_id: string
  agent_name: string
  mode: string
  status: SessionStatus
  // title is optional — backend SessionStatusResponse doesn't include it,
  // and it's populated later via setSessionTitle when the first message
  // arrives. Consumers should fallback (e.g. `meta.title || meta.agent_name`).
  title?: string
  game_version?: string
  module?: string
  error?: string
  created_at: number
  // User-visible hide flag (backed by SessionEntry.hidden). Missing = not hidden.
  hidden?: boolean
  // add-session-history-pagination D9：窗口内用户轮次计数（RefineChat isFirstTurn
  // 依据；窗口为 0 且 hasMore=false 才是真空会话——尾页必含最新 user 消息）
  turnCount?: number
}

export type LoadStatus = 'idle' | 'restoring' | 'loading_messages' | 'ready' | 'error'

// ── Session usage state（add-context-usage-visibility）─────────────
/** 单 agent 的 live 事件累加（run_complete.usage 逐轮累加）。 */
export interface UsageAggregate {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  runs: number
}

export interface SessionUsageState {
  /** agent_name → live 累加（事件驱动，刷新后清空；筛选 tabs 的名字来源之一） */
  byAgent: Record<string, UsageAggregate>
  /** 最近一次 GET /context-usage 快照（权威：刷新不丢、含分类拆解） */
  breakdown: ContextUsageResponse | null
  /** 本页面生命周期内收到过 compression 事件（popover 压缩提示行依据） */
  compressed: boolean
}

// ── Per-session record ────────────────────────────────────────────
/**
 * 历史分页窗口状态（add-session-history-pagination D1-D5）。
 * `undefined` = 全量模式（kill-switch / 未迁移入口）。
 */
export interface HistoryPaging {
  /** 是否存在更早的历史页 */
  hasMore: boolean
  /** 下一页游标（本窗口最老 run 的下标） */
  nextBefore: number
  /** 窗口最老 run 的锚标识（run_id 或退化形式 rt:{created_at}） */
  anchorRunId: string | null
  /** history_version 元组（D14），拉取时校验收敛 */
  runsVersion: { total_runs: number; total_messages: number } | null
  /** 向前翻页在途守卫 */
  fetchingOlder: boolean
}

/** D11 truncate 状态机（缺省 idle） */
export type TruncateState = 'idle' | 'confirming' | 'cutting' | 'reconnecting'

export type TimelineMutation = 'append' | 'prepend' | 'replace'

export interface SessionRecord {
  meta: SessionMeta
  timeline: TimelineItem[]
  interrupt: InterruptPayload | null
  // Track which agent_name is currently streaming (for token grouping)
  _activeAgentName: string | null
  // Session loading state (for sessions restored from backend)
  loadStatus: LoadStatus
  // Track last activated skills for Agent bubble display
  lastActivatedSkills: string[]
  // Last received seq_id for event deduplication and reconnection
  lastSeqId: number
  // Whether this session has an unread interrupt (for tab badge)
  hasUnreadInterrupt: boolean
  // M6 fix: WS connection status — 'disconnected' means StreamPool gave up
  // after MAX_RECONNECT_ATTEMPTS and the user must manually trigger reconnect.
  connectionStatus?: 'connected' | 'disconnected'
  // Staleness probe confirmed missed events — show StaleDisconnectBanner.
  // Distinct from connectionStatus: this fires when the staleness detector
  // compares backend seq_id vs local and finds divergence, NOT when StreamPool
  // exhausts its reconnect attempts.
  staleDisconnect?: boolean
  // add-session-history-pagination: 分页窗口状态（undefined = 全量模式）
  historyPaging?: HistoryPaging
  // add-session-history-pagination D11: truncate 状态机
  truncateState?: TruncateState
  // add-session-history-pagination D5: 最近一次 timeline 变更方式（自动滚底守卫：
  // 仅 'append' 允许 scrollToIndex(end)；'prepend'/'replace' 不拽回底部）
  lastMutation?: TimelineMutation
  // harden-create-session-entry D1：pending 占位卡创建失败原因（本地 UI 字段）
  createError?: string
}

// ── Store shape ───────────────────────────────────────────────────
interface SessionsState {
  sessions: Record<string, SessionRecord>
  activeSessionId: string | null

  // Actions
  addSession: (meta: SessionMeta) => void
  removeSession: (sessionId: string) => void
  /**
   * harden-create-session-entry D1：新建会话提交瞬间插入 `creating` 占位记录，
   * 返回临时 id（`pending-<uuid>`，`SessionPage` 对该前缀短路 on-demand 探测）。
   */
  addPendingSession: (agentName: string) => string
  /**
   * 占位 → 真实记录原子替换（单次 set，无闪断）；activeSessionId 指向占位时同步改指。
   */
  resolvePendingSession: (tempId: string, meta: SessionMeta) => void
  /** 占位转失败态：写 `createError`（本地 UI 字段，不进后端协议），卡片可移除后重试。 */
  rejectPendingSession: (tempId: string, error: string) => void
  /**
   * fix-session-io-blocking：乐观删除失败后的对账——重拉服务端会话列表，
   * 若该会话服务端仍存在则重新 addSession（不存在 = 服务端已删，本地移除即终态）。
   */
  resyncDeletedSession: (email: string, sessionId: string) => Promise<void>
  setActiveSession: (sessionId: string | null) => void
  updateSessionMeta: (sessionId: string, patch: Partial<SessionMeta>) => void
  setSessionTitle: (sessionId: string, title: string) => void
  setSessionHidden: (sessionId: string, hidden: boolean) => void
  setInterrupt: (sessionId: string, payload: InterruptPayload | null) => void
  appendEvent: (sessionId: string, event: NormalizedEvent) => void
  /**
   * fix-abort-latency D3：乐观「中断中」——POST /abort 成功后立即本地渲染，
   * 作为弱网 / WS 断线时的即时反馈兜底（复用 run_aborting 事件处理，幂等）。
   * POST 失败 MUST NOT 调用（run 可能仍在正常跑，不得误导）。
   */
  markAborting: (sessionId: string, agentName?: string) => void
  /** 断连恢复：pending 审批回灌 timeline（按 approval_id 去重，已挂卡不重复） */
  appendApprovalCards: (sessionId: string, approvals: Approval[]) => void
  /**
   * 澄清作答定格：提交成功后本地写入 answer。
   * 刷新后由 message 历史派生（deriveClarifyCardFromHistory），此处仅 live 一致性。
   */
  markClarifyAnswered: (sessionId: string, toolCallId: string, answer: { values?: Record<string, unknown>; selections?: Record<string, string[]> }) => void
  /** D11 truncate 状态机驱动（bubble 打开/取消 modal 时设置 confirming/idle） */
  setTruncateState: (sessionId: string, state: TruncateState) => void
  /**
   * D12 跨标签截断收敛：收到 messages_truncated 广播（另一标签触发 truncate）。
   * 自/外部判别 → 外部则拉权威消息重灌 + seq 水位清零 + meta.status 同步。
   */
  handleMessagesTruncated: (sessionId: string, payload: MessagesTruncatedEvent) => Promise<void>
  /**
   * add-session-history-pagination D1/D7：向前翻页——拉取更早历史页并 prepend
   * 到窗口头部（run 原子页，页内消息已带全量位号）。锚漂移时内部转 refreshTail。
   * Returns: true = 已处理（prepend 成功或 resync 收敛）；false = 无操作或失败。
   */
  prependHistoryPage: (sessionId: string) => Promise<boolean>
  /**
   * add-session-history-pagination D2：尾页回灌原语——按 run 边界 splice 替换
   * 窗口尾部（前缀保留），dedup 依据位号区间；单飞合并（在途仅记待重跑）。
   * 全量模式（无 historyPaging）= 整条时间线替换。
   */
  refreshTail: (sessionId: string) => Promise<void>
  /**
   * 乐观插入用户消息，返回新 timeline item 的 id（发送侧持有，失败时供
   * `removeUserMessage` 精确移除——fix-refine-chat-optimistic-send D2）。
   */
  appendUserMessage: (sessionId: string, content: string, username: string, workspaceContext?: { selected_paths: string[] }, activateSkills?: string[], uploadedFile?: { file_id: string; name: string; abs_path: string } | null) => string
  /**
   * fix-refine-chat-optimistic-send D1：乐观用户消息的失败还原原语。
   * 按 item id 精确移除（不盲删末尾），turnCount 同步回退。
   */
  removeUserMessage: (sessionId: string, itemId: string) => void
  setLoadStatus: (sessionId: string, status: LoadStatus) => void
  hydrateSessionEvents: (sessionId: string, events: HistoryEvent[]) => void
  /**
   * add-session-history-pagination D2：尾页首载 hydrate——事件回灌 + 从 page_meta
   * 初始化 historyPaging 分页窗口状态。三个加载入口（paged 模式）使用。
   */
  hydrateTailPage: (sessionId: string, events: HistoryEvent[], meta: PageMeta | null) => void
  /**
   * 删除回复及后续。
   *
   * 流程：乐观本地截断（确认即删，不等网络）→ truncate API → 响应内
   * remaining_messages 立即权威回灌 → restore + 重连后台化。失败重拉回滚。
   *
   * @param itemId 可选，被点击的 timeline item id（乐观截断定位用）
   * fix-resend-from-here D1/D2：backendMessageId 可延迟传入——确认后先乐观切
   * （cutting 互斥即生效，cutIdx 不漂移），id 解析后置；解析兜底由 resolveBackendMessageId
   * 提供（live 卡走一次尾页内容匹配），失败按 R1 原位回插。签名同时供
   * add-turn-regenerate 复用（按轮次锚定位后直传）。
   */
  truncateTimeline: (sessionId: string, backendMessageId: string | undefined, itemId?: string, resolveBackendMessageId?: () => Promise<string | null>) => Promise<void>
  /**
   * 轮次重答（add-turn-regenerate D3/D4/D6）：截断保留至该轮用户消息（U 原位
   * 保留，乐观切 U 之后全部），后端 acontinue_run 续跑（不追加用户消息），新回
   * 复经 WS 重连 seq 0 重放渲染。状态机/幂等/R1/R2/跨标签收敛与 truncateTimeline
   * 同构；turnCount 不扣减（U 未删）。
   *
   * @param userItemId 该轮用户卡片（U）的 timeline item id
   */
  regenerateTurn: (sessionId: string, userItemId: string) => Promise<void>
  setDraftContent: (sessionId: string, content: string) => void
  draftContent: Record<string, string>  // sessionId → draft text
  // M6 fix: mark a session's WS as disconnected after StreamPool gives up
  markSessionDisconnected: (sessionId: string) => void
  markSessionConnected: (sessionId: string) => void
  // Staleness detector: mark a session as stale-disconnect (probe confirmed
  // missed events) or clear it (new content arrived / manual reconnect).
  markSessionStaleDisconnect: (sessionId: string, value: boolean) => void
  // fix-event-seq-blackout D3: sync a session's status from the backend's
  // authoritative view (last-seq probe) — e.g. a missed run_complete left
  // the session stuck 'running' locally. Also clears the seq high-water
  // mark (sessionStorage + store) since the run is over.
  syncSessionStatus: (sessionId: string, status: SessionStatus) => void
  // add-context-usage-visibility: 会话累计消耗与上下文占用快照。
  // refreshContextUsage 静默拉端点，失败保持旧值不弹错（spec: 端点失败不阻塞）。
  // force=true 旁路后端缓存强制重算（弹窗手动刷新按钮）。
  refreshContextUsage: (sessionId: string, force?: boolean) => Promise<void>
  sessionUsage: Record<string, SessionUsageState>
}

// ── Unique ID generator ───────────────────────────────────────────
let _idCounter = 0
const uid = () => `item-${++_idCounter}-${Date.now()}`

/** 空会话记录（addSession / resolvePendingSession 共用形状，防两处漂移） */
function emptyRecord(meta: SessionMeta): SessionRecord {
  return {
    meta,
    timeline: [],
    interrupt: null,
    _activeAgentName: null,
    loadStatus: 'idle',
    lastActivatedSkills: [],
    lastSeqId: 0,
    hasUnreadInterrupt: false,
  }
}

// ── D13 truncate 幂等/重试协议状态（模块级瞬态，不进 UI state）──────
// Truncate idempotency.
interface TruncateAttempt {
  requestId: string
  /** WS 广播抢先确认：响应丢失但截断已生效（服务端 push_ws_event 先于响应写出） */
  broadcastConfirmed: boolean
}
const truncateAttempts = new Map<string, TruncateAttempt>()

/** D13 重试退避表：网络类失败同 request_id 有界重试 2 次（500ms / 2s） */
const TRUNCATE_RETRY_BACKOFF_MS = [500, 2000]

/** axios 错误 → HTTP status（非 HTTP 错误返回 undefined） */
function httpStatusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status
}

// ── refreshTail 单飞合并（D12.6）：在途时后续触发仅记「待重跑」─────
const refreshTailInFlight = new Set<string>()
const refreshTailRerunRequested = new Set<string>()

/** backendMessageId（"msg_{i}"）→ 位号 int；无 id（live 项）→ null */
function parseBackendSeq(backendMessageId: string | null | undefined): number | null {
  if (!backendMessageId) return null
  const m = /^msg_(\d+)$/.exec(backendMessageId)
  return m ? Number(m[1]) : null
}

/** 窗口内用户消息数（turnCount 维护用，D9） */
function countUserItems(timeline: TimelineItem[]): number {
  let n = 0
  for (const t of timeline) {
    if (t.type === 'user_message') n += 1
  }
  return n
}

/** 移除「中断中」过渡标记（fix-abort-latency D2：终态事件到达即清除） */
function dropAbortingMarker(timeline: TimelineItem[]): TimelineItem[] {
  return timeline.some(t => t.type === 'run_aborting')
    ? timeline.filter(t => t.type !== 'run_aborting')
    : timeline
}

/**
 * 「中断中」判定（fix-abort-latency D2）：由 timeline 过渡标记驱动，meta.status
 * 保持后端真实值（running）。终态事件到达时标记被清除。
 */
export function isAborting(record: SessionRecord | undefined): boolean {
  return record?.timeline.some(t => t.type === 'run_aborting') ?? false
}

/** Approval 记录 → 审批卡 timeline item（断连恢复回灌对话流用） */
function toApprovalCardItem(a: Approval): ApprovalPendingItem {
  return {
    type: 'approval_pending',
    id: uid(),
    run_id: a.run_id ?? '',
    approval_id: a.id,
    tool_name: a.tool_name,
    tool_args: a.tool_args,
    approval_type: a.approval_type,
    resolution: null,
  }
}

// ── 澄清卡派生 ───────────────────────────────────────────────────────

/**
 * tool result 文本 → 澄清作答。agno 续跑时注入的 tool message：
 * feedback = `User feedback received: [{"question": …, "selected": […]}]`
 * form     = `User inputs retrieved: [{"name": …, "value": …}]`
 * 解析失败返回 {}（仍视为已答，不回退待答态）。
 */
function parseClarifyAnswer(raw: unknown): ClarifyCardItem['answer'] {
  if (typeof raw !== 'string') return null
  const idx = raw.indexOf(':')
  if (idx < 0) return {}
  try {
    const parsed: unknown = JSON.parse(raw.slice(idx + 1).trim())
    if (!Array.isArray(parsed)) return {}
    const selections: Record<string, string[]> = {}
    const values: Record<string, unknown> = {}
    for (const item of parsed) {
      if (!item || typeof item !== 'object') continue
      const rec = item as Record<string, unknown>
      if ('question' in rec && 'selected' in rec) {
        selections[String(rec.question)] = Array.isArray(rec.selected) ? (rec.selected as string[]) : []
      } else if ('name' in rec) {
        values[String(rec.name)] = rec.value
      }
    }
    return {
      selections: Object.keys(selections).length > 0 ? selections : undefined,
      values: Object.keys(values).length > 0 ? values : undefined,
    }
  } catch {
    // ignore: 非 JSON 形态（如错误文本）→ 已答但无结构化答案
    return {}
  }
}

/** History tool_end（ask_user / get_user_input）→ 澄清卡 item（4.4 刷新回放源）。
 *  tool_result 有无 = 已答/未答（agno 续跑后注入 tool message，hydrateHistory
 *  既有 tool_call_id 合并机制直接给出结果）。回放卡 run_id 缺失（message 历史
 *  不带）→ 由 fetchPendingClarification 在卡片侧自愈。 */
function deriveClarifyCardFromHistory(event: HistoryToolEndEvent): ClarifyCardItem {
  const args = (event.tool_args ?? {}) as Record<string, unknown>
  const kind: 'feedback' | 'form' = event.tool_name === 'ask_user' ? 'feedback' : 'form'
  const questions: ClarifyQuestion[] =
    kind === 'feedback' && Array.isArray(args.questions)
      ? (args.questions as Array<Record<string, unknown>>).map(q => ({
          question: String(q.question ?? ''),
          header: (q.header as string) ?? null,
          multi_select: Boolean(q.multi_select),
          options: (Array.isArray(q.options) ? q.options : ([] as Array<Record<string, unknown>>)).map(o => ({
            label: String(o.label ?? ''),
            description: (o.description as string) ?? null,
          })),
        }))
      : []
  const fields: ClarifyField[] =
    kind === 'form' && Array.isArray(args.user_input_fields)
      ? (args.user_input_fields as Array<Record<string, unknown>>).map(f => ({
          name: String(f.field_name ?? f.name ?? ''),
          field_type: String(f.field_type ?? 'str'),
          description: (f.field_description as string) ?? (f.description as string) ?? null,
        }))
      : []
  return {
    type: 'clarify_request',
    id: uid(),
    run_id: '',
    tool_call_id: event.tool_call_id,
    tool_name: event.tool_name,
    kind,
    questions,
    fields,
    tool_args: args,
    answer: event.tool_result != null ? (parseClarifyAnswer(event.tool_result) ?? {}) : null,
  }
}

/**
 * HistoryEvent[] → TimelineItem[]：hydrateSessionEvents / prependHistoryPage /
 * refreshTail 共用的纯转换（无 seq / sessionStorage 副作用）。
 */
function eventsToTimeline(events: HistoryEvent[]): TimelineItem[] {
  let timeline: TimelineItem[] = []
  for (const event of events) {
    switch (event.event_type) {
      case 'reasoning': {
        // 历史 assistant 消息的整段思考快照 — 直接整块 append settled 思考块
        // （不走 appendEvent('reasoning_delta')：避免 seq 去重/sessionStorage 写入等
        // live 专属副作用，以及尾部块追加规则误合并相邻两轮思考）。
        // startedAt/endedAt 同值（消息 created_at 毫秒）→ 零时长，摘要行仅展示字数。
        const thinkingItem: AgentThinkingItem = {
          type: 'agent_thinking',
          id: uid(),
          agent_name: event.agent_name ?? '',
          content: event.content,
          isStreaming: false,
          startedAt: event.timestamp,
          endedAt: event.timestamp,
        }
        timeline = [...timeline, thinkingItem]
        break
      }
      case 'token':
        timeline = applyTokenEvent(timeline, event)
        break
      case 'tool_end': {
        // 澄清工具（ask_user / get_user_input）→ 澄清卡派生（4.4 回放源：
        // message 历史；tool_result 有无 = 已答/未答），不走通用工具卡。
        if (event.tool_name === 'ask_user' || event.tool_name === 'get_user_input') {
          timeline = [...timeline, deriveClarifyCardFromHistory(event)]
          break
        }
        // History tool events come pre-merged as tool_end (from hydrateHistory.ts)
        const card: ToolCallItem = {
          type: 'tool_call',
          id: uid(),
          tool_name: event.tool_name,
          tool_call_id: event.tool_call_id,
          agent_name: event.agent_name,
          inputs: event.tool_args,
          outputs: event.tool_result,
          status: 'done',
          backendMessageId: event.backend_message_id,
        }
        timeline = [...timeline, card]
        break
      }
      case 'user_message': {
        const userItem: UserMessageItem = {
          type: 'user_message',
          id: uid(),
          content: event.content,
          username: event.username ?? '用户',
          timestamp: event.timestamp ?? 0,
          ...(event.workspace_context ? { workspace_context: event.workspace_context } : {}),
          backendMessageId: event.backend_message_id,
        }
        timeline = [...timeline, userItem]
        break
      }
      default:
        break
    }
  }
  // Finalize any open streaming bubble
  return timeline.map(item =>
    item.type === 'agent_message' && item.isStreaming
      ? { ...item, isStreaming: false }
      : item
  )
}

// ── Token event handler ───────────────────────────────────────────
function applyTokenEvent(
  timeline: TimelineItem[],
  event: { agent_name?: string; content: string; backend_message_id?: string },
): TimelineItem[] {
  const agentName = event.agent_name ?? 'Agent'

  // token 到达 → 尾部流式思考块 settle（add-thinking-stream-design D4 状态机：
  // 思考结束，token 流开始；混合帧同帧到达时先 settle 再渲染消息气泡）
  const settled = settleStreamingThinking(timeline)
  const last = settled[settled.length - 1]

  // Merge into existing streaming bubble if same agent
  if (last?.type === 'agent_message' && last.isStreaming && last.agent_name === agentName) {
    const updated: AgentMessageItem = {
      ...last,
      content: last.content + event.content,
      // Preserve the backendMessageId from the first token, or use the new one
      backendMessageId: last.backendMessageId ?? event.backend_message_id,
    }
    return [...settled.slice(0, -1), updated]
  }

  // Finalize previous streaming bubble
  const finalized = last?.type === 'agent_message' && last.isStreaming
    ? [...settled.slice(0, -1), { ...last, isStreaming: false }]
    : settled

  const newBubble: AgentMessageItem = {
    type: 'agent_message',
    id: uid(),
    agent_name: agentName,
    content: event.content,
    isStreaming: true,
    backendMessageId: event.backend_message_id,
  }
  return [...finalized, newBubble]
}

// ── Thinking event handler（add-thinking-stream-display）──────────
/** settle 所有流式思考块：isStreaming=false 并记录 endedAt（摘要行时长用） */
function settleStreamingThinking(timeline: TimelineItem[]): TimelineItem[] {
  return timeline.map(item =>
    item.type === 'agent_thinking' && item.isStreaming
      ? { ...item, isStreaming: false, endedAt: Date.now() }
      : item
  )
}

function applyReasoningDelta(
  timeline: TimelineItem[],
  event: ReasoningDeltaEvent,
): TimelineItem[] {
  const agentName = event.agent_name ?? 'Agent'
  const last = timeline[timeline.length - 1]

  // 尾部流式思考块 → 追加增量（多段思考：非尾部/已 settle 的块不动，新段新块）
  if (last?.type === 'agent_thinking' && last.isStreaming) {
    return [...timeline.slice(0, -1), { ...last, content: last.content + event.content }]
  }

  const block: AgentThinkingItem = {
    type: 'agent_thinking',
    id: uid(),
    agent_name: agentName,
    content: event.content,
    isStreaming: true,
    startedAt: Date.now(),
    endedAt: null,
  }
  return [...timeline, block]
}

// ── Tool event handlers ───────────────────────────────────────────
function applyToolStart(timeline: TimelineItem[], event: ToolStartEvent): TimelineItem[] {
  // Finalize any open streaming bubble / thinking block
  const finalized = settleStreamingThinking(timeline).map(item =>
    item.type === 'agent_message' && item.isStreaming
      ? { ...item, isStreaming: false }
      : item
  )
  const card: ToolCallItem = {
    type: 'tool_call',
    id: uid(),
    tool_name: event.tool_name,
    tool_call_id: event.tool_call_id,
    agent_name: event.agent_name,
    inputs: event.inputs,
    status: 'running',
  }
  return [...finalized, card]
}

function applyToolEnd(timeline: TimelineItem[], event: ToolEndEvent): TimelineItem[] {
  return timeline.map(item => {
    if (item.type === 'tool_call' && item.tool_call_id === event.tool_call_id) {
      return { ...item, outputs: event.outputs, elapsed_ms: event.elapsed_ms, status: 'done' as const }
    }
    return item
  })
}

function applyToolError(timeline: TimelineItem[], event: ToolErrorEvent): TimelineItem[] {
  return timeline.map(item => {
    if (item.type === 'tool_call' && item.tool_call_id === event.tool_call_id) {
      return { ...item, status: 'error' as const, error: event.error }
    }
    return item
  })
}

// ── Store ─────────────────────────────────────────────────────────
export const useSessionsStore = create<SessionsState>((set, get) => ({
  sessions: {},
  activeSessionId: null,
  draftContent: {},
  sessionUsage: {},

  addSession: (meta) => set(state => ({
    sessions: {
      ...state.sessions,
      [meta.session_id]: emptyRecord(meta),
    },
  })),

  addPendingSession: (agentName) => {
    // 非安全上下文（http 直连局域网 IP）下 crypto.randomUUID 不存在——同 line 1107 守卫
    const tempId = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
      ? `pending-${crypto.randomUUID()}`
      : `pending-${Date.now()}-${Math.random().toString(36).slice(2)}`
    get().addSession({
      session_id: tempId,
      agent_name: agentName,
      mode: 'normal',
      status: 'creating',
      title: '创建中…',
      created_at: Date.now(),
    })
    return tempId
  },

  resolvePendingSession: (tempId, meta) => set(state => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- 占位仅用于剔除该 key
    const { [tempId]: _pending, ...rest } = state.sessions
    return {
      sessions: { ...rest, [meta.session_id]: emptyRecord(meta) },
      activeSessionId: state.activeSessionId === tempId ? meta.session_id : state.activeSessionId,
    }
  }),

  rejectPendingSession: (tempId, error) => set(state => {
    const record = state.sessions[tempId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        [tempId]: { ...record, createError: error },
      },
    }
  }),

  removeSession: (sessionId) => {
    // store 级不变量：记录没了 ⇒ WS 连接没了。删除入口（SessionList/
    // SessionPage/编码工作台）已预关，这里兜底其余移除路径，防止残留连接
    // 对已删除会话重拨（后端 4004 →「[StreamPool] WS error」）。
    streamPool.closeConnection(sessionId)
    set(state => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- 占位仅为 rest 解构剔除该 key（无值语义）
    const { [sessionId]: _removed, ...rest } = state.sessions
    // add-context-usage-visibility: 级联清理 usage 状态（防泄漏 + 防旧数据串场）
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- 同上，占位剔除
    const { [sessionId]: _removedUsage, ...restUsage } = state.sessionUsage
    return {
      sessions: rest,
      activeSessionId: state.activeSessionId === sessionId ? null : state.activeSessionId,
      sessionUsage: restUsage,
    }
    })
  },

  resyncDeletedSession: async (email, sessionId) => {
    try {
      const res = await getUserSessionsApi(email)
      const meta = res.sessions.find(s => s.session_id === sessionId)
      if (!meta) return  // 服务端已删（如 404 竞态）——本地移除即终态
      get().addSession({
        session_id: meta.session_id,
        agent_name: meta.agent_name,
        mode: meta.mode,
        status: meta.status ?? 'idle',
        title: meta.title,
        game_version: meta.game_version,
        module: meta.module,
        created_at: meta.created_at,
        hidden: meta.hidden,
      })
    } catch {
      console.warn('[SessionsStore] resyncDeletedSession failed — list reconciles on next app boot')
    }
  },

  setActiveSession: (sessionId) => set(state => {
    const updates: Record<string, SessionRecord> = { ...state.sessions }
    // Clear unread interrupt badge when switching to a session
    if (sessionId && updates[sessionId]) {
      updates[sessionId] = { ...updates[sessionId], hasUnreadInterrupt: false }
    }
    return { activeSessionId: sessionId, sessions: updates }
  }),

  updateSessionMeta: (sessionId, patch) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, meta: { ...record.meta, ...patch } },
      },
    }
  }),

  setSessionTitle: (sessionId, title) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, meta: { ...record.meta, title } },
      },
    }
  }),

  setSessionHidden: (sessionId, hidden) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, meta: { ...record.meta, hidden } },
      },
    }
  }),

  setInterrupt: (sessionId, payload) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, interrupt: payload },
      },
    }
  }),

  appendEvent: (sessionId, event) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state

    // ── Reset lastSeqId on new run (seq_id is per-run, restarts from 1) ──
    let effectiveLastSeqId = record.lastSeqId
    if (event.event_type === 'run_started') {
      effectiveLastSeqId = 0
      try { sessionStorage.removeItem(`stream_seq_${sessionId}`) } catch { /* ignore */ }
    }

    // ── seq_id deduplication ──
    const seqId = event.seq_id
    if (seqId != null && seqId <= effectiveLastSeqId) {
      return state // duplicate event, skip
    }

    // ── Update lastSeqId and persist ──
    let newLastSeqId = effectiveLastSeqId
    if (seqId != null && seqId > effectiveLastSeqId) {
      newLastSeqId = seqId
      try { sessionStorage.setItem(`stream_seq_${sessionId}`, String(seqId)) } catch { /* ignore */ }
    }

    let timeline = [...record.timeline]
    let metaPatch: Partial<SessionMeta> = {}
    let extraPatch: Partial<SessionRecord> = {}
    let usageStatePatch: SessionUsageState | null = null

    switch (event.event_type) {
      case 'token':
        timeline = applyTokenEvent(timeline, event)
        break

      case 'reasoning_delta':
        timeline = applyReasoningDelta(timeline, event as ReasoningDeltaEvent)
        break

      case 'tool_start':
        timeline = applyToolStart(timeline, event as ToolStartEvent)
        break

      case 'tool_end':
        timeline = applyToolEnd(timeline, event as ToolEndEvent)
        break

      case 'tool_error':
        timeline = applyToolError(timeline, event as ToolErrorEvent)
        break

      case 'run_started': {
        const e = event as RunStartedEvent
        timeline = [
          ...dropAbortingMarker(timeline).map(item => {
            if (item.type === 'agent_message' && item.isStreaming) return { ...item, isStreaming: false }
            if (item.type === 'agent_thinking' && item.isStreaming) return { ...item, isStreaming: false, endedAt: Date.now() }
            if (item.type === 'tool_call' && item.status === 'running') return { ...item, status: 'error' as const, error: 'Aborted by new run' }
            return item
          }),
          { type: 'run_started', id: uid(), agent_name: e.agent_name },
        ]
        metaPatch = { status: 'running' }
        break
      }

      case 'approval_pending': {
        // 审批暂停：pending 审批卡常驻 timeline，run 状态置
        // completed（暂停即本轮终态，续跑走 continue 通道，非 error/aborted）
        const e = event as ApprovalPendingEvent
        // 同步审批待办 store（断连恢复数据源；live pause 时 store 里是旧数据）
        void useCodingStore.getState().refreshApprovals(e.session_id)
        // add-context-usage-visibility: 审批暂停即一轮终态（run 不发
        // run_complete），此处同步刷新占用数据（后端已在暂停时失效缓存）。
        queueMicrotask(() => { void get().refreshContextUsage(sessionId) })
        // 同一审批已由待办回灌挂卡 → 不重复
        const duplicated = timeline.some(
          it => it.type === 'approval_pending' && it.approval_id !== null && it.approval_id === e.approval_id,
        )
        if (duplicated) break
        timeline = [
          ...timeline.map(item => {
            if (item.type === 'agent_message' && item.isStreaming) return { ...item, isStreaming: false }
            if (item.type === 'agent_thinking' && item.isStreaming) return { ...item, isStreaming: false, endedAt: Date.now() }
            return item
          }),
          {
            type: 'approval_pending' as const,
            id: uid(),
            run_id: e.run_id,
            approval_id: e.approval_id,
            tool_name: e.tool_name,
            tool_args: e.tool_args,
            approval_type: e.approval_type,
            resolution: null,
          },
        ]
        metaPatch = { status: 'completed' }
        break
      }

      case 'clarification_request': {
        // 澄清暂停：澄清卡常驻 timeline，run 状态置
        // completed（暂停即本轮终态，续跑走 continue 通道带 clarifications 作答）。
        // 去重 key = tool_call_id（澄清无 approval_id / DB 记录）。
        const e = event as ClarificationRequestEvent
        const duplicated = timeline.some(
          it => it.type === 'clarify_request' && it.tool_call_id === e.tool_call_id,
        )
        if (duplicated) break
        timeline = [
          ...timeline.map(item => {
            if (item.type === 'agent_message' && item.isStreaming) return { ...item, isStreaming: false }
            if (item.type === 'agent_thinking' && item.isStreaming) return { ...item, isStreaming: false, endedAt: Date.now() }
            return item
          }),
          {
            type: 'clarify_request' as const,
            id: uid(),
            run_id: e.run_id,
            tool_call_id: e.tool_call_id,
            tool_name: e.tool_name,
            kind: e.kind,
            questions: e.questions,
            fields: e.fields,
            tool_args: e.tool_args,
            answer: null,
          },
        ]
        metaPatch = { status: 'completed' }
        break
      }

      case 'run_complete': {
        const e = event as RunCompleteEvent
        timeline = [
          ...dropAbortingMarker(timeline).map(item => {
            if (item.type === 'agent_message' && item.isStreaming) return { ...item, isStreaming: false }
            if (item.type === 'agent_thinking' && item.isStreaming) return { ...item, isStreaming: false, endedAt: Date.now() }
            if (item.type === 'tool_call' && item.status === 'running') return { ...item, status: 'done' as const }
            return item
          }),
          { type: 'run_complete', id: uid(), final_response: e.final_response },
        ]
        metaPatch = { status: 'completed' }
        // Clear lastActivatedSkills on run complete
        extraPatch = { lastActivatedSkills: [] }
        // add-context-usage-visibility: live 累加 + 静默拉端点刷新
        // （后端已在 run_complete 时失效缓存，本次 refresh 必为新鲜数据）。
        if (e.usage) {
          const agentName = e.agent_name ?? 'main'
          const prevUsage = state.sessionUsage[sessionId]
          const prevAgg = prevUsage?.byAgent[agentName]
          usageStatePatch = {
            byAgent: {
              ...prevUsage?.byAgent,
              [agentName]: {
                input_tokens: (prevAgg?.input_tokens ?? 0) + e.usage.input_tokens,
                output_tokens: (prevAgg?.output_tokens ?? 0) + e.usage.output_tokens,
                total_tokens: (prevAgg?.total_tokens ?? 0) + e.usage.total_tokens,
                runs: (prevAgg?.runs ?? 0) + 1,
              },
            },
            breakdown: prevUsage?.breakdown ?? null,
            compressed: prevUsage?.compressed ?? false,
          }
        }
        // continue 通道的 run_complete 不带 usage（原始 agno 事件，未经
        // stream_adapter 提取），但后端已在续跑终态失效缓存 —— 刷新不看门。
        queueMicrotask(() => { void get().refreshContextUsage(sessionId) })
        break
      }

      case 'run_error': {
        const e = event as RunErrorEvent
        timeline = [
          ...dropAbortingMarker(timeline).map(item => {
            if (item.type === 'agent_message' && item.isStreaming) return { ...item, isStreaming: false }
            if (item.type === 'agent_thinking' && item.isStreaming) return { ...item, isStreaming: false, endedAt: Date.now() }
            if (item.type === 'tool_call' && item.status === 'running') return { ...item, status: 'error' as const, error: e.error }
            return item
          }),
          { type: 'run_error', id: uid(), error: e.error },
        ]
        metaPatch = { status: 'failed', error: e.error }
        break
      }

      case 'run_aborting': {
        // fix-abort-latency D2：「中断中」过渡态 — 冻结流式渲染（与 run_aborted
        // 同款），但 **不改写 meta.status**（后端此刻仍是 running，终态由
        // run_aborted 收口）。幂等：乐观 dispatch 与 WS 事件双来源，已有标记
        // 则不重复追加（tool_call 保持真实 status —— 工具侧此刻确实仍在收尾，
        // 由 run_aborted 统一置 error）。
        timeline = [
          ...timeline.map(item => {
            if (item.type === 'agent_message' && item.isStreaming) return { ...item, isStreaming: false }
            if (item.type === 'agent_thinking' && item.isStreaming) return { ...item, isStreaming: false, endedAt: Date.now() }
            return item
          }),
          ...(timeline.some(t => t.type === 'run_aborting')
            ? []
            : [{ type: 'run_aborting' as const, id: uid(), agent_name: (event as { agent_name?: string }).agent_name }]),
        ]
        break
      }

      case 'run_aborted': {
        timeline = [
          ...dropAbortingMarker(timeline).map(item => {
            if (item.type === 'agent_message' && item.isStreaming) return { ...item, isStreaming: false }
            if (item.type === 'agent_thinking' && item.isStreaming) return { ...item, isStreaming: false, endedAt: Date.now() }
            if (item.type === 'tool_call' && item.status === 'running') return { ...item, status: 'error' as const, error: 'Run aborted' }
            return item
          }),
          { type: 'run_aborted' as const, id: uid(), agent_name: (event as { agent_name?: string }).agent_name },
        ]
        metaPatch = { status: 'aborted' }
        break
      }

      case 'interrupt_request': {
        const e = event as InterruptRequestEvent
        // Add marker to timeline
        timeline = [
          ...timeline.map(item =>
            item.type === 'agent_message' && item.isStreaming
              ? { ...item, isStreaming: false }
              : item.type === 'agent_thinking' && item.isStreaming
                ? { ...item, isStreaming: false, endedAt: Date.now() }
                : item
          ),
          {
            type: 'interrupt_marker',
            id: uid(),
            interrupt_type: e.interrupt_type,
            tool_name: (e.payload?.tool_name as string) ?? undefined,
          },
        ]
        // Interrupt payload is set separately via setInterrupt
        metaPatch = { status: 'interrupt_pending' }
        break
      }

      case 'review_requested': {
        const e = event as ReviewRequestedEvent
        timeline = [
          ...timeline.map(item =>
            item.type === 'agent_message' && item.isStreaming
              ? { ...item, isStreaming: false }
              : item.type === 'agent_thinking' && item.isStreaming
                ? { ...item, isStreaming: false, endedAt: Date.now() }
                : item
          ),
          {
            type: 'interrupt_marker',
            id: uid(),
            interrupt_type: e.interrupt_type,
            tool_name: undefined,
          },
        ]
        metaPatch = { status: 'interrupt_pending' }
        break
      }

      case 'session_idle':
        timeline = dropAbortingMarker(timeline)
        metaPatch = { status: 'idle' as SessionStatus }
        extraPatch = { loadStatus: 'idle' as LoadStatus }
        break

      case 'interrupt_timeout':
        metaPatch = { status: 'running' }
        extraPatch = { interrupt: null }
        break

      case 'heartbeat':
        // ignore — only update lastSeqId (already done above)
        return {
          sessions: {
            ...state.sessions,
            [sessionId]: { ...record, lastSeqId: newLastSeqId },
          },
        }

      case 'compression': {
        // add-context-usage-visibility: L2 压缩已触发标记（popover 提示行依据）。
        // 占用骤降源于压缩，避免用户误判为数据丢失。
        const prevUsage = state.sessionUsage[sessionId]
        if (prevUsage?.compressed) break
        usageStatePatch = {
          byAgent: prevUsage?.byAgent ?? {},
          breakdown: prevUsage?.breakdown ?? null,
          compressed: true,
        }
        break
      }
    }

    // fix-event-seq-blackout D4: terminal events drop the seq high-water
    // mark (sessionStorage + store) — mirrors the run_started reset above,
    // aligning the client mark with the run lifecycle so a post-run refresh
    // reconnects with last_event_id=0 instead of a stale high-water mark.
    if (event.event_type === 'run_complete' || event.event_type === 'run_error' || event.event_type === 'run_aborted') {
      newLastSeqId = 0
      try { sessionStorage.removeItem(`stream_seq_${sessionId}`) } catch { /* ignore */ }
    }

    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...record,
          ...extraPatch,
          timeline,
          meta: { ...record.meta, ...metaPatch },
          lastSeqId: newLastSeqId,
          lastMutation: 'append' as const,
        },
      },
      ...(usageStatePatch
        ? { sessionUsage: { ...state.sessionUsage, [sessionId]: usageStatePatch } }
        : {}),
    }
  }),

  markAborting: (sessionId, agentName) => {
    get().appendEvent(sessionId, {
      event_type: 'run_aborting',
      session_id: sessionId,
      agent_name: agentName,
    })
  },

  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- 参数占位保持接口签名对齐（uploadedFile 渲染由 MessageInputBar 乐观插入完成）
  appendUserMessage: (sessionId, content, username, workspaceContext, activateSkills, _uploadedFile) => {
    const itemId = uid()
    set(state => {
      const record = state.sessions[sessionId]
      if (!record) return state
      // Finalize any streaming agent_message, then append user message
      const finalized = record.timeline.map(item =>
        item.type === 'agent_message' && item.isStreaming
          ? { ...item, isStreaming: false }
          : item
      )
      const userItem: UserMessageItem = {
        type: 'user_message',
        id: itemId,
        content,
        username,
        timestamp: Date.now(),
        ...(workspaceContext ? { workspace_context: workspaceContext } : {}),
        ...(activateSkills?.length ? { activate_skills: activateSkills } : {}),
      }
      return {
        sessions: {
          ...state.sessions,
          [sessionId]: {
            ...record,
            timeline: [...finalized, userItem],
            lastActivatedSkills: activateSkills?.length ? activateSkills : [],
            lastMutation: 'append' as const,
            meta: { ...record.meta, turnCount: (record.meta.turnCount ?? 0) + 1 },
          },
        },
      }
    })
    return itemId
  },

  removeUserMessage: (sessionId, itemId) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    const target = record.timeline.find(t => t.id === itemId && t.type === 'user_message')
    if (!target) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...record,
          timeline: record.timeline.filter(t => t.id !== itemId),
          lastMutation: 'replace' as const,
          meta: { ...record.meta, turnCount: Math.max(0, (record.meta.turnCount ?? 0) - 1) },
        },
      },
    }
  }),

  setLoadStatus: (sessionId, status) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, loadStatus: status },
      },
    }
  }),

  setDraftContent: (sessionId, content) => set(state => ({
    draftContent: { ...state.draftContent, [sessionId]: content },
  })),

  // M6 fix: mark WS as disconnected/connected (called by StreamPool)
  markSessionDisconnected: (sessionId) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, connectionStatus: 'disconnected' },
      },
    }
  }),

  markSessionConnected: (sessionId) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    return {
      sessions: {
        ...state.sessions,
        // D7: manual reconnect (ensureConnection) also clears staleDisconnect
        // so the banner dismisses even before the first new event arrives.
        [sessionId]: { ...record, connectionStatus: 'connected', staleDisconnect: false },
      },
    }
  }),

  markSessionStaleDisconnect: (sessionId, value) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    // Skip if value unchanged — avoids spurious re-renders of subscribed
    // components (StaleDisconnectBanner, ExecutionTimeline).
    if (record.staleDisconnect === value) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, staleDisconnect: value },
      },
    }
  }),

  // fix-event-seq-blackout D3: backend-authoritative status sync — used by
  // the staleness probe to self-heal a session stuck 'running' after a
  // missed terminal event. Drops the seq high-water mark (sessionStorage +
  // store) so the next run's reconnects start from a clean slate.
  syncSessionStatus: (sessionId, status) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    try { sessionStorage.removeItem(`stream_seq_${sessionId}`) } catch { /* ignore */ }
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...record,
          lastSeqId: 0,
          meta: { ...record.meta, status },
        },
      },
    }
  }),

  truncateTimeline: async (sessionId, backendMessageId, itemId, resolveBackendMessageId) => {
    const state = get()
    const record = state.sessions[sessionId]
    if (!record) return

    // ── D11 S2 CUTTING 进入 ─────────────────────────────────────────
    // 互斥：fetchOlder 检查此状态（E8）；MessageInputBar 禁用输入。
    const setTruncateState = (t: TruncateState) => set(s => {
      const rec = s.sessions[sessionId]
      if (!rec) return s
      return { sessions: { ...s.sessions, [sessionId]: { ...rec, truncateState: t } } }
    })
    setTruncateState('cutting')

    // ── D4.1 乐观截断：确认即删（<100ms UI 反馈）────────────────────
    // 权威数据由下方 refreshTail 收敛覆盖。被删项之前若紧邻 run_started
    // 边界行（该 run 内容已全部删除），回退切掉，防保留区出现孤立边界行。
    // 记录被删项与原下标：R1 原位 splice 回插用（非快照整替，避免吞并发页）。
    let cutIdx = -1
    let removedItems: TimelineItem[] = []
    if (itemId) {
      // fix-resend-from-here D2：cutIdx >= 0 即切（含窗口首位——翻页窗口起点/
      // 轮次起始卡），旧 idx > 0 守卫会让首位卡乐观切静默失效（"等后端才删"成因）。
      const idx = record.timeline.findIndex(t => t.id === itemId)
      if (idx >= 0) {
        let cut = idx
        while (cut > 0 && record.timeline[cut - 1].type === 'run_started') {
          cut -= 1
        }
        if (cut < record.timeline.length) {
          cutIdx = cut
          removedItems = record.timeline.slice(cut)
          const removed = removedItems
          set(s => {
            const rec = s.sessions[sessionId]
            if (!rec) return s
            return {
              sessions: {
                ...s.sessions,
                [sessionId]: {
                  ...rec,
                  timeline: rec.timeline.slice(0, cut),
                  lastMutation: 'replace' as const,
                  meta: { ...rec.meta, turnCount: Math.max(0, (rec.meta.turnCount ?? 0) - countUserItems(removed)) },
                },
              },
            }
          })
        }
      }
    }

    try {
      // ── fix-resend-from-here D1：backendMessageId 延迟解析 ──────────
      // 乐观切已完成、cutting 互斥生效（prependHistoryPage 被挡，cutIdx 不漂移），
      // 此处解析安全。本地无 id 且兜底解析返回空 → 视为本地失败走 R1 原位回插
      // （2.3），不再"alert 后什么都不做"。
      let messageId: string | null = backendMessageId ?? null
      if (!messageId) {
        messageId = (await resolveBackendMessageId?.()) ?? null
        if (!messageId) {
          throw new Error('无法定位消息（本地窗口无 id 且尾页匹配失败），请刷新页面后重试')
        }
      }

      // ── D4.2 + D13 truncate API：幂等键 + 有界重试状态机 ──────────
      // 网络失败 ≠ 服务端未生效：同 request_id 重试（2 次，500ms/2s 退避），
      // 期间收到回显自己 request_id 的 WS 广播 → 广播抢先确认（响应丢失情形）。
      // 404 = 外部已改（R2）；409/400 = 语义错误不重试；耗尽 → R1 回滚。
      const requestId = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
        ? crypto.randomUUID()
        : `trunc-${Date.now()}-${Math.random().toString(36).slice(2)}`
      const attempt: TruncateAttempt = { requestId, broadcastConfirmed: false }
      truncateAttempts.set(sessionId, attempt)

      for (let i = 0; ; i++) {
        if (attempt.broadcastConfirmed) break
        try {
          await truncateMessagesApi(sessionId, { message_id: messageId, request_id: requestId })
          break
        } catch (err) {
          const status = httpStatusOf(err)
          if (status === 404) throw err        // 外部已改 → R2 resync
          if (status === 409 || status === 400) throw err  // 语义错误，重试无意义
          if (i >= TRUNCATE_RETRY_BACKOFF_MS.length) throw err  // 耗尽 → R1
          await new Promise(resolve => setTimeout(resolve, TRUNCATE_RETRY_BACKOFF_MS[i]))
        }
      }

      // ── D11 S3 RECONNECTING ────────────────────────────────────────
      // 强制关闭旧 WS：其 lastSeqId 是截断前高水位，保持 OPEN 会让下一次
      // ensureConnection no-op，后端 WS dedup 静默丢弃新 run 全部事件。
      // seq 复位（fix-event-seq-blackout 模式）：sessionStorage 高水位必须
      // 手动清除（useStream 会取两者较大值）。
      setTruncateState('reconnecting')
      streamPool.closeConnection(sessionId)
      try { sessionStorage.removeItem(`stream_seq_${sessionId}`) } catch { /* ignore */ }
      // seq 水位清零：原路径经 hydrateSessionEvents 内置重置，refreshTail
      // 不动 seq 语义（断连自愈保持水位）→ 截断场景显式清零（store + 上面
      // sessionStorage 双通道，useStream 取两者较大值）。
      set(s => {
        const rec = s.sessions[sessionId]
        if (!rec) return s
        return { sessions: { ...s.sessions, [sessionId]: { ...rec, lastSeqId: 0 } } }
      })

      // ── D2/7.3 尾页同构收敛：统一走 refreshTail ────────────────────
      // paged 模式 = run 边界 splice 前缀保留（paging 不变量不动，I1）；
      // kill-switch = 全量重灌（与原 remaining_messages 路径同源）。
      // 广播抢先确认路径（响应丢失）同构收敛。
      await get().refreshTail(sessionId)

      // ── D4.3 restore + 重连后台化（fire-and-forget）────────────────
      // truncate 已清 record.agent，restore 重建 Agent。restore 幂等
      //（already_active 短路），且 sendMessage 有 404→restore 重试兜底，
      // 失败仅告警不阻断 UI。
      const email = useAuthStore.getState().user?.email
      if (email) {
        void (async () => {
          try {
            await restoreSessionApi(email, sessionId)
            streamPool.ensureConnection(sessionId, 0)
          } catch (err) {
            console.warn('[SessionsStore] background restore after truncate failed:', err)
          }
        })()
      }

      setTruncateState('idle')
    } catch (err) {
      console.warn('[SessionsStore] truncate failed, rolling back optimistic cut:', err)

      if (httpStatusOf(err) === 404) {
        // ── R2 RESYNC：外部已改（如双标签并发 truncate）→ 权威尾页收敛 ──
        try {
          await get().refreshTail(sessionId)
        } catch (rerunErr) {
          console.warn('[SessionsStore] R2 resync refreshTail failed:', rerunErr)
        }
      } else if (cutIdx >= 0 && removedItems.length > 0 && get().sessions[sessionId]?.truncateState !== 'idle') {
        // ── R1 ROLLBACK：被删项按原下标 splice 回插（非快照整替，避免吞
        // 并发产生的页/状态；E8）。若外部截断已收敛视图（truncateState 被置
        // idle）则跳过回插——外部视图即权威。E14 极端窗口（存储已截断但广播
        // 与响应均丢）由 D14 在下次拉取时裁决。──
        set(s => {
          const rec = s.sessions[sessionId]
          if (!rec) return s
          const timeline = cutIdx <= rec.timeline.length
            ? [...rec.timeline.slice(0, cutIdx), ...removedItems, ...rec.timeline.slice(cutIdx)]
            : [...rec.timeline, ...removedItems]
          return {
            sessions: {
              ...s.sessions,
              [sessionId]: {
                ...rec,
                timeline,
                lastMutation: 'replace' as const,
                meta: { ...rec.meta, turnCount: (rec.meta.turnCount ?? 0) + countUserItems(removedItems) },
              },
            },
          }
        })
      } else {
        // 无 itemId（无法原位回插）→ 尾页原语收敛（spec：不保留全量替换路径）
        try {
          await get().refreshTail(sessionId)
        } catch (rerunErr) {
          console.warn('[SessionsStore] fallback refreshTail failed:', rerunErr)
        }
      }
      setTruncateState('idle')
      throw err
    } finally {
      // attempt 注册期覆盖整个收敛/回滚：响应后到达的自家广播也能被识别，
      // 避免误判外部而重复收敛。
      truncateAttempts.delete(sessionId)
    }
  },

  // add-turn-regenerate D3/D4/D5/D6：轮次重答。与 truncateTimeline 的差异：
  // - 乐观切保留 U（cutIdx = uIdx + 1）；turnCount 不扣减（U 未删）；
  // - 成功后不走 refreshTail——会话已 RUNNING，尾页会合并 pending 半轮，与
  //   WS 重放双渲染；改 closeConnection + seq 清零 + ensureConnection(0)
  //   全量重放新 run 事件（run_started 重置水位，token 重建轮体）；
  // - 锚三级解析（D5，全链路 MUST NOT 内容匹配）：U 卡带位号 id 直传；
  //   U 是窗口最后一张 user 卡 → turn: last（零网络）；更早 live 轮 →
  //   乐观切后一次尾页解析（倒数第 N+1 个 user 消息，N = 该轮之后的
  //   user 卡数——窗口是 storage 后缀，后续轮必在窗口内）。
  regenerateTurn: async (sessionId, userItemId) => {
    const record = get().sessions[sessionId]
    if (!record) return

    const setTruncateState = (t: TruncateState) => set(s => {
      const rec = s.sessions[sessionId]
      if (!rec) return s
      return { sessions: { ...s.sessions, [sessionId]: { ...rec, truncateState: t } } }
    })

    const uIdx = record.timeline.findIndex(t => t.id === userItemId)
    if (uIdx < 0) return
    const uItem = record.timeline[uIdx] as UserMessageItem

    // ── D6 状态机：cutting 先行（输入禁用 + 翻页互斥 + R1 回插判据）──
    setTruncateState('cutting')

    // ── D3 乐观切：U 原位保留，删本轮体及其后所有轮 ──
    const cutIdx = uIdx + 1
    const removedItems: TimelineItem[] = record.timeline.slice(cutIdx)
    const laterUserCount = removedItems.filter(t => t.type === 'user_message').length
    if (removedItems.length > 0) {
      set(s => {
        const rec = s.sessions[sessionId]
        if (!rec) return s
        return {
          sessions: {
            ...s.sessions,
            [sessionId]: {
              ...rec,
              timeline: rec.timeline.slice(0, cutIdx),
              lastMutation: 'replace' as const,
            },
          },
        }
      })
    }

    // ── D5 锚解析（失败 → R1 原位回插，不走"什么都不做"老路）──
    let anchor: { message_id: string; turn_last?: undefined } | { message_id?: undefined; turn_last: true }
    if (uItem.backendMessageId) {
      anchor = { message_id: uItem.backendMessageId }
    } else if (laterUserCount === 0) {
      anchor = { turn_last: true }
    } else {
      try {
        const resp = await getSessionMessagesTailApi(sessionId)
        const users = resp.messages.filter(m => m.role === 'user')
        const target = users[users.length - 1 - laterUserCount]
        if (!target?.id) throw new Error('尾页未覆盖目标轮，请刷新页面后重试')
        anchor = { message_id: target.id }
      } catch (err) {
        console.warn('[SessionsStore] regenerate anchor resolution failed:', err)
        if (removedItems.length > 0) {
          set(s => {
            const rec = s.sessions[sessionId]
            if (!rec) return s
            return {
              sessions: {
                ...s.sessions,
                [sessionId]: {
                  ...rec,
                  timeline: [...rec.timeline.slice(0, cutIdx), ...removedItems, ...rec.timeline.slice(cutIdx)],
                  lastMutation: 'replace' as const,
                },
              },
            }
          })
        }
        setTruncateState('idle')
        throw err
      }
    }

    // ── D13 幂等协议（与 truncateTimeline 同构）──
    const requestId = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
      ? crypto.randomUUID()
      : `regen-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const attempt: TruncateAttempt = { requestId, broadcastConfirmed: false }
    truncateAttempts.set(sessionId, attempt)

    try {
      for (let i = 0; ; i++) {
        if (attempt.broadcastConfirmed) break
        try {
          await regenerateTurnApi(sessionId, { ...anchor, request_id: requestId })
          break
        } catch (err) {
          const status = httpStatusOf(err)
          if (status === 404) throw err        // 外部已改 → R2 resync
          if (status === 409 || status === 400 || status === 422) throw err  // 语义错误，重试无意义
          if (i >= TRUNCATE_RETRY_BACKOFF_MS.length) throw err  // 耗尽 → R1
          await new Promise(resolve => setTimeout(resolve, TRUNCATE_RETRY_BACKOFF_MS[i]))
        }
      }

      // ── 续跑已在服务端启动：WS 重连 fromSeq 0 全量重放新 run 事件 ──
      // 不 refreshTail（RUNNING 态尾页合并 pending 半轮 → 与重放双渲染）。
      setTruncateState('reconnecting')
      streamPool.closeConnection(sessionId)
      try { sessionStorage.removeItem(`stream_seq_${sessionId}`) } catch { /* ignore */ }
      set(s => {
        const rec = s.sessions[sessionId]
        if (!rec) return s
        return {
          sessions: {
            ...s.sessions,
            [sessionId]: {
              ...rec,
              lastSeqId: 0,
              // 后端已置 RUNNING；本地同步（重放 run_started 亦会置，此处提前
              // 封锁输入框，防重放窗口内误发送撞 409）
              meta: { ...rec.meta, status: 'running' },
            },
          },
        }
      })
      streamPool.ensureConnection(sessionId, 0)

      setTruncateState('idle')
    } catch (err) {
      console.warn('[SessionsStore] regenerate turn failed, rolling back optimistic cut:', err)

      if (httpStatusOf(err) === 404) {
        // ── R2 RESYNC：外部已改（并发截断/重答）→ 权威尾页收敛 ──
        try {
          await get().refreshTail(sessionId)
        } catch (rerunErr) {
          console.warn('[SessionsStore] R2 resync refreshTail failed:', rerunErr)
        }
      } else if (removedItems.length > 0 && get().sessions[sessionId]?.truncateState !== 'idle') {
        // ── R1 ROLLBACK：被删项按原下标 splice 回插；外部广播已收敛则跳过 ──
        set(s => {
          const rec = s.sessions[sessionId]
          if (!rec) return s
          const timeline = cutIdx <= rec.timeline.length
            ? [...rec.timeline.slice(0, cutIdx), ...removedItems, ...rec.timeline.slice(cutIdx)]
            : [...rec.timeline, ...removedItems]
          return {
            sessions: {
              ...s.sessions,
              [sessionId]: {
                ...rec,
                timeline,
                lastMutation: 'replace' as const,
              },
            },
          }
        })
      } else {
        // 空轮体（无可回插）→ 尾页原语收敛
        try {
          await get().refreshTail(sessionId)
        } catch (rerunErr) {
          console.warn('[SessionsStore] fallback refreshTail failed:', rerunErr)
        }
      }
      setTruncateState('idle')
      throw err
    } finally {
      truncateAttempts.delete(sessionId)
    }
  },

  // add-session-history-pagination D1/D7：向前翻页——拉更早历史页并 prepend。
  // 页 = run 原子块，消息带全量位号；锚漂移（resync）时转 refreshTail 收敛。
  prependHistoryPage: async (sessionId) => {
    const record = get().sessions[sessionId]
    const paging = record?.historyPaging
    if (!record || !paging || paging.fetchingOlder || !paging.hasMore) return false
    // D11/E8 互斥：truncate cutting/reconnecting 期间不翻页（并发 prepend 会
    // 干扰 R1 原位回插的下标语义）
    const tstate = record.truncateState
    if (tstate && tstate !== 'idle') return false

    set(state => {
      const rec = state.sessions[sessionId]
      if (!rec?.historyPaging) return state
      return {
        sessions: {
          ...state.sessions,
          [sessionId]: { ...rec, historyPaging: { ...rec.historyPaging, fetchingOlder: true } },
        },
      }
    })

    try {
      const startedAt = performance.now()
      const resp = await getSessionMessagesPageApi(sessionId, {
        before: paging.nextBefore,
        anchor_run_id: paging.anchorRunId ?? undefined,
      })
      // 8.2 翻页耗时观测（>300ms 告警，便于长会话基线对比）
      const elapsed = performance.now() - startedAt
      if (elapsed > 300) {
        console.warn(`[SessionsStore] prependHistoryPage slow: ${Math.round(elapsed)}ms (${resp.messages.length} msgs)`)
      }

      if (resp.resync) {
        // D3 ABA：窗口前缀已漂移（并发 truncate）→ 权威尾页重拉整窗收敛
        console.warn('[SessionsStore] page anchor mismatch — resyncing to tail')
        await get().refreshTail(sessionId)
        return true
      }

      const events = hydrateMessages(resp.messages, record.meta.agent_name)
      const pageItems = eventsToTimeline(events)

      set(state => {
        const rec = state.sessions[sessionId]
        if (!rec) return state
        const meta = resp.page_meta
        // 去重：页消息位号必然小于窗口头（游标排他），防御性过滤已知 id
        const knownSeqs = new Set(
          rec.timeline
            .map(t => parseBackendSeq((t as { backendMessageId?: string }).backendMessageId))
            .filter((s): s is number => s != null),
        )
        const deduped = pageItems.filter(t => {
          const seq = parseBackendSeq((t as { backendMessageId?: string }).backendMessageId)
          return seq == null || !knownSeqs.has(seq)
        })
        return {
          sessions: {
            ...state.sessions,
            [sessionId]: {
              ...rec,
              timeline: [...deduped, ...rec.timeline],
              lastMutation: 'prepend' as const,
              meta: { ...rec.meta, turnCount: (rec.meta.turnCount ?? 0) + countUserItems(deduped) },
              historyPaging: {
                fetchingOlder: false,
                hasMore: meta?.has_more ?? false,
                nextBefore: meta?.next_before ?? 0,
                anchorRunId: meta?.anchor ?? null,
                runsVersion: meta ? { total_runs: meta.total_runs, total_messages: meta.total_messages } : rec.historyPaging?.runsVersion ?? null,
              },
            },
          },
        }
      })
      return true
    } catch (err) {
      // D7：失败不砸窗口，游标不丢（fetchingOlder 复位即可重试）
      console.warn('[SessionsStore] prependHistoryPage failed:', err)
      set(state => {
        const rec = state.sessions[sessionId]
        if (!rec?.historyPaging) return state
        return {
          sessions: {
            ...state.sessions,
            [sessionId]: { ...rec, historyPaging: { ...rec.historyPaging, fetchingOlder: false } },
          },
        }
      })
      return false
    }
  },

  // add-session-history-pagination D2：尾页回灌原语。
  // paged 模式：按位号区间 splice 替换窗口尾部（前缀保留，live 项视为尾部）；
  // kill-switch 关闭：保留旧全量路径（GET /messages 整条重灌）。
  // 单飞合并：在途时后续触发仅记「待重跑」。失败上抛（调用方按场景处理）。
  refreshTail: async (sessionId) => {
    const record = get().sessions[sessionId]
    if (!record) return

    if (refreshTailInFlight.has(sessionId)) {
      refreshTailRerunRequested.add(sessionId)
      return
    }
    refreshTailInFlight.add(sessionId)

    try {
      // D10 kill-switch：全量模式保留旧路径（与既有全量重灌行为一致）
      if (!isHistoryPaginationEnabled()) {
        const res = await getSessionMessagesApi(sessionId)
        get().hydrateSessionEvents(sessionId, hydrateMessages(res.messages, record.meta.agent_name))
        return
      }

      const resp = await getSessionMessagesTailApi(sessionId)
      const events = hydrateMessages(resp.messages, record.meta.agent_name)
      const newTail = eventsToTimeline(events)
      const meta = resp.page_meta

      set(state => {
        const rec = state.sessions[sessionId]
        if (!rec) return state

        let timeline: TimelineItem[]
        const paging = rec.historyPaging
        const startSeq = parseBackendSeq(resp.messages[0]?.id)
        if (paging && startSeq != null) {
          // run 边界 splice：定位窗口内第一个「位号 >= 尾页起点」的项
          //（live 项无位号 → 视为尾部，必被替换）；找不到（异常窗口）→ 整替兜底
          const cutIdx = rec.timeline.findIndex(t => {
            const seq = parseBackendSeq((t as { backendMessageId?: string }).backendMessageId)
            return seq == null ? true : seq >= startSeq
          })
          timeline = cutIdx === -1 ? newTail : [...rec.timeline.slice(0, cutIdx), ...newTail]
        } else {
          timeline = newTail
        }

        const baseHistoryPaging = paging ?? {
          hasMore: false,
          nextBefore: 0,
          anchorRunId: null,
          runsVersion: null,
          fetchingOlder: false,
        }
        return {
          sessions: {
            ...state.sessions,
            [sessionId]: {
              ...rec,
              timeline,
              loadStatus: 'ready',
              lastMutation: 'replace' as const,
              meta: { ...rec.meta, turnCount: countUserItems(timeline) },
              historyPaging: {
                ...baseHistoryPaging,
                hasMore: meta?.has_more ?? false,
                nextBefore: meta?.next_before ?? 0,
                anchorRunId: meta?.anchor ?? null,
                runsVersion: meta ? { total_runs: meta.total_runs, total_messages: meta.total_messages } : baseHistoryPaging.runsVersion,
              },
            },
          },
        }
      })
    } finally {
      refreshTailInFlight.delete(sessionId)
      // 单飞合并：在途期间又有触发（如连续外部截断）→ 补跑一次收敛到最终态
      if (refreshTailRerunRequested.delete(sessionId)) {
        void get().refreshTail(sessionId)
      }
    }
  },

  // D12 跨标签截断收敛：messages_truncated 广播分派（streamPool 前置拦截）。
  // 存量修复: messages_truncated 跨标签收敛协议。
  handleMessagesTruncated: async (sessionId, payload) => {
    const record = get().sessions[sessionId]
    if (!record) return

    // ── D13 自/外部判别：request_id 匹配本会话在途 truncate ──
    const attempt = truncateAttempts.get(sessionId)
    if (payload.request_id && attempt && attempt.requestId === payload.request_id) {
      // 广播抢先确认（时序合法：服务端 push_ws_event 先于响应写出）。
      // 发起方的收敛由 truncateTimeline 重试状态机完成，此处不重复拉取。
      attempt.broadcastConfirmed = true
      return
    }

    // ── 外部截断（另一标签触发）收敛 ──
    // 公共：seq 水位清零（防御性；fix-event-seq-blackout D4 终态后本已为 0）。
    try { sessionStorage.removeItem(`stream_seq_${sessionId}`) } catch { /* ignore */ }

    // D12.5 状态机分派：confirming（modal 开）→ 置 idle 触发 bubble 关闭 modal
    // + 提示「消息已被其它窗口修改」；cutting → 置 idle 阻止本会话 R1 回插
    // 覆盖外部已收敛视图（R1 分支检查 truncateState）。
    if (record.truncateState === 'confirming' || record.truncateState === 'cutting') {
      set(state => {
        const rec = state.sessions[sessionId]
        if (!rec) return state
        return {
          sessions: {
            ...state.sessions,
            [sessionId]: { ...rec, truncateState: 'idle' },
          },
        }
      })
    }

    // meta.status 同步：truncate 后端必置 IDLE（409 拒绝运行中 + 5.2 终态→IDLE）。
    if (record.meta.status !== 'idle') {
      set(state => {
        const rec = state.sessions[sessionId]
        if (!rec) return state
        return {
          sessions: {
            ...state.sessions,
            [sessionId]: { ...rec, meta: { ...rec.meta, status: 'idle' } },
          },
        }
      })
    }

    // 收敛分模式：paged = refreshTail（run 边界 splice 前缀保留）；
    // 全量模式 = 拉权威 messages 整条重灌（截断后存储即权威）。
    if (record.historyPaging) {
      await get().refreshTail(sessionId)
      return
    }
    try {
      const res = await getSessionMessagesApi(sessionId)
      get().hydrateSessionEvents(sessionId, hydrateMessages(res.messages, record.meta.agent_name))
    } catch (err) {
      console.warn('[SessionsStore] external truncate convergence re-fetch failed:', err)
    }
  },

  // D11 truncate 状态机驱动：bubble 打开 modal 时置 confirming，取消置 idle；
  // 外部截断广播到达时 handleMessagesTruncated 置 idle（bubble 订阅后关 modal）。
  setTruncateState: (sessionId, tstate) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    if ((record.truncateState ?? 'idle') === tstate) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, truncateState: tstate },
      },
    }
  }),

  appendApprovalCards: (sessionId, approvals) => set(state => {
    const record = state.sessions[sessionId]
    if (!record || approvals.length === 0) return state
    const existing = new Set(
      record.timeline
        .filter((it): it is ApprovalPendingItem => it.type === 'approval_pending' && it.approval_id !== null)
        .map(it => it.approval_id),
    )
    const newItems = approvals.filter(a => !existing.has(a.id)).map(toApprovalCardItem)
    if (newItems.length === 0) return state
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: { ...record, timeline: [...record.timeline, ...newItems], lastMutation: 'append' as const },
      },
    }
  }),

  markClarifyAnswered: (sessionId, toolCallId, answer) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    const timeline = record.timeline.map(it =>
      it.type === 'clarify_request' && it.tool_call_id === toolCallId && it.answer === null
        ? { ...it, answer }
        : it,
    )
    return { sessions: { ...state.sessions, [sessionId]: { ...record, timeline } } }
  }),

  hydrateSessionEvents: (sessionId, events) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    const timeline = eventsToTimeline(events)
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...record,
          timeline,
          loadStatus: 'ready',
          lastSeqId: 0,
          // 'append'：首载/回滚/收敛时若用户在底部则跟随（保持既有 UX）；
          // 翻到顶部阅读旧历史时 isAutoScroll=false 不会被拽回
          lastMutation: 'append' as const,
          meta: { ...record.meta, turnCount: countUserItems(timeline) },
        },
      },
    }
  }),

  // add-session-history-pagination D2：尾页首载 hydrate + historyPaging 初始化。
  hydrateTailPage: (sessionId, events, meta) => set(state => {
    const record = state.sessions[sessionId]
    if (!record) return state
    const timeline = eventsToTimeline(events)
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...record,
          timeline,
          loadStatus: 'ready',
          lastSeqId: 0,
          lastMutation: 'append' as const,
          meta: { ...record.meta, turnCount: countUserItems(timeline) },
          historyPaging: {
            hasMore: meta?.has_more ?? false,
            nextBefore: meta?.next_before ?? 0,
            anchorRunId: meta?.anchor ?? null,
            runsVersion: meta
              ? { total_runs: meta.total_runs, total_messages: meta.total_messages }
              : null,
            fetchingOlder: false,
          },
        },
      },
    }
  }),

  // add-context-usage-visibility: 静默刷新上下文占用快照。
  // 失败保持旧值不弹错（spec: 端点失败不阻塞 —— 会话不存在/网络错误均静默）。
  // force=true 旁路后端缓存强制重算（弹窗手动刷新按钮）。
  refreshContextUsage: async (sessionId, force = false) => {
    try {
      const breakdown = await getContextUsageApi(sessionId, force)
      set(state => {
        const prev = state.sessionUsage[sessionId]
        return {
          sessionUsage: {
            ...state.sessionUsage,
            [sessionId]: { byAgent: prev?.byAgent ?? {}, breakdown, compressed: prev?.compressed ?? false },
          },
        }
      })
    } catch (err) {
      // 404 = 新会话尚未落库（首挂载即拉）/ 已删会话——设计内降级，静默
      // （harden-session-history-paging）；其他错误保留告警。
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status !== 404) {
        console.warn('[SessionsStore] refreshContextUsage failed (keeping previous value):', err)
      }
    }
  },
}))
