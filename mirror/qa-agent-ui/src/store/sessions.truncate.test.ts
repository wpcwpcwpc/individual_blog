/// <reference types="vitest/globals" />
import { useSessionsStore, type SessionMeta, type SessionRecord } from '@/store/sessions'
import { streamPool } from '@/services/streamPool'
import { truncateMessages, getSessionMessages, getSessionMessagesTail } from '@/api/sessions'
import type { TruncateResponse } from '@/types/api'
import type { HistoryMessage } from '@/types/api'
import type { AgentMessageItem, UserMessageItem, MessagesTruncatedEvent } from '@/types/events'

vi.mock('@/api/sessions', () => ({
  getWSStreamUrl: (sid: string) => `ws://test/${sid}`,
  getSessionLastSeq: vi.fn(),
  getSessionMessages: vi.fn(),
  getSessionMessagesTail: vi.fn(),
  truncateMessages: vi.fn(),
  restoreSession: vi.fn().mockResolvedValue({}),
  getContextUsage: vi.fn().mockResolvedValue(null),
}))

// ── Fixtures ──────────────────────────────────────────────────────

const sid = 'sid-trunc'

const remainingHistory: HistoryMessage[] = [
  { id: 'msg_0', role: 'user', content: 'q0', created_at: 1 },
  { id: 'msg_1', role: 'assistant', content: 'r0', created_at: 2 },
]

const truncateOk = {
  truncated_count: 2,
  remaining_messages: [...remainingHistory],
  request_id: 'rid-1',
  history_version: { total_runs: 1, total_messages: 2 },
}

function userItem(i: number): UserMessageItem {
  return { type: 'user_message', id: `u${i}`, content: `q${i}`, username: 'me', timestamp: 1 }
}

function agentItem(i: number): AgentMessageItem {
  return { type: 'agent_message', id: `a${i}`, agent_name: 'QAAgent', content: `r${i}`, isStreaming: false, backendMessageId: `msg_${2 * i + 1}` }
}

function seedSession(status: SessionMeta['status'] = 'idle'): void {
  const meta: SessionMeta = { session_id: sid, agent_name: 'QAAgent', mode: 'normal', status, created_at: Date.now() }
  const record: SessionRecord = {
    meta,
    timeline: [userItem(0), agentItem(0), userItem(1), agentItem(1)],
    interrupt: null,
    _activeAgentName: null,
    loadStatus: 'ready',
    lastActivatedSkills: [],
    lastSeqId: 5,
    hasUnreadInterrupt: false,
  }
  useSessionsStore.setState({ sessions: { [sid]: record }, activeSessionId: sid, draftContent: {} })
}

function truncatedEvent(overrides: Partial<MessagesTruncatedEvent> = {}): MessagesTruncatedEvent {
  return {
    event_type: 'messages_truncated',
    session_id: sid,
    truncated_count: 2,
    remaining_count: 2,
    ...overrides,
  }
}

const netError = (status?: number) => {
  const err = new Error('network down') as Error & { response?: { status: number } }
  if (status != null) err.response = { status }
  return err
}

beforeEach(() => {
  vi.clearAllMocks()
  useSessionsStore.setState({ sessions: {}, activeSessionId: null, draftContent: {} })
  seedSession()
  sessionStorage.setItem(`stream_seq_${sid}`, '5')
})

// ── truncateTimeline — D13 重试状态机 ─────────────────────────────

