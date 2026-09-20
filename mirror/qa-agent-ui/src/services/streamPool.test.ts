/// <reference types="vitest/globals" />
import { useSessionsStore, type SessionMeta } from '@/store/sessions'
import { streamPool } from '@/services/streamPool'
import { getSessionLastSeq, getSessionMessagesTail } from '@/api/sessions'

vi.mock('@/api/sessions', () => ({
  getWSStreamUrl: (sid: string) => `ws://test/${sid}`,
  getSessionLastSeq: vi.fn(),
  getSessionMessagesTail: vi.fn(),
}))

// ── Fake WebSocket（jsdom 无原生实现）──────────────────────────
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

  close() {
    this.readyState = 3
  }
}

function addRunningSession(sid: string) {
  const meta: SessionMeta = {
    session_id: sid,
    agent_name: 'Agent',
    mode: 'normal',
    status: 'running',
    created_at: Date.now(),
  }
  useSessionsStore.getState().addSession(meta)
}

describe('StreamPoolManager onerror 噪声抑制', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
    useSessionsStore.setState({
      sessions: {},
      activeSessionId: null,
      draftContent: {},
    })
    streamPool.closeAll()
  })

  afterEach(() => {
    streamPool.closeAll()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('连接已从池中移除时，onerror 不告警（删除会话关闭 CONNECTING 套接字的场景）', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    addRunningSession('sid-del')

    streamPool.ensureConnection('sid-del', 0)
    const ws = FakeWebSocket.last
    expect(ws).not.toBeNull()

    // 删除会话流程：closeConnection 先移除池条目，再 close 一个
    // CONNECTING 中的套接字 → Chromium 异步派发 error 事件
    streamPool.closeConnection('sid-del')
    ws!.onerror?.()

    expect(warn).not.toHaveBeenCalledWith('[StreamPool] WS error for sid-del')
  })

  it('套接字仍在池中（真实连接错误）时，onerror 保持告警', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    addRunningSession('sid-live')

    streamPool.ensureConnection('sid-live', 0)
    const ws = FakeWebSocket.last
    expect(ws).not.toBeNull()

    ws!.onerror?.()

    expect(warn).toHaveBeenCalledWith('[StreamPool] WS error for sid-live')
  })
})

describe('删除会话后的拨号守卫（WS error 防回归）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
    useSessionsStore.setState({
      sessions: {},
      activeSessionId: null,
      draftContent: {},
    })
    streamPool.closeAll()
  })

  afterEach(() => {
    streamPool.closeAll()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('ensureConnection 对 store 中不存在的会话 no-op（删除后 resume/truncate 背景重连等路径）', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    FakeWebSocket.last = null

    // store 无该会话记录（已被删除）
    streamPool.ensureConnection('sid-gone', 0)

    expect(FakeWebSocket.last).toBeNull()
    expect(streamPool.isConnected('sid-gone')).toBe(false)
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('sid-gone'))
  })

  it('removeSession 级联关闭池内 WS 连接（store 级不变量：记录没了 ⇒ 连接没了）', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    addRunningSession('sid-rm')

    streamPool.ensureConnection('sid-rm', 0)
    expect(streamPool.isConnected('sid-rm')).toBe(true)
    const ws = FakeWebSocket.last
    expect(ws).not.toBeNull()

    useSessionsStore.getState().removeSession('sid-rm')

    expect(streamPool.isConnected('sid-rm')).toBe(false)
    expect(ws!.readyState).toBe(3)
  })
})

describe('staleness probe 自愈回灌（run 终态后时间线恢复）', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
    useSessionsStore.setState({
      sessions: {},
      activeSessionId: null,
      draftContent: {},
    })
    streamPool.closeAll()
  })

  afterEach(() => {
    streamPool.closeAll()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('探针发现 backend 已 completed → 回灌 messages 时间线（含思考块）+ 同步状态', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    addRunningSession('sid-heal')

    // 建立连接（探针要求池中存在 connection）
    streamPool.ensureConnection('sid-heal', 0)
    FakeWebSocket.last!.onopen?.()

    // backend：run 已完成、seq 领先（本地落后 = 错过终态事件）
    vi.mocked(getSessionLastSeq).mockResolvedValue({ session_id: 'sid-heal', last_seq_id: 4665, status: 'completed' })
    // 尾页端点返回权威终态：assistant 消息带 reasoning_content
    vi.mocked(getSessionMessagesTail).mockResolvedValue({
      messages: [
        { id: 'msg_0', role: 'user', content: '生成用例', created_at: 1750000000.0 },
        { id: 'msg_1', role: 'assistant', content: '最终答复', created_at: 1750000001.0, reasoning_content: '完整的思考内容' },
      ],
    } as Awaited<ReturnType<typeof getSessionMessagesTail>>)

    // 触发探针（private 方法，单测直调）
    await (streamPool as unknown as { probeConnection: (sid: string) => Promise<void> })
      .probeConnection('sid-heal')

    const record = useSessionsStore.getState().sessions['sid-heal']
    expect(record).toBeDefined()
    // 状态同步为 backend 权威值
    expect(record!.meta.status).toBe('completed')
    // 时间线已回灌：思考块（settled）+ 正文，冻结的「思考中」被替换
    const types = record!.timeline.map(t => t.type)
    expect(types).toContain('agent_thinking')
    expect(types).toContain('agent_message')
    const thinking = record!.timeline.find(t => t.type === 'agent_thinking')
    expect(thinking && 'isStreaming' in thinking ? thinking.isStreaming : true).toBe(false)
    // 连接已关闭（run 终态，无需保活）
    expect(streamPool.isConnected('sid-heal')).toBe(false)
  })

  it('回灌失败（messages 接口异常）→ 仍同步状态，不抛出', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    addRunningSession('sid-heal-err')

    streamPool.ensureConnection('sid-heal-err', 0)
    FakeWebSocket.last!.onopen?.()

    vi.mocked(getSessionLastSeq).mockResolvedValue({ session_id: 'sid-heal-err', last_seq_id: 100, status: 'failed' })
    vi.mocked(getSessionMessagesTail).mockRejectedValue(new Error('network down'))

    await (streamPool as unknown as { probeConnection: (sid: string) => Promise<void> })
      .probeConnection('sid-heal-err')

    const record = useSessionsStore.getState().sessions['sid-heal-err']
    expect(record!.meta.status).toBe('failed')
    expect(streamPool.isConnected('sid-heal-err')).toBe(false)
  })
})
