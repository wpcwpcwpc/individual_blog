import apiClient from './client'
import type {
  ContextUsageResponse,
  CreateSessionRequest,
  CreateSessionResponse,
  SendMessageRequest,
  SendMessageResponse,
  SessionStatusResponse,
  LastSeqResponse,
  ReviewRequest,
  ReviewResponse,
  UserSessionsResponse,
  SessionMessagesResponse,
  AbortResponse,
  ResumeRequest,
  ResumeResponse,
  TruncateRequest,
  TruncateResponse,
  RegenerateTurnRequest,
  RegenerateTurnResponse,
  UpdateSessionRequest,
  UpdateSessionVisibilityRequest,
} from '@/types/api'

export const createSession = (data: CreateSessionRequest) =>
  apiClient.post<CreateSessionResponse>('/sessions', data).then(r => r.data)

export const sendMessage = (sessionId: string, data: SendMessageRequest) =>
  apiClient.post<SendMessageResponse>(`/sessions/${sessionId}/messages`, data).then(r => r.data)
export const getSessionStatus = (sessionId: string) =>
  apiClient.get<SessionStatusResponse>(`/sessions/${sessionId}/status`).then(r => r.data)

/** GET /sessions/{id}/stream/last-seq — lightweight probe for the staleness detector. */
export const getSessionLastSeq = (sessionId: string) =>
  apiClient.get<LastSeqResponse>(`/sessions/${sessionId}/stream/last-seq`).then(r => r.data)

/**
 * GET /sessions/{id}/context-usage — 上下文窗口分类占用 + 会话累计 token 消耗
 * （add-context-usage-visibility）。active 会话含完整拆解与 segments；
 * inactive 降级（agent 侧字段 null）。force=true 旁路后端缓存强制重算
 * （弹窗手动刷新按钮）。
 */
export const getContextUsage = (sessionId: string, force = false) =>
  apiClient
    .get<ContextUsageResponse>(`/sessions/${sessionId}/context-usage`, {
      params: force ? { force: true } : undefined,
    })
    .then(r => r.data)

export const reviewInterrupt = (sessionId: string, data: ReviewRequest) =>
  apiClient.post<ReviewResponse>(`/sessions/${sessionId}/review`, data).then(r => r.data)

export const deleteSession = (email: string, sessionId: string) =>
  apiClient.delete(`/users/${encodeURIComponent(email)}/sessions/${sessionId}`).then(r => r.data)

/**
 * 构造会话事件流 WebSocket 的绝对 URL。
 *
 * WebSocket 构造器不接受相对路径（`new WebSocket('/api/...')` 直接抛
 * SyntaxError），必须显式补全协议与 host：
 * - 绝对 http(s) base（dev 模式 .env.local 注入）→ 转 ws(s) 直连；
 * - 相对 base（build 产物烤入的 '/api'）→ 按页面 origin 补全为 ws(s)://host。
 */
export const getWSStreamUrl = (sessionId: string, lastEventId?: number) => {
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  // Convert http(s) to ws(s)
  let wsBase = base.replace(/^http/, 'ws')
  if (!/^wss?:\/\//.test(wsBase)) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    wsBase = `${proto}//${window.location.host}${wsBase}`
  }
  const url = `${wsBase}/sessions/${sessionId}/stream/ws`
  return lastEventId != null ? `${url}?last_event_id=${lastEventId}` : url
}

/** GET /users/{email}/sessions — returns historical session meta list */
export const getUserSessions = (email: string) =>
  apiClient.get<UserSessionsResponse>(`/users/${encodeURIComponent(email)}/sessions`).then(r => r.data)

/** POST /users/{email}/sessions/{id}/restore — idempotently rebuild Agent in memory */
export const restoreSession = (email: string, sessionId: string) =>
  apiClient.post(`/users/${encodeURIComponent(email)}/sessions/${sessionId}/restore`).then(r => r.data)

/** GET /sessions/{id}/messages — returns full Agno message history from SQLite */
export const getSessionMessages = (sessionId: string) =>
  apiClient.get<SessionMessagesResponse>(`/sessions/${sessionId}/messages`).then(r => r.data)

/**
 * GET /sessions/{id}/messages — 向前翻页（add-session-history-pagination D1/D3）。
 * 任一分页参数存在即分页模式；anchor_run_id 为窗口最老 run 的锚标识，
 * 后端校验失败返回 resync=true。
 */
export const getSessionMessagesPage = (
  sessionId: string,
  params: { before: number; limit?: number; anchor_run_id?: string },
) =>
  apiClient
    .get<SessionMessagesResponse>(`/sessions/${sessionId}/messages`, { params })
    .then(r => r.data)

/**
 * GET /sessions/{id}/messages/tail — 尾页原语（D2）：最近 N runs + 进行中
 * run 的 pending 事件合并 + page_meta。五路回灌统一走此端点。
 */
export const getSessionMessagesTail = (sessionId: string, limit?: number) =>
  apiClient
    .get<SessionMessagesResponse>(`/sessions/${sessionId}/messages/tail`, {
      params: limit != null ? { limit } : undefined,
    })
    .then(r => r.data)

/** POST /sessions/{id}/abort — immediately interrupt a running session */
export const abortSession = (sessionId: string) =>
  apiClient.post<AbortResponse>(`/sessions/${sessionId}/abort`).then(r => r.data)

/** POST /sessions/{id}/resume — resume an interrupted session or submit review result */
export const resumeSession = (sessionId: string, data: ResumeRequest) =>
  apiClient.post<ResumeResponse>(`/sessions/${sessionId}/resume`, data).then(r => r.data)

/** POST /sessions/{id}/messages/truncate — truncate messages from a given point (inclusive) */
export const truncateMessages = (sessionId: string, data: TruncateRequest) =>
  apiClient.post<TruncateResponse>(`/sessions/${sessionId}/messages/truncate`, data).then(r => r.data)

/**
 * POST /sessions/{id}/turns/regenerate — 轮次重答（add-turn-regenerate）：截断保留
 * 至该轮用户消息，服务端 acontinue_run 续跑（不追加用户消息），新回复经 WS 流式
 * 渲染。锚：message_id（位号）/ turn_last，MUST NOT 内容匹配。
 */
export const regenerateTurn = (sessionId: string, data: RegenerateTurnRequest) =>
  apiClient.post<RegenerateTurnResponse>(`/sessions/${sessionId}/turns/regenerate`, data).then(r => r.data)

/** PATCH /users/{email}/sessions/{sessionId} — update session title */
export const updateSessionTitle = (email: string, sessionId: string, title: string) =>
  apiClient.patch(`/users/${encodeURIComponent(email)}/sessions/${sessionId}`, { title } as UpdateSessionRequest).then(r => r.data)

/** PUT /users/{email}/sessions/{sessionId}/visibility — hide/unhide a session */
export const updateSessionVisibility = (email: string, sessionId: string, hidden: boolean) =>
  apiClient.put(`/users/${encodeURIComponent(email)}/sessions/${sessionId}/visibility`, { hidden } as UpdateSessionVisibilityRequest).then(r => r.data)
