import { restoreSession, resumeSession, sendMessage } from '@/api/sessions'
import { streamPool } from '@/services/streamPool'
import { useSessionsStore } from '@/store/sessions'

/**
 * Resume an interrupted session — optimistic-first chain (harden-resume-interrupt-chain).
 *
 * Phase 0 (optimistic, BEFORE any network): clear the interrupt card and mark
 *   status 'running' so the UI reacts immediately; snapshot pre-state for rollback.
 * Phase 1 (background, serialized): if session memory is gone (loadStatus !==
 *   'ready' or status inactive), POST /users/{email}/sessions/{id}/restore to
 *   rebuild the Agent in memory (idempotent).
 * Phase 2: POST /sessions/{id}/resume {type:'user_resume', message} to clear abort signal.
 * Phase 3: streamPool.ensureConnection to (re)open WS.
 * Phase 4: POST /sessions/{id}/messages with continuation prompt to actually start
 *   the agent (user message, or continuation prompt when empty).
 *
 * On any background failure: rollback status → 'aborted' + restore the interrupt
 * snapshot, then RETHROW — callers keep their existing catch/revert contract
 * (MessageInputBar.handleResume both catches and surfaces the error; its extra
 * `updateSessionMeta(aborted)` is idempotent with ours).
 *
 * Mirrors MessageInputBar.handleResume logic, extracted for reuse.
 */
export async function resumeInterruptedSession(
  sid: string,
  email: string,
  lastSeqId: number,
  userMessage: string = '',
): Promise<void> {
  const store = useSessionsStore.getState()
  const record = store.sessions[sid]

  // ── Phase 0: optimistic local state (before the first RTT) ──
  const prevInterrupt = record?.interrupt ?? null
  store.setInterrupt(sid, null)
  store.updateSessionMeta(sid, { status: 'running' })

  const rollback = () => {
    const s = useSessionsStore.getState()
    s.updateSessionMeta(sid, { status: 'aborted' })
    s.setInterrupt(sid, prevInterrupt)
  }

  try {
    // Phase 1: restore memory if missing (idempotent — backend no-ops if alive)
    const needsRestore = !record || record.loadStatus !== 'ready' || record.meta.status === 'inactive'
    if (needsRestore) {
      await restoreSession(email, sid)
    }

    // Phase 2: clear backend abort signal
    await resumeSession(sid, { type: 'user_resume', message: userMessage })

    // Phase 3: reconnect WS (was closed after run_aborted / refresh)
    streamPool.ensureConnection(sid, lastSeqId)

    // Phase 4: actually start the agent — if user didn't type a message, send a
    // continuation prompt so the LLM (which has full history) picks up where it left off.
    const resumeContent = userMessage || '请从中断的位置继续执行之前的任务'
    await sendMessage(sid, { content: resumeContent, stream: true })
  } catch (err) {
    rollback()
    throw err
  }
}