describe('truncateTimeline — D13 truncate 幂等/重试状态机', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('网络失败 → 同 request_id 有界重试，第 2 次成功（服务端幂等重放）→ refreshTail 收敛', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const bodies: { message_id: string; request_id?: string }[] = []
    vi.mocked(truncateMessages)
      .mockImplementationOnce(async (_s, body) => { bodies.push(body); throw netError() })
      .mockImplementationOnce(async (_s, body) => { bodies.push(body); return truncateOk })
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...remainingHistory], page_meta: null })

    const promise = useSessionsStore.getState().truncateTimeline(sid, 'msg_2', 'a1')
    await vi.advanceTimersByTimeAsync(500)
    await promise

    expect(truncateMessages).toHaveBeenCalledTimes(2)
    expect(bodies[0].request_id).toBeTruthy()
    expect(bodies[1].request_id).toBe(bodies[0].request_id)  // 同 request_id 重试
    expect(getSessionMessagesTail).toHaveBeenCalledTimes(1)    // 成功路径尾页同构收敛
    expect(getSessionMessages).not.toHaveBeenCalled()
    // 收敛：尾页消息 → 2 items；水位清零；状态机回 idle
    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline).toHaveLength(2)
    expect(record.lastSeqId).toBe(0)
    expect(record.truncateState ?? 'idle').toBe('idle')
    expect(sessionStorage.getItem(`stream_seq_${sid}`)).toBeNull()
  })

  it('重试耗尽（3 次网络失败）→ R1 原下标回插恢复删除前视图并抛错', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(truncateMessages).mockRejectedValue(netError())

    const promise = useSessionsStore.getState().truncateTimeline(sid, 'msg_2', 'a1')
    const expectation = expect(promise).rejects.toThrow('network down')
    await vi.advanceTimersByTimeAsync(500)
    await vi.advanceTimersByTimeAsync(2000)
    await expectation

    expect(truncateMessages).toHaveBeenCalledTimes(3)  // 1 + 2 次重试
    expect(getSessionMessages).not.toHaveBeenCalled() // R1 不再全量重拉
    // 原下标回插：被删项（a1）按原位恢复，其余项引用不变
    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline.map(t => t.id)).toEqual(['u0', 'a0', 'u1', 'a1'])
    expect(record.truncateState ?? 'idle').toBe('idle')
  })

  it('404（外部已改）→ 不重试，R2 refreshTail 收敛', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(truncateMessages).mockRejectedValue(netError(404))
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...remainingHistory], page_meta: null })

    await expect(useSessionsStore.getState().truncateTimeline(sid, 'msg_2', 'a1')).rejects.toThrow()

    expect(truncateMessages).toHaveBeenCalledTimes(1)  // 404 不重试
    expect(getSessionMessagesTail).toHaveBeenCalledTimes(1) // R2 权威尾页收敛
    expect(useSessionsStore.getState().sessions[sid].timeline).toHaveLength(2)
  })

  it('广播抢先确认（响应丢失）→ 跳过重试，refreshTail 收敛', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    let capturedRequestId = ''
    vi.mocked(truncateMessages).mockImplementationOnce(async (_s, body) => {
      capturedRequestId = body.request_id ?? ''
      throw netError()
    })
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...remainingHistory], page_meta: null })

    const promise = useSessionsStore.getState().truncateTimeline(sid, 'msg_2', 'a1')
    await vi.advanceTimersByTimeAsync(0)  // 首次请求已失败，处于 500ms 退避窗口

    // 期间收到回显自己 request_id 的 WS 广播（时序合法：广播先于响应）
    await useSessionsStore.getState().handleMessagesTruncated(sid, truncatedEvent({ request_id: capturedRequestId }))

    await vi.advanceTimersByTimeAsync(500)
    await promise

    expect(truncateMessages).toHaveBeenCalledTimes(1)     // 不再重试
    expect(getSessionMessagesTail).toHaveBeenCalledTimes(1) // 抢先确认 → 尾页收敛
    expect(useSessionsStore.getState().sessions[sid].timeline).toHaveLength(2)
  })
})

// ── handleMessagesTruncated — D12 跨标签截断收敛 ──────────────────

