import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect, lazy, Suspense } from 'react'
import { useAppStore } from '@/store/app'
import { useSessionsStore } from '@/store/sessions'
import { useAuthStore } from '@/store/auth'
import { getLoginUser } from '@/api/auth'
import { getLoginRequiredUrl } from '@/api/client'
import { getUserSessions, abortSession, restoreSession } from '@/api/sessions'
import { streamPool } from '@/services/streamPool'
import { AppLayout } from '@/components/layout/AppLayout'
import { ErrorBoundary } from '@/components/common/ErrorBoundary'
import { SessionPage } from '@/pages/SessionPage'
import { SystemPage } from '@/pages/SystemPage'
import { TracingPage } from '@/pages/TracingPage'

const McpPage = lazy(() => import('@/pages/McpPage').then(m => ({ default: m.McpPage })))
const CodingPage = lazy(() => import('@/pages/CodingPage').then(m => ({ default: m.CodingPage })))

/**
 * Restore sessions from backend user sessions API.
 * Uses real status from backend instead of hardcoding 'idle'.
 * Running sessions are degraded to aborted via optimistic abort.
 * Inactive sessions are auto-restored transparently.
 */
async function restoreSessionsFromBackend(email: string) {
  const { addSession, setLoadStatus, updateSessionMeta } = useSessionsStore.getState()

  try {
    const res = await getUserSessions(email)

    // Populate Zustand sessions store with real status
    for (const meta of res.sessions) {
      const realStatus = meta.status ?? 'idle'
      addSession({
        session_id: meta.session_id,
        agent_name: meta.agent_name,
        mode: meta.mode,
        status: realStatus,
        title: meta.title,
        game_version: meta.game_version,
        module: meta.module,
        created_at: meta.created_at,
        hidden: meta.hidden,
      })

      // Degrade running sessions: optimistic abort
      if (realStatus === 'running' || realStatus === 'interrupt_pending') {
        abortSession(meta.session_id).catch(() => {})  // fire-and-forget
        updateSessionMeta(meta.session_id, { status: 'aborted' })
      }

      // Auto-restore inactive sessions transparently
      if (realStatus === 'inactive') {
        setLoadStatus(meta.session_id, 'restoring')
        restoreSession(email, meta.session_id)
          .then(() => {
            // Normalize meta.status 'inactive' → 'idle' after successful restore,
            // otherwise SessionPage's auto-load effect (which whitelists only
            // completed/failed/idle/aborted) won't trigger /messages fetch
            // on first click into this session.
            updateSessionMeta(meta.session_id, { status: 'idle' })
            setLoadStatus(meta.session_id, 'idle')  // ready for on-demand message loading
          })
          .catch(() => {
            setLoadStatus(meta.session_id, 'error')
          })
      }
    }
  } catch {
    console.warn('[App] Failed to restore sessions from backend')
  }
}

// Legacy localStorage cleanup — remove stale keys from previous versions
function cleanupLegacyStorage() {
  try {
    localStorage.removeItem('qa_agent_session_ids')
  } catch { /* ignore */ }
}

/**
 * 8.2 — Scan all running/interrupt_pending sessions and open stream connections.
 * Called once after sessions are restored from backend.
 */
function bootStreamPool() {
  const { sessions } = useSessionsStore.getState()
  for (const [sessionId, record] of Object.entries(sessions)) {
    const status = record.meta.status
    if (status === 'running' || status === 'interrupt_pending') {
      // Read persisted lastSeqId from sessionStorage
      let lastSeqId = record.lastSeqId || 0
      try {
        const stored = sessionStorage.getItem(`stream_seq_${sessionId}`)
        if (stored) {
          const parsed = parseInt(stored, 10)
          if (parsed > lastSeqId) lastSeqId = parsed
        }
      } catch { /* ignore */ }
      streamPool.ensureConnection(sessionId, lastSeqId)
    }
  }
}

export default function App() {
  const initialize = useAppStore(s => s.initialize)
  const fetchAvailableModels = useAppStore(s => s.fetchAvailableModels)
  const authStatus = useAuthStore(s => s.status)
  const setUser = useAuthStore(s => s.setUser)

  useEffect(() => {
    // Step 1: Check login status first
    getLoginUser().then(res => {
      if (res.code === 0 && res.data.user_info) {
        setUser(res.data.user_info)
        // Step 2: Only initialize app data after auth confirmed
        initialize()
        // Step 2b: Fetch available models for UI model selector
        fetchAvailableModels()
        // Step 3: Restore sessions from backend using current user
        cleanupLegacyStorage()
        restoreSessionsFromBackend(res.data.user_info.email).then(() => {
          // Step 4: Boot stream pool for active sessions
          bootStreamPool()
        })
      } else if (res.code === 300 && res.data.redirect_url) {
        // Override backend's Referer-derived origin_url with current page URL
        // (Referer unreliable → would fall back to frontend_url '/' = AI 助手).
        window.location.href = getLoginRequiredUrl(window.location.href)
      }
    }).catch(() => {
      // Network error — treat as unauthenticated, redirect to login
      window.location.href = getLoginRequiredUrl(window.location.href)
    })
  }, [initialize, fetchAvailableModels, setUser])

  // 8.1 — Close all stream connections on page unload
  useEffect(() => {
    const handleBeforeUnload = () => {
      streamPool.closeAll()
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [])

  // Show full-screen loading while checking auth
  if (authStatus === 'loading') {
    return (
      <div className="fixed inset-0 flex flex-col items-center justify-center bg-[hsl(var(--background))] gap-4">
        <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-[hsl(var(--muted-foreground))]">正在验证登录状态…</p>
      </div>
    )
  }

  return (
    <ErrorBoundary>
      <BrowserRouter>
      <Routes>
        {/* 所有页面共用 AppLayout 壳(侧边栏 + 会话)。
            /eval 评测页迁入 AppLayout 作内容页,与 nav 项 UX 一致(侧边栏常驻);
            nav 项 dev-only(见 navRegistry requiresFeature),但路由本身保留可直连 URL。 */}
        <Route
          path="*"
          element={
            <AppLayout>
              <Routes>
                <Route path="/" element={<Navigate to="/sessions" replace />} />
                <Route path="/sessions" element={<SessionPage />} />
                <Route path="/sessions/:sessionId" element={<SessionPage />} />
                <Route
                  path="/mcp"
                  element={
                    <Suspense fallback={<div className="p-8 text-sm text-[hsl(var(--muted-foreground))]">加载 MCP 管理页…</div>}>
                      <McpPage />
                    </Suspense>
                  }
                />
                <Route path="/coding" element={<Suspense fallback={<div className="p-8 text-sm text-[hsl(var(--muted-foreground))]">加载编码工作台…</div>}><CodingPage /></Suspense>} />
                <Route path="/coding/:sessionId" element={<Suspense fallback={<div className="p-8 text-sm text-[hsl(var(--muted-foreground))]">加载编码工作台…</div>}><CodingPage /></Suspense>} />
                <Route path="/tracing" element={<TracingPage />} />
                <Route path="/system" element={<SystemPage />} />
              </Routes>
            </AppLayout>
          }
        />
      </Routes>
    </BrowserRouter>
    </ErrorBoundary>
  )
}