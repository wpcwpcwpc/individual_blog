/// <reference types="vitest/globals" />
import type { ReactNode } from 'react'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { SessionList } from '@/components/session/SessionList'
import { useSessionsStore, type SessionMeta } from '@/store/sessions'

// ── Mocks ──────────────────────────────────────────────────────────

vi.mock('@/components/ui/Tooltip', () => ({
  Tooltip: ({ children }: { children?: ReactNode }) => children ?? null,
}))
vi.mock('@/services/streamPool', () => ({
  streamPool: { closeConnection: vi.fn(), ensureConnection: vi.fn() },
}))
vi.mock('@/api/sessions', () => ({
  deleteSession: vi.fn(async () => ({})),
  updateSessionTitle: vi.fn(async () => ({})),
  updateSessionVisibility: vi.fn(async () => ({})),
}))

// ── Helpers ────────────────────────────────────────────────────────

function seedPending(agentName = 'QAAutomationAgent') {
  useSessionsStore.setState({ sessions: {}, activeSessionId: null, draftContent: {} })
  return useSessionsStore.getState().addPendingSession(agentName)
}

function renderList() {
  return render(
    <MemoryRouter initialEntries={['/sessions']}>
      <Routes>
        <Route path="/sessions" element={<SessionList />} />
        <Route path="/sessions/:sessionId" element={<div>SESSION_PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SessionList — pending 占位卡（harden-create-session-entry）', () => {
  afterEach(() => cleanup())

  it('creating 状态渲染「创建中」占位卡（不可进入会话页）', () => {
    const tempId = seedPending()
    renderList()

    expect(screen.getByText('创建中…')).toBeTruthy()
    expect(screen.getByText('创建中')).toBeTruthy()

    fireEvent.click(screen.getByText('创建中…'))
    expect(screen.queryByText('SESSION_PAGE')).toBeNull()
    expect(useSessionsStore.getState().activeSessionId).toBeNull()
    expect(useSessionsStore.getState().sessions[tempId]).toBeTruthy()
  })

  it('reject 后渲染「创建失败」错误卡（含原因），点移除即删除记录', () => {
    const tempId = seedPending()
    useSessionsStore.getState().rejectPendingSession(tempId, 'Network Error')
    renderList()

    expect(screen.getByText('创建失败')).toBeTruthy()
    expect(screen.getByText('Network Error')).toBeTruthy()

    fireEvent.click(screen.getByRole('button'))
    expect(useSessionsStore.getState().sessions[tempId]).toBeUndefined()
  })

  it('resolve 后占位卡被真实会话替换（错误态/占位文案不再出现）', () => {
    const tempId = seedPending()
    const meta: SessionMeta = {
      session_id: 'real-sid',
      agent_name: 'QAAutomationAgent',
      mode: 'normal',
      status: 'idle',
      title: '真实会话',
      created_at: Date.now(),
    }
    useSessionsStore.getState().resolvePendingSession(tempId, meta)
    renderList()

    expect(screen.getByText('真实会话')).toBeTruthy()
    expect(screen.queryByText('创建中')).toBeNull()

    fireEvent.click(screen.getByText('真实会话'))
    expect(screen.getByText('SESSION_PAGE')).toBeTruthy()
  })
})
