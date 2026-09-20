/// <reference types="vitest/globals" />
import { useSessionsStore, type SessionMeta, type SessionRecord } from '@/store/sessions'
import { streamPool } from '@/services/streamPool'
import { regenerateTurn, getSessionMessagesTail } from '@/api/sessions'
import type { HistoryMessage } from '@/types/api'
import type { AgentMessageItem, TimelineItem, UserMessageItem } from '@/types/events'

vi.mock('@/api/sessions', () => ({
  getWSStreamUrl: (sid: string) => `ws://test/${sid}`,
  getSessionLastSeq: vi.fn(),
  getSessionMessages: vi.fn(),
  getSessionMessagesTail: vi.fn(),
  truncateMessages: vi.fn(),
  regenerateTurn: vi.fn(),
  restoreSession: vi.fn().mockResolvedValue({}),
}))

vi.mock('@/services/streamPool', () => ({
  streamPool: {
    closeConnection: vi.fn(),
    ensureConnection: vi.fn(),
    closeAll: vi.fn(),
    isConnected: vi.fn(() => false),
  },
}))

// ── Fixtures ──────────────────────────────────────────────────────

const sid = 'sid-regen-store'

const tailHistory: HistoryMessage[] = [
  { id: 'msg_0', role: 'user', content: 'q0', created_at: 1 },
  { id: 'msg_1', role: 'assistant', content: 'r0', created_at: 2 },
  { id: 'msg_2', role: 'user', content: 'q1', created_at: 3 },
  { id: 'msg_3', role: 'assistant', content: 'r1', created_at: 4 },
]

function userItem(i: number, backendMessageId?: string): UserMessageItem {
  return { type: 'user_message', id: `u${i}`, content: `q${i}`, username: 'me', timestamp: i, backendMessageId }
}

function agentItem(i: number): AgentMessageItem {
  return { type: 'agent_message', id: `a${i}`, agent_name: 'QAAgent', content: `r${i}`, isStreaming: false, backendMessageId: `msg_${2 * i + 1}` }
}

function seedSession(timeline: TimelineItem[]): void {
  const meta: SessionMeta = { session_id: sid, agent_name: 'QAAgent', mode: 'normal', status: 'completed', created_at: Date.now(), turnCount: 2 }
  const record: SessionRecord = {
    meta,
    timeline,
    interrupt: null,
    _activeAgentName: null,
    loadStatus: 'ready',
    lastActivatedSkills: [],
    lastSeqId: 5,
    hasUnreadInterrupt: false,
  }
  useSessionsStore.setState({ sessions: { [sid]: record }, activeSessionId: sid, draftContent: {} })
  sessionStorage.setItem(`stream_seq_${sid}`, '5')
}

const netError = (status?: number) => {
  const err = new Error('network down') as Error & { response?: { status: number } }
  if (status != null) err.response = { status }
  return err
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...tailHistory], page_meta: null })
  useSessionsStore.setState({ sessions: {}, activeSessionId: null, draftContent: {} })
  sessionStorage.clear()
})

// ── 锚三级解析（D5）+ 乐观切（D3）──────────────────────────────

