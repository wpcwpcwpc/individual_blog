/// <reference types="vitest/globals" />
import { useSessionsStore, type SessionMeta } from '@/store/sessions'

// ── Helpers ────────────────────────────────────────────────────────

function reset() {
  useSessionsStore.setState({ sessions: {}, activeSessionId: null, draftContent: {} })
}

const REAL_META: SessionMeta = {
  session_id: 'real-sid',
  agent_name: 'QAAutomationAgent',
  mode: 'normal',
  status: 'idle',
  created_at: Date.now(),
}

describe('sessions store — pending 会话动作（harden-create-session-entry）', () => {
  beforeEach(() => reset())

  it('addPendingSession 返回 pending- 前缀临时 id 并插入 creating 占位记录', () => {
    const tempId = useSessionsStore.getState().addPendingSession('QAAutomationAgent')

    expect(tempId.startsWith('pending-')).toBe(true)
    const record = useSessionsStore.getState().sessions[tempId]
    expect(record.meta).toMatchObject({
      session_id: tempId,
      agent_name: 'QAAutomationAgent',
      status: 'creating',
      title: '创建中…',
    })
    expect(record.createError).toBeUndefined()
  })

  it('resolvePendingSession 原子替换占位 → 真实记录（临时 key 消失、activeSessionId 改指）', () => {
    const tempId = useSessionsStore.getState().addPendingSession('QAAutomationAgent')
    useSessionsStore.getState().setActiveSession(tempId)

    useSessionsStore.getState().resolvePendingSession(tempId, REAL_META)

    const state = useSessionsStore.getState()
    expect(state.sessions[tempId]).toBeUndefined()
    expect(state.sessions['real-sid'].meta).toEqual(REAL_META)
    expect(state.activeSessionId).toBe('real-sid')
  })

  it('resolvePendingSession 指向他处时不改 activeSessionId', () => {
    const tempId = useSessionsStore.getState().addPendingSession('QAAutomationAgent')
    useSessionsStore.getState().setActiveSession('other-sid')

    useSessionsStore.getState().resolvePendingSession(tempId, REAL_META)

    expect(useSessionsStore.getState().activeSessionId).toBe('other-sid')
  })

  it('rejectPendingSession 写 createError（状态仍为 creating，不伪造后端状态）', () => {
    const tempId = useSessionsStore.getState().addPendingSession('QAAutomationAgent')

    useSessionsStore.getState().rejectPendingSession(tempId, 'Network Error')

    const record = useSessionsStore.getState().sessions[tempId]
    expect(record.createError).toBe('Network Error')
    expect(record.meta.status).toBe('creating')
  })

  it('rejectPendingSession 对已移除的临时 id 为 no-op（resolve 后到达的失败不复活记录）', () => {
    const tempId = useSessionsStore.getState().addPendingSession('QAAutomationAgent')
    useSessionsStore.getState().removeSession(tempId)

    useSessionsStore.getState().rejectPendingSession(tempId, 'late failure')

    expect(useSessionsStore.getState().sessions[tempId]).toBeUndefined()
  })
})
