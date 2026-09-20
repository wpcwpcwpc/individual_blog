import { useParams, useNavigate, Navigate } from 'react-router-dom'
import { useEffect, useCallback, useState } from 'react'
import { useSessionsStore } from '@/store/sessions'
import { useWorkspaceStore } from '@/store/workspace'
import { useAuthStore } from '@/store/auth'
import { CODING_AGENT_ID } from '@/config/agentIds'
import { useStream } from '@/hooks/useStream'
import { useResizable } from '@/hooks/useResizable'
import { SessionHeader } from '@/components/session/SessionHeader'
import { ExecutionTimeline } from '@/components/timeline/ExecutionTimeline'
import { InterruptPanel } from '@/components/interrupt/InterruptPanel'
import { WorkspacePanel } from '@/components/workspace/WorkspacePanel'
import { ResizeHandle } from '@/components/ui/ResizeHandle'
import { MessageInputBar } from '@/components/session/MessageInputBar'
import { MCPPanel } from '@/components/mcp/MCPPanel'
import { ConfirmDeleteModal } from '@/components/common/ConfirmDeleteModal'
import { deleteSession, getSessionStatus, restoreSession, getSessionMessages, getSessionMessagesTail } from '@/api/sessions'
import { getWorkspace } from '@/api/workspace'
import { streamPool } from '@/services/streamPool'
import { hydrateMessages } from '@/utils/hydrateHistory'
import { isHistoryPaginationEnabled } from '@/utils/historyPaging'