describe('handleMessagesTruncated — D12 跨标签截断收敛', () => {
  it('外部截断（request_id 非本会话在途）→ 全量重灌 + status 同步 idle + 水位清零', async () => {
    seedSession('completed')
    vi.mocked(getSessionMessages).mockResolvedValue({ session_id: sid, messages: [...remainingHistory] })

    await useSessionsStore.getState().handleMessagesTruncated(sid, truncatedEvent({ request_id: 'other-tab-rid' }))

    const record = useSessionsStore.getState().sessions[sid]
    expect(record.meta.status).toBe('idle')
    expect(record.timeline).toHaveLength(2)
    expect(record.loadStatus).toBe('ready')
    expect(sessionStorage.getItem(`stream_seq_${sid}`)).toBeNull()
  })

  it('旧载荷（无 request_id，旧后端）→ 兼容收敛', async () => {
    vi.mocked(getSessionMessages).mockResolvedValue({ session_id: sid, messages: [...remainingHistory] })

    await useSessionsStore.getState().handleMessagesTruncated(sid, truncatedEvent())

    expect(getSessionMessages).toHaveBeenCalledTimes(1)
    expect(useSessionsStore.getState().sessions[sid].timeline).toHaveLength(2)
  })

  it('收敛拉取失败 → 静默告警不抛出（视图保持，等下次拉取收敛）', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(getSessionMessages).mockRejectedValue(new Error('boom'))

    await expect(
      useSessionsStore.getState().handleMessagesTruncated(sid, truncatedEvent()),
    ).resolves.toBeUndefined()

    // 水位与 status 仍已处理（不因拉取失败回退）
    expect(sessionStorage.getItem(`stream_seq_${sid}`)).toBeNull()
    expect(useSessionsStore.getState().sessions[sid].meta.status).toBe('idle')
  })

  it('record 不存在 → no-op', async () => {
    useSessionsStore.setState({ sessions: {} })
    await expect(
      useSessionsStore.getState().handleMessagesTruncated(sid, truncatedEvent()),
    ).resolves.toBeUndefined()
    expect(getSessionMessages).not.toHaveBeenCalled()
  })
})

// ── truncateTimeline — D11 状态机与互斥（7.4）──────────────────────

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}

describe('truncateTimeline — D11 状态机与互斥', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('状态序列：cutting（请求在途）→ idle（收敛完成）', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const truncDeferred = deferred<TruncateResponse>()
    vi.mocked(truncateMessages).mockReturnValueOnce(truncDeferred.promise)
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...remainingHistory], page_meta: null })

    const promise = useSessionsStore.getState().truncateTimeline(sid, 'msg_2', 'a1')
    await Promise.resolve()
    expect(useSessionsStore.getState().sessions[sid].truncateState).toBe('cutting')

    truncDeferred.resolve(truncateOk)
    await promise
    expect(useSessionsStore.getState().sessions[sid].truncateState ?? 'idle').toBe('idle')
  })

  it('E8：cutting 期间 prependHistoryPage 互斥 no-op', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    // 分页窗口会话（prepend 前置条件齐备）
    const record = useSessionsStore.getState().sessions[sid]
    useSessionsStore.setState({
      sessions: {
        [sid]: { ...record!, historyPaging: { hasMore: true, nextBefore: 2, anchorRunId: 'run-2', runsVersion: null, fetchingOlder: false } },
      },
    })
    vi.mocked(truncateMessages).mockRejectedValue(netError())

    const promise = useSessionsStore.getState().truncateTimeline(sid, 'msg_2', 'a1')
    const expectation = expect(promise).rejects.toThrow('network down')
    await vi.advanceTimersByTimeAsync(0)  // 首次失败，进入退避窗口（cutting）
    expect(useSessionsStore.getState().sessions[sid].truncateState).toBe('cutting')

    const ok = await useSessionsStore.getState().prependHistoryPage(sid)
    expect(ok).toBe(false)  // cutting 互斥

    await vi.advanceTimersByTimeAsync(500)
    await vi.advanceTimersByTimeAsync(2000)
    await expectation
  })

  it('外部广播 confirming → truncateState 置 idle（bubble 关 modal 信号）', async () => {
    // paged 会话：外部收敛走 refreshTail
    const record = useSessionsStore.getState().sessions[sid]
    useSessionsStore.setState({
      sessions: {
        [sid]: { ...record!, historyPaging: { hasMore: true, nextBefore: 2, anchorRunId: 'run-2', runsVersion: null, fetchingOlder: false } },
      },
    })
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...remainingHistory], page_meta: null })
    useSessionsStore.getState().setTruncateState(sid, 'confirming')

    await useSessionsStore.getState().handleMessagesTruncated(sid, truncatedEvent({ request_id: 'other-tab' }))

    expect(useSessionsStore.getState().sessions[sid].truncateState ?? 'idle').toBe('idle')
    expect(getSessionMessagesTail).toHaveBeenCalledTimes(1)  // 外部收敛
  })

  it('外部广播 during cutting → 置 idle 且 R1 不回插（外部收敛即权威）', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    // paged 会话：外部收敛走 refreshTail
    const record = useSessionsStore.getState().sessions[sid]
    useSessionsStore.setState({
      sessions: {
        [sid]: { ...record!, historyPaging: { hasMore: true, nextBefore: 2, anchorRunId: 'run-2', runsVersion: null, fetchingOlder: false } },
      },
    })
    // 我方 truncate 全部网络失败（重试耗尽 → R1 分支）
    vi.mocked(truncateMessages).mockRejectedValue(netError())
    // 外部广播触发 refreshTail 收敛为 2 条
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...remainingHistory], page_meta: null })

    const promise = useSessionsStore.getState().truncateTimeline(sid, 'msg_2', 'a1')
    const expectation = expect(promise).rejects.toThrow('network down')
    await vi.advanceTimersByTimeAsync(0)  // 首次失败，进入 500ms 退避

    // 另一标签的截断广播（request_id 非本会话在途）→ 收敛 + 置 idle
    await useSessionsStore.getState().handleMessagesTruncated(sid, truncatedEvent({ request_id: 'other-tab' }))

    await vi.advanceTimersByTimeAsync(500)
    await vi.advanceTimersByTimeAsync(2000)
    await expectation

    // R1 跳过回插：视图保持外部收敛结果（2 条），而非回插成 4 条
    expect(useSessionsStore.getState().sessions[sid].timeline).toHaveLength(2)
  })

  it('fix-resend-from-here D2：窗口首位（idx===0）也乐观切', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const truncDeferred = deferred<TruncateResponse>()
    vi.mocked(truncateMessages).mockReturnValueOnce(truncDeferred.promise)
    vi.mocked(getSessionMessagesTail).mockResolvedValue({ session_id: sid, messages: [...remainingHistory], page_meta: null })

    const promise = useSessionsStore.getState().truncateTimeline(sid, 'msg_0', 'u0')
    await Promise.resolve()
    // 确认即切：首位卡此前被 idx>0 守卫静默跳过（"等后端才删"成因）
    expect(useSessionsStore.getState().sessions[sid].timeline).toHaveLength(0)
    expect(useSessionsStore.getState().sessions[sid].truncateState).toBe('cutting')

    truncDeferred.resolve(truncateOk)
    await promise
    expect(useSessionsStore.getState().sessions[sid].timeline).toHaveLength(2)
    expect(useSessionsStore.getState().sessions[sid].truncateState ?? 'idle').toBe('idle')
  })

  it('fix-resend-from-here D1/2.3：id 延迟解析失败 → R1 原位回插 + idle + 不发 truncate', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    await expect(
      useSessionsStore.getState().truncateTimeline(sid, undefined, 'a1', () => Promise.resolve(null)),
    ).rejects.toThrow('无法定位消息')

    expect(truncateMessages).not.toHaveBeenCalled()
    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline.map(t => t.id)).toEqual(['u0', 'a0', 'u1', 'a1'])
    expect(record.truncateState ?? 'idle').toBe('idle')
  })
})

