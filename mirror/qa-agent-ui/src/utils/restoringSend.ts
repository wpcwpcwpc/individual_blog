import { restoreSession, sendMessage } from '@/api/sessions'

/**
 * Restore→resend chain for orphaned sessions (POST /messages → 400
 * "No agent attached to session"). harden-resume-interrupt-chain 2.1/2.2.
 *
 * The agent rebuild behind restoreSession can take seconds — the input bar must
 * not stay in loading state for it. The caller releases the input, shows an
 * interim hint, and hands the payload here; the chain runs restore → resend in
 * the background.
 *
 * Dedup per session: while a chain is in flight, further 400-retries attach to
 * it — they wait for the restore to finish, then send their OWN payload (the
 * in-flight chain only resends its own message).
 */
export interface ResendPayload {
  content: string
  stream: boolean
  workspace_context?: { selected_paths: string[] }
  activate_skills?: string[]
  model_override?: string
}

const restoringPromises = new Map<string, Promise<void>>()

export function enqueueRestoringSend(
  sessionId: string,
  email: string,
  payload: ResendPayload,
): Promise<void> {
  const existing = restoringPromises.get(sessionId)
  if (existing) {
    // Attach: wait for the in-flight restore, then send this payload.
    return existing.then(() => sendMessage(sessionId, payload)).then(() => undefined)
  }
  const chain = (async () => {
    await restoreSession(email, sessionId)
    await sendMessage(sessionId, payload)
  })().finally(() => {
    restoringPromises.delete(sessionId)
  })
  restoringPromises.set(sessionId, chain)
  return chain
}

/** Test-only: clear in-flight dedup state between tests. */
export function resetRestoringPromisesForTest(): void {
  restoringPromises.clear()
}
