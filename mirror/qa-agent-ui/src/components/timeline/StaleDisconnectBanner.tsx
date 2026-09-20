import { useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useSessionsStore } from '@/store/sessions'
import { useAuthStore } from '@/store/auth'
import { restoreSession } from '@/api/sessions'
import { streamPool } from '@/services/streamPool'
import { cn } from '@/utils/cn'

interface Props {
  sessionId: string
}

/**
 * StaleDisconnectBanner — inline banner rendered at the end of the timeline
 * when the staleness detector confirms the WS has silently dropped events.
 *
 * Render condition (also enforced by ExecutionTimeline mounting site):
 *   sessions[sessionId].staleDisconnect === true && meta.status === 'running'
 *
 * Reconnect action (design.md D4 + add-session-history-pagination D2):
 *   1. refreshTail（paged：run 边界 splice 前缀保留；kill-switch：全量重灌，
 *      covers events that fell out of event_store memory window）
 *   2. streamPool.closeConnection (clear old WS + timers)
 *   3. streamPool.ensureConnection(lastSeqId) — backend replays seq_id >
 *      lastSeqId; markSessionConnected clears staleDisconnect (banner
 *      dismisses)
 */
export function StaleDisconnectBanner({ sessionId }: Props) {
  const staleDisconnect = useSessionsStore(s => s.sessions[sessionId]?.staleDisconnect ?? false)
  const status = useSessionsStore(s => s.sessions[sessionId]?.meta.status ?? 'idle')
  const lastSeqId = useSessionsStore(s => s.sessions[sessionId]?.lastSeqId ?? 0)
  const userEmail = useAuthStore(s => s.user?.email)

  const [reconnecting, setReconnecting] = useState(false)

  if (!staleDisconnect || status !== 'running') return null

  const handleReconnect = async () => {
    if (reconnecting) return
    setReconnecting(true)
    try {
      // Step 1: refreshTail (restore + retry on 404 — covers the case
      // where backend lost the session from memory while we were stale).
      try {
        await useSessionsStore.getState().refreshTail(sessionId)
      } catch (err) {
        const httpStatus = (err as { response?: { status?: number } })?.response?.status
        if (httpStatus === 404 && userEmail) {
          await restoreSession(userEmail, sessionId)
          await useSessionsStore.getState().refreshTail(sessionId)
        } else {
          throw err
        }
      }

      // Step 2 + 3: close stale WS, re-establish with last_event_id=lastSeqId.
      // ensureConnection internally calls markSessionConnected which clears
      // staleDisconnect → banner auto-dismisses.
      streamPool.closeConnection(sessionId)
      streamPool.ensureConnection(sessionId, lastSeqId)
    } catch (err) {
      console.warn('[StaleDisconnectBanner] reconnect failed', err)
    } finally {
      setReconnecting(false)
    }
  }

  return (
    <div
      className={cn(
        'mt-3 flex items-center gap-3 rounded-lg border px-4 py-3',
        'border-amber-300 bg-amber-50 text-amber-800',
      )}
    >
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span className="flex-1 text-sm">
        检测到与服务器连接异常，可能错过部分回复
      </span>
      <button
        type="button"
        onClick={handleReconnect}
        disabled={reconnecting}
        className={cn(
          'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
          'bg-amber-600 text-white hover:bg-amber-700',
          'disabled:cursor-not-allowed disabled:opacity-60',
        )}
      >
        <RefreshCw className={cn('h-3.5 w-3.5', reconnecting && 'animate-spin')} />
        {reconnecting ? '重连中…' : '重新连接'}
      </button>
    </div>
  )
}
