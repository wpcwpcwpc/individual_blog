import { useEffect, useMemo } from 'react'
import { streamPool } from '@/services/streamPool'
import { useSessionsStore } from '@/store/sessions'

/**
 * useStream — thin React hook wrapping StreamPoolManager.
 *
 * Lifecycle:
 *   - On mount / sessionId change: calls `streamPool.ensureConnection`
 *     with the persisted `lastSeqId` from sessionStorage.
 *   - On unmount: does **NOT** close the connection — the Pool Manager
 *     owns the full connection lifecycle.
 *
 * Exposes reactive `isConnected` status.
 */
export function useStream(sessionId: string | null, enabled: boolean = true) {
  // Read lastSeqId from sessionStorage on mount
  useEffect(() => {
    if (!sessionId || !enabled) return

    let lastSeqId = 0
    try {
      const stored = sessionStorage.getItem(`stream_seq_${sessionId}`)
      if (stored) {
        lastSeqId = parseInt(stored, 10) || 0
      }
    } catch { /* ignore */ }

    // Also check store for a potentially fresher value
    const record = useSessionsStore.getState().sessions[sessionId]
    if (record && record.lastSeqId > lastSeqId) {
      lastSeqId = record.lastSeqId
    }

    streamPool.ensureConnection(sessionId, lastSeqId)
  }, [sessionId, enabled])

  const isConnected = useMemo(() => {
    if (!sessionId) return false
    return streamPool.isConnected(sessionId)
  }, [sessionId])

  return { isConnected }
}