// ── streamPool 前置拦截（不进 appendEvent switch）────────────────

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  static last: FakeWebSocket | null = null

  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  onerror: (() => void) | null = null

  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.last = this
  }

  close() { this.readyState = 3 }
}

describe('streamPool — messages_truncated 前置拦截', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
    streamPool.closeAll()
  })

  afterEach(() => {
    streamPool.closeAll()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('拦截后分派 handleMessagesTruncated，不进 appendEvent、不断连', async () => {
    const dispatched: MessagesTruncatedEvent[] = []
    useSessionsStore.setState({
      handleMessagesTruncated: async (_s, payload) => { dispatched.push(payload) },
    })
    const appendSpy = vi.fn()
    useSessionsStore.setState({ appendEvent: appendSpy })

    streamPool.ensureConnection(sid, 0)
    FakeWebSocket.last!.onopen?.()

    const payload = truncatedEvent({ request_id: 'other-tab-rid', seq_id: 1 })
    FakeWebSocket.last!.onmessage?.({ data: JSON.stringify(payload) })
    await Promise.resolve()

    expect(dispatched).toHaveLength(1)
    expect(dispatched[0].request_id).toBe('other-tab-rid')
    // 不进 appendEvent switch（非 timeline 事件）
    expect(appendSpy).not.toHaveBeenCalled()
    // 与 session_idle 不同：连接保持（D12.7 不做 WS 重连）
    expect(streamPool.isConnected(sid)).toBe(true)
  })
})
