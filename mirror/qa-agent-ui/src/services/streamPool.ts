/**
 * StreamPoolManager — global singleton managing WS connections
 * (one per active session), with events dispatched to the Zustand sessions store.
 */
import { getWSStreamUrl, getSessionLastSeq } from '@/api/sessions'
import { useSessionsStore } from '@/store/sessions'
import type { NormalizedEvent, InterruptRequestEvent, ReviewRequestedEvent, MessagesTruncatedEvent } from '@/types/events'
import type { InterruptPayload, SessionStatus } from '@/types/api'

// ── Types ────────────────────────────────────────────────────────

interface StreamConnection {
  conn: WebSocket
  lastSeqId: number
  reconnectTimer?: ReturnType<typeof setTimeout>
  heartbeatTimer?: ReturnType<typeof setTimeout>
  // Staleness detector timer — fires WS_STALENESS_TIMEOUT_MS after the last
  // non-heartbeat event. Distinct from heartbeatTimer (which any onmessage
  // resets); this only resets on user-visible content.
  stalenessTimer?: ReturnType<typeof setTimeout>
  reconnectAttempt: number
}

// ── Constants ────────────────────────────────────────────────────

const WS_CONNECT_TIMEOUT_MS = 5_000
const WS_HEARTBEAT_TIMEOUT_MS = 35_000
const WS_BACKOFF_SCHEDULE = [1000, 2000, 4000, 8000, 16000, 30000]
// M6 fix: cap reconnect attempts to avoid infinite retry storms
const MAX_RECONNECT_ATTEMPTS = 20
// M7 fix: cap concurrent WS connections (Chrome same-origin WS limit ~6)
const MAX_CONCURRENT_CONNECTIONS = 6
// Staleness detector: if no user-visible content (non-heartbeat) event arrives
// within this window while session is running, probe backend seq_id to
// distinguish "LLM thinking" from "WS silently dropped". See design.md D2.
const WS_STALENESS_TIMEOUT_MS = 60_000

// ── Helpers ──────────────────────────────────────────────────────

/** Returns true if the session is in a state where we should attempt reconnect. */
function isSessionReconnectable(sessionId: string): boolean {
  const record = useSessionsStore.getState().sessions[sessionId]
  if (!record) return false
  const status = record.meta.status
  return status === 'running' || status === 'interrupt_pending'
}

// ── StreamPoolManager ────────────────────────────────────────────

class StreamPoolManager {
  private connections = new Map<string, StreamConnection>()

  // ── Public API ─────────────────────────────────────────────

  /** Idempotent: ensures a live WS connection exists for the given session. */
  ensureConnection(sessionId: string, lastSeqId?: number): void {
    // 删除后拨号守卫：store 无记录（已删除）的会话绝不 (重)拨 — 否则后端
    // 4004 拒绝握手，产生「[StreamPool] WS error」噪声 + 无意义重连风暴。
    // 覆盖删除竞态路径：truncate 背景恢复重连、resume、stale-banner 手动重连等。
    if (!useSessionsStore.getState().sessions[sessionId]) {
      console.warn(`[StreamPool] skip ensureConnection for unknown session ${sessionId} (not in store)`)
      return
    }

    const existing = this.connections.get(sessionId)
    if (existing) {
      const { conn } = existing
      if (conn.readyState === WebSocket.OPEN || conn.readyState === WebSocket.CONNECTING) {
        return
      }
      // Stale connection — clean up before reconnecting
      this.closeConnection(sessionId)
    }

    // M6 fix: manual ensureConnection resets the reconnect counter
    // (user-initiated reconnect should not inherit prior failure state).
    useSessionsStore.getState().markSessionConnected(sessionId)

    // M7 fix: enforce concurrent connection cap before opening a new one
    this.evictIfOverCapacity(sessionId)

    this.connectWS(sessionId, lastSeqId ?? 0)
  }

  // M7 fix: LRU eviction when pool is full
  private evictIfOverCapacity(incomingSessionId: string): void {
    if (this.connections.size < MAX_CONCURRENT_CONNECTIONS) return

    const activeId = useSessionsStore.getState().activeSessionId
    // Candidate = not the active session, not the incoming one, smallest lastSeqId
    let victimId: string | null = null
    let victimSeqId = Number.POSITIVE_INFINITY
    for (const [sid, conn] of this.connections) {
      if (sid === activeId || sid === incomingSessionId) continue
      if (conn.lastSeqId < victimSeqId) {
        victimSeqId = conn.lastSeqId
        victimId = sid
      }
    }
    if (victimId) {
      console.warn(`[StreamPool] Pool full (${this.connections.size}/${MAX_CONCURRENT_CONNECTIONS}), evicting LRU session ${victimId}`)
      this.closeConnection(victimId)
    } else {
      console.warn(`[StreamPool] Pool full but no eligible victim — all ${this.connections.size} connections are active`)
    }
  }