describe('regenerateTurn — 锚解析与乐观切', () => {
  it('hydrated 轮：U 卡位号 id 直传（零额外网络），U 原位保留', async () => {
    seedSession([userItem(0, 'msg_0'), agentItem(0), userItem(1, 'msg_2'), agentItem(1)])
    vi.mocked(regenerateTurn).mockResolvedValue({
      request_id: 'rid-1', truncated_count: 1, remaining_count: 3, history_version: null,
    })

    await useSessionsStore.getState().regenerateTurn(sid, 'u1')

    // 锚：U 卡自带 id，未做尾页解析
    expect(getSessionMessagesTail).not.toHaveBeenCalled()
    expect(regenerateTurn).toHaveBeenCalledWith(sid, expect.objectContaining({ message_id: 'msg_2' }))
    // U 原位保留：只剩 [u0, a0, u1]
    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline.map(t => t.id)).toEqual(['u0', 'a0', 'u1'])
    // turnCount 不扣减（4.4：重答不减用户轮数）
    expect(record.meta.turnCount).toBe(2)
    // WS 重连重放：关旧连接 + seq 清零 + fromSeq 0
    expect(streamPool.closeConnection).toHaveBeenCalledWith(sid)
    expect(streamPool.ensureConnection).toHaveBeenCalledWith(sid, 0)
    expect(record.lastSeqId).toBe(0)
    expect(sessionStorage.getItem(`stream_seq_${sid}`)).toBeNull()
    // meta.status 同步 running（重放窗口内封锁输入）
    expect(record.meta.status).toBe('running')
    expect(record.truncateState ?? 'idle').toBe('idle')
  })

  it('起始轮（窗口首位）点击 → 保留 [u0]，切至该轮 U 之后', async () => {
    seedSession([userItem(0, 'msg_0'), agentItem(0), userItem(1, 'msg_2'), agentItem(1)])
    vi.mocked(regenerateTurn).mockResolvedValue({
      request_id: 'rid', truncated_count: 3, remaining_count: 1, history_version: null,
    })

    await useSessionsStore.getState().regenerateTurn(sid, 'u0')

    expect(useSessionsStore.getState().sessions[sid].timeline.map(t => t.id)).toEqual(['u0'])
    expect(useSessionsStore.getState().sessions[sid].meta.turnCount).toBe(2)
  })

  it('最新 live 轮（无 id、其后无 user 卡）→ turn: last 锚（零网络）', async () => {
    seedSession([userItem(0, 'msg_0'), agentItem(0), userItem(1), agentItem(1)])
    vi.mocked(regenerateTurn).mockResolvedValue({
      request_id: 'rid', truncated_count: 1, remaining_count: 3, history_version: null,
    })

    await useSessionsStore.getState().regenerateTurn(sid, 'u1')

    expect(regenerateTurn).toHaveBeenCalledWith(sid, expect.objectContaining({ turn_last: true }))
    expect(getSessionMessagesTail).not.toHaveBeenCalled()
    expect(useSessionsStore.getState().sessions[sid].timeline.map(t => t.id)).toEqual(['u0', 'a0', 'u1'])
  })

  it('更早 live 轮（无 id、后有 user 卡）→ 一次尾页解析按倒数位号定位', async () => {
    seedSession([userItem(0), agentItem(0), userItem(1), agentItem(1)])

    await useSessionsStore.getState().regenerateTurn(sid, 'u0')

    // 尾页解析一次（非阻塞视觉反馈：乐观切已先行）
    expect(getSessionMessagesTail).toHaveBeenCalledTimes(1)
    // laterUserCount=1 → users[len-1-1] = msg_0
    expect(regenerateTurn).toHaveBeenCalledWith(sid, expect.objectContaining({ message_id: 'msg_0' }))
    expect(useSessionsStore.getState().sessions[sid].timeline.map(t => t.id)).toEqual(['u0'])
  })

  it('尾页解析失败（尾页未覆盖目标轮）→ R1 原位回插 + idle + 抛错', async () => {
    seedSession([userItem(0), agentItem(0), userItem(1), agentItem(1)])
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [], page_meta: null })

    await expect(useSessionsStore.getState().regenerateTurn(sid, 'u0')).rejects.toThrow('尾页未覆盖目标轮')

    expect(regenerateTurn).not.toHaveBeenCalled()
    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline.map(t => t.id)).toEqual(['u0', 'a0', 'u1', 'a1'])
    expect(record.truncateState ?? 'idle').toBe('idle')
  })
})

// ── 失败回滚 / R2 / 幂等重试（D13 同构）─────────────────────────

describe('regenerateTurn — 失败路径', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('重试耗尽（3 次网络失败）→ R1 原下标回插并抛错', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    seedSession([userItem(0, 'msg_0'), agentItem(0), userItem(1, 'msg_2'), agentItem(1)])
    vi.mocked(regenerateTurn).mockRejectedValue(netError())

    const promise = useSessionsStore.getState().regenerateTurn(sid, 'u1')
    const expectation = expect(promise).rejects.toThrow('network down')
    await vi.advanceTimersByTimeAsync(500)
    await vi.advanceTimersByTimeAsync(2000)
    await expectation

    expect(regenerateTurn).toHaveBeenCalledTimes(3)
    // 原下标回插：U 之后的 [a1] 恢复，视图回滚
    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline.map(t => t.id)).toEqual(['u0', 'a0', 'u1', 'a1'])
    expect(record.truncateState ?? 'idle').toBe('idle')
    // 未走到重连阶段
    expect(streamPool.ensureConnection).not.toHaveBeenCalled()
  })

  it('404（外部已改）→ 不重试，R2 refreshTail 收敛', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    seedSession([userItem(0, 'msg_0'), agentItem(0), userItem(1, 'msg_2'), agentItem(1)])
    vi.mocked(regenerateTurn).mockRejectedValue(netError(404))

    await expect(useSessionsStore.getState().regenerateTurn(sid, 'u1')).rejects.toThrow()

    expect(regenerateTurn).toHaveBeenCalledTimes(1)
    expect(getSessionMessagesTail).toHaveBeenCalledTimes(1) // R2 权威尾页收敛
  })

  it('广播抢先确认（响应丢失）→ 跳过重试，直接重连（不 refreshTail 双渲染）', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    seedSession([userItem(0, 'msg_0'), agentItem(0), userItem(1, 'msg_2'), agentItem(1)])
    let capturedRequestId = ''
    vi.mocked(regenerateTurn).mockImplementationOnce(async (_s, body) => {
      capturedRequestId = body.request_id ?? ''
      throw netError()
    })

    const promise = useSessionsStore.getState().regenerateTurn(sid, 'u1')
    await vi.advanceTimersByTimeAsync(0)  // 首次失败，500ms 退避中

    // 自家 messages_truncated 广播（request_id 匹配）→ 抢先确认
    await useSessionsStore.getState().handleMessagesTruncated(sid, {
      event_type: 'messages_truncated',
      session_id: sid,
      truncated_count: 1,
      remaining_count: 3,
      request_id: capturedRequestId,
    })

    await vi.advanceTimersByTimeAsync(500)
    await promise

    // 不再重试 POST；不 refreshTail（重放重建轮体）
    expect(regenerateTurn).toHaveBeenCalledTimes(1)
    expect(getSessionMessagesTail).not.toHaveBeenCalled()
    expect(streamPool.ensureConnection).toHaveBeenCalledWith(sid, 0)
  })
})