export function SessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const sessions = useSessionsStore(s => s.sessions)
  const removeSession = useSessionsStore(s => s.removeSession)
  const addSession = useSessionsStore(s => s.addSession)
  const setActiveSession = useSessionsStore(s => s.setActiveSession)
  const setLoadStatus = useSessionsStore(s => s.setLoadStatus)
  const hydrateSessionEvents = useSessionsStore(s => s.hydrateSessionEvents)
  const user = useAuthStore(s => s.user)
  const storeSetWorkspace = useWorkspaceStore(s => s.setWorkspace)
  const storeClearWorkspace = useWorkspaceStore(s => s.clearWorkspace)
  const navigate = useNavigate()

  // Resizable interrupt panel (hook must be called unconditionally)
  const {
    width: interruptWidth,
    isDragging: interruptDragging,
    handleProps: interruptHandleProps,
  } = useResizable({
    defaultWidth: 420,
    minWidth: 300,
    maxWidth: 600,
    storageKey: 'qa-ui:interrupt-panel-width',
    direction: 'left',
  })

  // ── Session loading state machine ─────────────────────────────
  const loadMessages = useCallback(async (sid: string, agentName: string) => {
    setLoadStatus(sid, 'loading_messages')
    try {
      // add-session-history-pagination D2：paged 模式首载尾页（kill-switch 回退全量）
      if (isHistoryPaginationEnabled()) {
        const res = await getSessionMessagesTail(sid)
        useSessionsStore.getState().hydrateTailPage(sid, hydrateMessages(res.messages, agentName), res.page_meta ?? null)
      } else {
        const res = await getSessionMessages(sid)
        const events = hydrateMessages(res.messages, agentName)
        hydrateSessionEvents(sid, events)
      }
      // hydrate actions set loadStatus='ready' internally
    } catch {
      setLoadStatus(sid, 'error')
    }
  }, [setLoadStatus, hydrateSessionEvents])

  const loadSessionData = useCallback(async (sid: string) => {
    const record = sessions[sid]
    // If already has timeline events, no need to reload
    if (record?.timeline.length > 0) return
    if (record?.loadStatus === 'loading_messages' || record?.loadStatus === 'restoring') return

    const agentName = record?.meta.agent_name ?? 'Agent'

    // fix-session-io-blocking：status 探测与消息加载并行——tail 端点直读存储，
    // 不依赖内存态，无需等 status 结果。status 404/inactive（后端重启后的罕见
    // 路径）才走 restore 阻塞分支；restore 完成后消息若已到达则不重复拉取。
    let statusFailed = false
    const statusPromise = getSessionStatus(sid)
      .then(statusRes => {
        if (statusRes.status === 'inactive') {
          // Session exists in DB but not in memory (e.g. after backend restart).
          // Must restore before the session can accept messages — treat same as 404.
          statusFailed = true
        }
      })
      .catch((err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status
        if (status === 404) {
          statusFailed = true
        }
        // Other errors (network blip): let the message load decide the UX.
      })

    const messagesPromise = loadMessages(sid, agentName)

    await Promise.all([statusPromise, messagesPromise])

    if (!statusFailed) return

    // Memory lost / inactive — restore first.
    if (!user?.email) {
      setLoadStatus(sid, 'error')
      return
    }
    setLoadStatus(sid, 'restoring')
    try {
      await restoreSession(user.email, sid)
      // Messages already arrived via the parallel loadMessages — only reload
      // if they did not (parallel load failed → timeline empty).
      const rec = useSessionsStore.getState().sessions[sid]
      if (!rec || rec.timeline.length === 0) {
        await loadMessages(sid, agentName)
      } else {
        setLoadStatus(sid, 'ready')
      }
    } catch {
      setLoadStatus(sid, 'error')
    }
  }, [sessions, user, loadMessages, setLoadStatus])

  // Mark the active session in Zustand store
  useEffect(() => {
    if (sessionId) {
      setActiveSession(sessionId)
    }
  }, [sessionId, setActiveSession])

  // Load workspace when switching sessions (only for non-new sessions)
  useEffect(() => {
    if (!sessionId) return
    const record = sessions[sessionId]
    // Skip for newly created sessions (status 'idle' with no timeline) —
    // they can't have a workspace yet, avoid a pointless 404.
    if (!record || (record.meta.status === 'idle' && record.timeline.length === 0)) return

    let cancelled = false
    ;(async () => {
      try {
        const info = await getWorkspace(sessionId)
        if (cancelled) return
        if (info) {
          storeSetWorkspace(sessionId, info.root_path, info.tree)
        } else {
          storeClearWorkspace(sessionId)
        }
      } catch {
        if (!cancelled) storeClearWorkspace(sessionId)
      }
    })()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, storeSetWorkspace, storeClearWorkspace])

  // Trigger session data load when navigating to a session that exists in store
  useEffect(() => {
    if (!sessionId) return
    const record = sessions[sessionId]
    if (!record) return
    if (record.loadStatus === 'idle' && record.timeline.length === 0) {
      // Auto-load for any session that needs its messages:
      // - completed/failed: finished sessions
      // - idle: may be a restored-from-storage session (was inactive, normalized to idle)
      // - inactive: safety net if auto-restore in App.tsx hasn't normalized status yet;
      //             loadSessionData will call getSessionStatus and restore as needed.
      const s = record.meta.status
      if (
        s === 'completed' ||
        s === 'failed' ||
        s === 'idle' ||
        s === 'aborted' ||
        s === 'inactive'
      ) {
        loadSessionData(sessionId)
      }
    }
  }, [sessionId, sessions, loadSessionData])

  // Stream connection — only enabled when session is ready to receive events.
  // Disabled during restore/loading to prevent 404 loops on inactive sessions,
  // and re-enabled (via enabled dependency) once loadStatus reaches 'ready'.
  // Read loadStatus here (before any early returns) so hooks are called unconditionally.
  const currentLoadStatus = sessionId ? (sessions[sessionId]?.loadStatus ?? 'idle') : 'idle'
  const currentStatus = sessionId ? (sessions[sessionId]?.meta.status ?? 'idle') : 'idle'
  const streamEnabled = !sessionId ? false
    : currentLoadStatus === 'ready'
    || currentStatus === 'running'
    || currentStatus === 'interrupt_pending'
  useStream(sessionId ?? null, streamEnabled)

  // Delete modal state
  const [showDeleteModal, setShowDeleteModal] = useState(false)

  // Sessions reached via direct URL (e.g. "打开会话" from sync/eval progress)
  // that aren't yet in the store. The store is only hydrated at app boot from
  // GET /users/{email}/sessions, so sessions created mid-flight (bug-structuring
  // session spawned by version sync, etc.) are unknown until we fetch them
  // on-demand by id.
  const [notFoundSids, setNotFoundSids] = useState<Set<string>>(() => new Set())
  const [fetchingSids, setFetchingSids] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    if (!sessionId) return
    // harden-create-session-entry：pending 占位卡用的是 `pending-<uuid>` 临时 id，
    // 后端无对应实体，探测只会打出 404（并污染 notFoundSids）——直接短路。
    if (sessionId.startsWith('pending-')) return
    // Already in store — nothing to do. Read via getState() rather than the
    // render-scope `sessions`: deps deliberately exclude sessions/notFoundSids/
    // fetchingSids so the setFetchingSids-triggered re-render cannot re-run
    // this effect and cancel its own in-flight fetch (self-cancellation wedged
    // fetchingSids → perpetual「正在加载会话…」on URLs pointing at sessions
    // absent from the store, e.g. revisiting a deleted session via history).
    if (useSessionsStore.getState().sessions[sessionId]) return
    if (notFoundSids.has(sessionId) || fetchingSids.has(sessionId)) return

    setFetchingSids(prev => new Set(prev).add(sessionId))
    let cancelled = false
    ;(async () => {
      try {
        const res = await getSessionStatus(sessionId)
        if (cancelled) return
        // 200 — session exists (in memory or storage). Add to store; the
        // auto-load effect below will pick it up and trigger restore/messages
        // (inactive status is whitelisted there).
        addSession({
          session_id: res.session_id,
          agent_name: res.agent_name || 'Agent',
          mode: res.mode || 'normal',
          status: res.status,
          game_version: res.game_version || '',
          module: res.module || '',
          created_at: Date.now(),
        })
      } catch {
        if (cancelled) return
        // 404 or network error — settled not-found state (no perpetual spinner)
        setNotFoundSids(prev => new Set(prev).add(sessionId))
      } finally {
        // Always clear the in-flight marker, even if this run was cancelled
        // (StrictMode remount / sid change) — otherwise fetchingSids wedges.
        setFetchingSids(prev => {
          const next = new Set(prev)
          next.delete(sessionId)
          return next
        })
      }
    })()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- 只依赖 sessionId（见 effect 内注释）
  }, [sessionId])

  const handleDeleteConfirm = () => {
    if (!sessionId || !user?.email) throw new Error('用户未登录')
    streamPool.closeConnection(sessionId)
    setShowDeleteModal(false)
    // fix-session-io-blocking：乐观删除——UI 先行（导航 + store 移除），HTTP
    // 请求后台发出；失败时告警并从服务端对账（resyncDeletedSession）。
    // Navigate BEFORE removing from store to avoid the "session not found" guard
    // rendering before the navigation takes effect.
    navigate('/sessions', { replace: true })
    removeSession(sessionId)
    deleteSession(user.email, sessionId).catch(() => {
      console.warn(`[SessionPage] deleteSession failed for ${sessionId} — reconciling from server`)
      void useSessionsStore.getState().resyncDeletedSession(user.email, sessionId)
    })
  }

  // No session selected
  if (!sessionId) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-slate-600">
          <div className="text-5xl mb-3">🤖</div>
          <p className="text-sm font-medium text-slate-400">选择或新建一个会话开始使用</p>
          <p className="text-xs text-slate-600 mt-1">点击左侧「新建会话」按钮</p>
        </div>
      </div>
    )
  }

  // Session not in store — either still fetching on-demand, or confirmed missing.
  if (!sessions[sessionId]) {
    const isFetching = fetchingSids.has(sessionId)
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-slate-600">
          {isFetching ? (
            <>
              <div className="w-6 h-6 mx-auto mb-3 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-sm text-slate-400">正在加载会话…</p>
            </>
          ) : (
            <div className="space-y-2">
              <p className="text-sm">会话不存在或已被删除</p>
              <button
                onClick={() => navigate('/sessions')}
                className="text-xs text-violet-400 hover:text-violet-300 underline"
              >
                返回会话列表
              </button>
            </div>
          )}
        </div>
      </div>
    )
  }

  const record = sessions[sessionId]
  const loadStatus = currentLoadStatus

  // 编码工作台会话不在通用会话界面展示（工作台提供专属 UI），重定向回工作台
  if (record.meta.agent_name === CODING_AGENT_ID) {
    return <Navigate to={`/coding/${sessionId}`} replace />
  }

  // Delete modal (rendered in all states)
  const deleteModal = showDeleteModal ? (
    <ConfirmDeleteModal
      sessionTitle={`${record.meta.agent_name} · ${sessionId.slice(0, 8)}`}
      isRunning={record.meta.status === 'running'}
      onConfirm={handleDeleteConfirm}
      onCancel={() => setShowDeleteModal(false)}
    />
  ) : null

  // Session loading states — show overlay instead of timeline
  if (loadStatus === 'restoring') {
    return (
      <div className="flex flex-col h-full">
        <SessionHeader sessionId={sessionId} onDelete={() => setShowDeleteModal(true)} />
        <div className="flex-1 flex items-center justify-center gap-3">
          <div className="w-5 h-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-slate-400">正在恢复会话，请稍候…</p>
        </div>
      </div>
    )
  }

  if (loadStatus === 'loading_messages') {
    return (
      <div className="flex flex-col h-full">
        <SessionHeader sessionId={sessionId} onDelete={() => setShowDeleteModal(true)} />
        <div className="flex-1 p-4 space-y-3 overflow-hidden">
          {/* Skeleton rows */}
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className={`flex gap-3 ${i % 2 === 0 ? '' : 'flex-row-reverse'}`}>
              <div className="w-7 h-7 rounded-full bg-slate-800 animate-pulse flex-shrink-0" />
              <div
                className="h-10 rounded-xl bg-slate-800 animate-pulse"
                style={{ width: `${40 + (i * 13) % 40}%` }}
              />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (loadStatus === 'error') {
    return (
      <div className="flex flex-col h-full">
        <SessionHeader sessionId={sessionId} onDelete={() => setShowDeleteModal(true)} />
        <div className="flex-1 flex flex-col items-center justify-center gap-3">
          <p className="text-sm text-red-400">会话记录加载失败</p>
          <button
            onClick={() => loadSessionData(sessionId)}
            className="text-xs text-violet-400 hover:text-violet-300 underline"
          >
            点击重试
          </button>
        </div>
      </div>
    )
  }

  const interruptIsOpen = record.interrupt !== null

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <SessionHeader sessionId={sessionId} onDelete={() => setShowDeleteModal(true)} />

      {/* Main area: workspace + timeline + resize handle + interrupt panel side by side */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <WorkspacePanel sessionId={sessionId} />
        <ExecutionTimeline sessionId={sessionId} />
        {interruptIsOpen && (
          <ResizeHandle handleProps={interruptHandleProps} isDragging={interruptDragging} />
        )}
        <InterruptPanel
          sessionId={sessionId}
          panelWidth={interruptWidth}
          isDragging={interruptDragging}
        />
        <MCPPanel />
      </div>

      {/* Message input */}
      <MessageInputBar sessionId={sessionId} />

      {/* Delete confirmation modal */}
      {deleteModal}
    </div>
  )
}