  // ── WebSocket ──────────────────────────────────────────────

  private connectWS(sessionId: string, lastSeqId: number): void {
    const url = getWSStreamUrl(sessionId, lastSeqId || undefined)
    const ws = new WebSocket(url)

    const connection: StreamConnection = {
      conn: ws,
      lastSeqId,
      reconnectAttempt: 0,
    }
    this.connections.set(sessionId, connection)

    // 5s first-connect timeout
    const connectTimeout = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        console.warn(`[StreamPool] WS connect timeout for ${sessionId}`)
        ws.close()
        // fix-event-seq-blackout D5: identity check — the pool entry may
        // already point to a newer connection; only delete our own entry.
        if (this.connections.get(sessionId)?.conn === ws) {
          this.connections.delete(sessionId)
        }
      }
    }, WS_CONNECT_TIMEOUT_MS)

    ws.onopen = () => {
      clearTimeout(connectTimeout)
      connection.reconnectAttempt = 0
      this.resetHeartbeatTimer(sessionId)
      // D2: start staleness timer on open so we probe even if zero events
      // ever arrive (covers session running but WS silently dead from start).
      this.resetStalenessTimer(sessionId)
    }

    ws.onmessage = (msgEvent) => {
      this.resetHeartbeatTimer(sessionId)
      try {
        const data = JSON.parse(msgEvent.data as string) as NormalizedEvent
        if (data.seq_id != null) {
          connection.lastSeqId = data.seq_id
        }
        this.handleEvent(sessionId, data)
      } catch (err) {
        console.error('[StreamPool] WS parse error:', err)
      }
    }

    ws.onclose = (closeEvent) => {
      clearTimeout(connectTimeout)
      this.clearTimers(connection)

      // fix-event-seq-blackout D5: identity check. The manual-reconnect flow
      // (closeConnection → ensureConnection) replaces the pool entry with a
      // new WebSocket before this async onclose fires; deleting by key here
      // would evict the NEW connection and orphan it (no heartbeat/staleness
      // timers). Only act if the pool still points at this instance.
      if (this.connections.get(sessionId)?.conn !== ws) return

      if (closeEvent.code === 1000) {
        // Normal close (terminal event) — just remove from pool
        this.connections.delete(sessionId)
        return
      }

      // Abnormal close — attempt reconnect if session is still active
      this.connections.delete(sessionId)
      if (isSessionReconnectable(sessionId)) {
        const attempt = connection.reconnectAttempt
        // M6 fix: cap reconnect attempts
        if (attempt >= MAX_RECONNECT_ATTEMPTS) {
          console.error(`[StreamPool] Giving up reconnect for ${sessionId} after ${attempt} attempts`)
          useSessionsStore.getState().markSessionDisconnected(sessionId)
          return
        }
        const delay = WS_BACKOFF_SCHEDULE[Math.min(attempt, WS_BACKOFF_SCHEDULE.length - 1)]
        console.warn(`[StreamPool] WS closed abnormally (code=${closeEvent.code}) for ${sessionId}, reconnecting in ${delay}ms (attempt ${attempt + 1}/${MAX_RECONNECT_ATTEMPTS})`)

        const timer = setTimeout(() => {
          if (isSessionReconnectable(sessionId)) {
            this.connectWS(sessionId, connection.lastSeqId)
            // Carry forward attempt counter
            const newConn = this.connections.get(sessionId)
            if (newConn) {
              newConn.reconnectAttempt = attempt + 1
            }
          }
        }, delay)

        // Store timer reference so it can be cleared if connection is manually closed
        const placeholder: StreamConnection = {
          ...connection,
          conn: ws, // already closed, but keeps the shape
          reconnectTimer: timer,
          reconnectAttempt: attempt + 1,
        }
        this.connections.set(sessionId, placeholder)
      }
    }

    ws.onerror = () => {
      // Manual close (delete/evict/reconnect flow) already dropped the pool
      // entry; Chromium fires a spurious error event when close() hits a
      // CONNECTING socket. Identity check mirrors onclose (D5) — only
      // report errors for sockets the pool still owns.
      if (this.connections.get(sessionId)?.conn !== ws) return
      // onerror is always followed by onclose; no extra action needed
      console.warn(`[StreamPool] WS error for ${sessionId}`)
    }
  }

  // Heartbeat timer
  private resetHeartbeatTimer(sessionId: string): void {
    const connection = this.connections.get(sessionId)
    if (!connection) return

    if (connection.heartbeatTimer) {
      clearTimeout(connection.heartbeatTimer)
    }

    connection.heartbeatTimer = setTimeout(() => {
      console.warn(`[StreamPool] WS heartbeat timeout for ${sessionId}`)
      const conn = connection.conn
      if (conn.readyState === WebSocket.OPEN) {
        conn.close()
      }
      // onclose handler will schedule reconnect
    }, WS_HEARTBEAT_TIMEOUT_MS)
  }

  // ── Staleness detector ──────────────────────────────────────
  // D2: distinct from heartbeat timer. Only resets on user-visible
  // content events (non-heartbeat). On fire, probes backend seq_id to
  // distinguish "LLM thinking" from "WS silently dropped".

  private resetStalenessTimer(sessionId: string): void {
    const connection = this.connections.get(sessionId)
    if (!connection) return

    // Skip if session is no longer running — staleness detection only
    // applies to active runs. Reads store directly (singleton-safe).
    const record = useSessionsStore.getState().sessions[sessionId]
    const status = record?.meta.status
    if (status !== 'running' && status !== 'interrupt_pending') return

    if (connection.stalenessTimer) {
      clearTimeout(connection.stalenessTimer)
    }

    connection.stalenessTimer = setTimeout(() => {
      void this.probeConnection(sessionId)
    }, WS_STALENESS_TIMEOUT_MS)
  }

  private clearStalenessTimer(connection: StreamConnection): void {
    if (connection.stalenessTimer) {
      clearTimeout(connection.stalenessTimer)
      connection.stalenessTimer = undefined
    }
  }

  private async probeConnection(sessionId: string): Promise<void> {
    const connection = this.connections.get(sessionId)
    if (!connection) return

    // Re-check running state at fire time — session may have transitioned
    // since the timer was scheduled.
    const record = useSessionsStore.getState().sessions[sessionId]
    const status = record?.meta.status
    if (status !== 'running' && status !== 'interrupt_pending') return

    const localSeq = connection.lastSeqId
    let backendSeq: number | null = null
    let backendStatus: SessionStatus | null = null
    try {
      const resp = await getSessionLastSeq(sessionId)
      backendSeq = resp.last_seq_id
      backendStatus = resp.status
    } catch (err) {
      // HTTP failure (network / 5xx / 404) → confirm disconnect
      console.warn(`[StreamPool] staleness probe HTTP failed for ${sessionId}:`, err)
      useSessionsStore.getState().markSessionStaleDisconnect(sessionId, true)
      this.clearStalenessTimer(connection)
      return
    }

    // fix-event-seq-blackout D3: consume the backend-authoritative status
    // before comparing seq. If the backend already finished the run (or the
    // session left memory), the local 'running' state is stale (missed
    // terminal event) — sync status, clear the seq high-water mark and drop
    // the connection instead of showing the disconnect banner.
    if (
      backendStatus === 'completed' || backendStatus === 'failed'
      || backendStatus === 'aborted' || backendStatus === 'inactive'
    ) {
      console.warn(
        `[StreamPool] staleness probe: backend status=${backendStatus} for ${sessionId}, self-healing stale local running state`,
      )
      // Run 已终态但本地时间线缺尾段（WS 掉线重连落在 clear_persisted 之后、
      // replay 为空）→ refreshTail 权威回灌时间线（add-session-history-pagination
      // D2：paged 模式 run 边界 splice 前缀保留；kill-switch 全量重灌），
      // 否则冻结在最后一个 delta（如「思考中 · N 字」永不收尾）。
      try {
        await useSessionsStore.getState().refreshTail(sessionId)
      } catch (err) {
        // 回灌失败不阻断状态同步（保持原自愈行为）
        console.warn(`[StreamPool] self-heal refreshTail failed for ${sessionId}:`, err)
      }
      useSessionsStore.getState().markSessionStaleDisconnect(sessionId, false)
      useSessionsStore.getState().syncSessionStatus(sessionId, backendStatus)
      this.closeConnection(sessionId)
      return
    }

    if (backendSeq === localSeq) {
      // Backend has no newer events — LLM is thinking. Reset timer and
      // keep waiting. No banner.
      this.resetStalenessTimer(sessionId)
      return
    }

    // backendSeq !== localSeq (ahead OR behind). Ahead = missed events;
    // behind = backend lost memory (inactive). Both → confirm disconnect.
    console.warn(
      `[StreamPool] staleness probe confirmed disconnect for ${sessionId}: backend seq=${backendSeq} local seq=${localSeq}`,
    )
    useSessionsStore.getState().markSessionStaleDisconnect(sessionId, true)
    this.clearStalenessTimer(connection)
  }

  // ── Event handling ─────────────────────────────────────────

  private handleEvent(sessionId: string, data: NormalizedEvent): void {
    const store = useSessionsStore

    // heartbeat — don't dispatch to store (WS heartbeat timer reset
    // is already handled in onmessage)
    if (data.event_type === 'heartbeat') return

    // session_idle — dispatch, then close/remove connection
    if (data.event_type === 'session_idle') {
      store.getState().appendEvent(sessionId, data)
      this.closeConnection(sessionId)
      return
    }

    // messages_truncated — D12 跨标签截断收敛：与 session_idle 同级前置拦截，
    // 不进 appendEvent switch（非 timeline 事件，且与 seq 去重守卫解耦）。
    // 存量修复: messages_truncated 跨标签收敛协议。
    if (data.event_type === 'messages_truncated') {
      void store.getState().handleMessagesTruncated(sessionId, data as MessagesTruncatedEvent)
      return
    }

    // Dispatch all other events to the store timeline
    store.getState().appendEvent(sessionId, data)

    // D2 + D7: non-heartbeat content event arrived — restart staleness
    // timer, and auto-clear staleDisconnect (dismiss banner) since the WS
    // is demonstrably delivering content again.
    this.resetStalenessTimer(sessionId)
    const record = store.getState().sessions[sessionId]
    if (record?.staleDisconnect) {
      store.getState().markSessionStaleDisconnect(sessionId, false)
    }

    // interrupt_timeout — explicitly clear interrupt via store action
    if (data.event_type === 'interrupt_timeout') {
      store.getState().setInterrupt(sessionId, null)
    }

    // interrupt_request / review_requested — also set interrupt payload + unread badge
    if (data.event_type === 'interrupt_request' || data.event_type === 'review_requested') {
      const e = data as InterruptRequestEvent | ReviewRequestedEvent
      const payload: InterruptPayload = {
        interrupt_id: e.interrupt_id,
        interrupt_type: e.interrupt_type,
        payload: e.payload,
        created_at: e.timestamp,
        age_seconds: 0,
        actions: e.actions,
      }
      store.getState().setInterrupt(sessionId, payload)

      // Mark unread if this is not the active session
      const { activeSessionId, sessions } = store.getState()
      if (sessionId !== activeSessionId && sessions[sessionId]) {
        store.setState((state) => {
          const record = state.sessions[sessionId]
          if (!record) return state
          // L10 fix: avoid unnecessary re-render if already marked unread
          if (record.hasUnreadInterrupt) return state
          return {
            sessions: {
              ...state.sessions,
              [sessionId]: { ...record, hasUnreadInterrupt: true },
            },
          }
        })
      }
    }
  }

  // ── Connection management ──────────────────────────────────

  /** Close and remove a single connection. */
  closeConnection(sessionId: string): void {
    const connection = this.connections.get(sessionId)
    if (!connection) return

    this.clearTimers(connection)

    const { conn } = connection
    if (conn.readyState === WebSocket.OPEN || conn.readyState === WebSocket.CONNECTING) {
      conn.close(1000)
    }

    this.connections.delete(sessionId)
  }

  /** Close all connections. */
  closeAll(): void {
    for (const sessionId of [...this.connections.keys()]) {
      this.closeConnection(sessionId)
    }
  }

  /** Check if a connection exists for the given session. */
  isConnected(sessionId: string): boolean {
    return this.connections.has(sessionId)
  }

  /** Get the total number of active connections. */
  getConnectionCount(): number {
    return this.connections.size
  }

  // ── Internal helpers ───────────────────────────────────────

  private clearTimers(connection: StreamConnection): void {
    if (connection.reconnectTimer) {
      clearTimeout(connection.reconnectTimer)
      connection.reconnectTimer = undefined
    }
    if (connection.heartbeatTimer) {
      clearTimeout(connection.heartbeatTimer)
      connection.heartbeatTimer = undefined
    }
    if (connection.stalenessTimer) {
      clearTimeout(connection.stalenessTimer)
      connection.stalenessTimer = undefined
    }
  }
}

// ── Singleton export ─────────────────────────────────────────────

export type { StreamConnection }
export const streamPool = new StreamPoolManager()