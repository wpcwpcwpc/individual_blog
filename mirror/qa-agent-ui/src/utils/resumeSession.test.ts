/// <reference types="vitest/globals" />
import { useSessionsStore } from '@/store/sessions'
import type { InterruptPayload } from '@/types/api'
import { resumeInterruptedSession } from '@/utils/resumeSession'
import { restoreSession, sendMessage } from '@/api/sessions'

// ── API mocks（记录调用顺序 + 快照 restore 触发瞬间的 store 状态）──

const { calls, snapshotAtRestore } = vi.hoisted(() => ({
  calls: [] as string[],
  snapshotAtRestore: { current: null as null | { meta: { status: string }; interrupt: unknown } },
}))

vi.mock('@/api/sessions', () => ({
  restoreSession: vi.fn(async () => {
    calls.push('restore')
    const rec = useSessionsStore.getState().sessions['sid-resume']
    snapshotAtRestore.current = rec
      ? { meta: { status: rec.meta.status }, interrupt: rec.interrupt }
      : null
  }),
  resumeSession: vi.fn(async () => {
    calls.push('resume')
  }),
  sendMessage: vi.fn(async () => {
    calls.push('send')
  }),
}))

vi.mock('@/services/streamPool', () => ({
  streamPool: {
    ensureConnection: vi.fn(() => {
      calls.push('ws')
    }),
  },
}))

// ── Store fixture ─────────────────────────────────────────────────

const interruptPayload = { type: 'review', payload: {} } as unknown as InterruptPayload

function seedSession(overrides: { loadStatus?: 'ready' | 'idle' } = {}) {
  useSessionsStore.setState({ sessions: {}, activeSessionId: null, draftContent: {} })
  useSessionsStore.getState().addSession({
    session_id: 'sid-resume',
    agent_name: 'TestAgent',
    mode: 'normal',
    status: 'aborted',
    created_at: Date.now(),
  })
  useSessionsStore.getState().setInterrupt('sid-resume', interruptPayload)
  if (overrides.loadStatus) {
    useSessionsStore.getState().setLoadStatus('sid-resume', overrides.loadStatus)
  }
}

beforeEach(() => {
  calls.length = 0
  snapshotAtRestore.current = null
  vi.clearAllMocks()
})

// ── 乐观先行（Phase 0 在任何网络请求之前生效）────────────────────

describe('resumeInterruptedSession — optimistic-first', () => {
  it('applies optimistic state before the first network call', async () => {
    seedSession()
    await resumeInterruptedSession('sid-resume', 'user@test', 0, '')

    // restore 触发瞬间（第一个网络调用）interrupt 已清除、状态已置 running
    expect(calls[0]).toBe('restore')
    expect(snapshotAtRestore.current).not.toBeNull()
    expect(snapshotAtRestore.current!.meta.status).toBe('running')
    expect(snapshotAtRestore.current!.interrupt).toBeNull()
  })

  it('runs the chain in order restore → resume → ws → send when memory is gone', async () => {
    seedSession()
    await resumeInterruptedSession('sid-resume', 'user@test', 0, '继续')
    expect(calls).toEqual(['restore', 'resume', 'ws', 'send'])
  })

  it('skips restore when session memory is alive (loadStatus ready)', async () => {
    seedSession({ loadStatus: 'ready' })
    await resumeInterruptedSession('sid-resume', 'user@test', 0, '')
    expect(calls).toEqual(['resume', 'ws', 'send'])
  })
})

// ── 失败回滚 ─────────────────────────────────────────────────────

describe('resumeInterruptedSession — rollback on failure', () => {
  it('reverts status to aborted, restores interrupt snapshot, then rethrows', async () => {
    seedSession()
    vi.mocked(sendMessage).mockRejectedValueOnce(new Error('boom'))

    await expect(resumeInterruptedSession('sid-resume', 'user@test', 0, '')).rejects.toThrow('boom')

    const rec = useSessionsStore.getState().sessions['sid-resume']
    expect(rec.meta.status).toBe('aborted')
    expect(rec.interrupt).toBe(interruptPayload)
  })

  it('restore failure also rolls back (no resume/send attempted)', async () => {
    seedSession()
    // mockRejectedValueOnce 会整体覆盖实现（绕过 calls.push）→ 用显式实现
    vi.mocked(restoreSession).mockImplementationOnce(async () => {
      calls.push('restore')
      throw new Error('restore boom')
    })

    await expect(resumeInterruptedSession('sid-resume', 'user@test', 0, '')).rejects.toThrow('restore boom')

    const rec = useSessionsStore.getState().sessions['sid-resume']
    expect(rec.meta.status).toBe('aborted')
    expect(rec.interrupt).toBe(interruptPayload)
    expect(calls).toEqual(['restore'])
  })
})
