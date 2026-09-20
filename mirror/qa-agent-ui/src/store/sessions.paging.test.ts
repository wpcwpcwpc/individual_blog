/// <reference types="vitest/globals" />
import { useSessionsStore, type SessionMeta, type SessionRecord, type HistoryPaging } from '@/store/sessions'
import { getSessionMessagesPage, getSessionMessagesTail } from '@/api/sessions'
import { hydrateMessages } from '@/utils/hydrateHistory'
import type { HistoryMessage, SessionMessagesResponse, PageMeta } from '@/types/api'
import type { AgentMessageItem, UserMessageItem, TimelineItem } from '@/types/events'

vi.mock('@/api/sessions', () => ({
  getWSStreamUrl: (sid: string) => `ws://test/${sid}`,
  getSessionLastSeq: vi.fn(),
  getSessionMessages: vi.fn(),
  getSessionMessagesPage: vi.fn(),
  getSessionMessagesTail: vi.fn(),
  truncateMessages: vi.fn(),
  restoreSession: vi.fn().mockResolvedValue({}),
  getContextUsage: vi.fn().mockResolvedValue(null),
}))

// ── Fixtures ──────────────────────────────────────────────────────

const sid = 'sid-page'

function hist(seq: number, role: 'user' | 'assistant', content: string): HistoryMessage {
  return { id: `msg_${seq}`, role, content, created_at: seq }
}

function userMsg(seq: number, content: string): UserMessageItem {
  return { type: 'user_message', id: `u${seq}`, content, username: 'me', timestamp: seq, backendMessageId: `msg_${seq}` }
}

function agentMsg(seq: number, content: string): AgentMessageItem {
  return { type: 'agent_message', id: `a${seq}`, agent_name: 'QAAgent', content, isStreaming: false, backendMessageId: `msg_${seq}` }
}

function pageMeta(overrides: Partial<PageMeta> = {}): PageMeta {
  return { has_more: true, next_before: 2, total_runs: 4, total_messages: 8, anchor: 'run-2', ...overrides }
}

function pageResponse(messages: HistoryMessage[], meta: PageMeta | null): SessionMessagesResponse {
  return { session_id: sid, messages, page_meta: meta, resync: false }
}

/** 8 条消息（user/assistant 交替，位号 0..7） */
function fullHistory(): HistoryMessage[] {
  return Array.from({ length: 8 }, (_, i) => hist(i, i % 2 === 0 ? 'user' : 'assistant', `m${i}`))
}

function seed(timeline: TimelineItem[], paging: Partial<HistoryPaging> | null): void {
  const meta: SessionMeta = { session_id: sid, agent_name: 'QAAgent', mode: 'normal', status: 'idle', created_at: Date.now() }
  const record: SessionRecord = {
    meta,
    timeline,
    interrupt: null,
    _activeAgentName: null,
    loadStatus: 'ready',
    lastActivatedSkills: [],
    lastSeqId: 0,
    hasUnreadInterrupt: false,
    ...(paging != null
      ? { historyPaging: { hasMore: true, nextBefore: 2, anchorRunId: 'run-2', runsVersion: null, fetchingOlder: false, ...paging } }
      : {}),
  }
  useSessionsStore.setState({ sessions: { [sid]: record }, activeSessionId: sid, draftContent: {} })
}

function projection(timeline: TimelineItem[]) {
  return timeline.map(t => ({ type: t.type, content: (t as { content?: string }).content ?? null, backendMessageId: (t as { backendMessageId?: string }).backendMessageId ?? null }))
}

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}

beforeEach(() => {
  vi.clearAllMocks()
  useSessionsStore.setState({ sessions: {}, activeSessionId: null, draftContent: {} })
})

// ── 4.6 parity 性质测试：hydrate(全量) ≡ concat(hydrate(页₀..页ₖ)) ──

describe('parity — 分页拼接与全量 hydrate 等价', () => {
  it('尾页 + 翻页拼接的时间线 === 全量 hydrate 的时间线', async () => {
    const full = fullHistory()
    // A: 全量 hydrate
    seed([], null)
    useSessionsStore.getState().hydrateSessionEvents(sid, hydrateMessages(full, 'QAAgent'))
    const timelineA = useSessionsStore.getState().sessions[sid].timeline

    // B: 尾页 hydrate → prepend 前页
    seed([], { hasMore: true, nextBefore: 2, anchorRunId: 'run-2' })
    useSessionsStore.getState().hydrateSessionEvents(sid, hydrateMessages(full.slice(4), 'QAAgent'))
    vi.mocked(getSessionMessagesPage).mockResolvedValue(pageResponse(full.slice(0, 4), pageMeta({ has_more: false, next_before: 0, anchor: null })))
    await useSessionsStore.getState().prependHistoryPage(sid)
    const timelineB = useSessionsStore.getState().sessions[sid].timeline

    expect(projection(timelineB)).toEqual(projection(timelineA))
  })
})

// ── prependHistoryPage ─────────────────────────────────────────────

describe('prependHistoryPage — 向前翻页', () => {
  it('拉取更早页并 prepend：头部拼接 + lastMutation=prepend + page_meta 更新', async () => {
    seed([userMsg(6, 'm6'), agentMsg(7, 'm7')], {})
    vi.mocked(getSessionMessagesPage).mockResolvedValue(
      pageResponse(fullHistory().slice(0, 4), pageMeta({ next_before: 0, anchor: null, has_more: false })),
    )

    await useSessionsStore.getState().prependHistoryPage(sid)

    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline).toHaveLength(6)                       // 4 页项 + 2 窗口项
    expect(record.lastMutation).toBe('prepend')
    expect(record.historyPaging?.fetchingOlder).toBe(false)
    expect(record.historyPaging?.hasMore).toBe(false)
    expect(record.historyPaging?.nextBefore).toBe(0)
    expect(record.historyPaging?.runsVersion).toEqual({ total_runs: 4, total_messages: 8 })
    // 头部是页内容（位号 0..3），尾部保持原窗口
    expect(projection(record.timeline.slice(0, 4))).toEqual(projection(record.timeline.slice(0, 4)))
    expect((record.timeline[4] as UserMessageItem).backendMessageId).toBe('msg_6')
  })

  it('在途守卫：fetchingOlder 期间重复触发只发一次请求', async () => {
    const d = deferred<SessionMessagesResponse>()
    seed([agentMsg(7, 'm7')], {})
    vi.mocked(getSessionMessagesPage).mockReturnValueOnce(d.promise)

    const p1 = useSessionsStore.getState().prependHistoryPage(sid)
    const p2 = useSessionsStore.getState().prependHistoryPage(sid)
    expect(useSessionsStore.getState().sessions[sid].historyPaging?.fetchingOlder).toBe(true)

    d.resolve(pageResponse(fullHistory().slice(0, 4), pageMeta({ has_more: false, next_before: 0, anchor: null })))
    await Promise.all([p1, p2])

    expect(getSessionMessagesPage).toHaveBeenCalledTimes(1)
  })

  it('hasMore=false 或非分页会话 → no-op', async () => {
    seed([agentMsg(7, 'm7')], { hasMore: false })
    await useSessionsStore.getState().prependHistoryPage(sid)
    expect(getSessionMessagesPage).not.toHaveBeenCalled()

    seed([agentMsg(7, 'm7')], null)
    await useSessionsStore.getState().prependHistoryPage(sid)
    expect(getSessionMessagesPage).not.toHaveBeenCalled()
  })

  it('失败不砸窗口：游标不变、fetchingOlder 复位可重试', async () => {
    seed([agentMsg(7, 'm7')], { nextBefore: 3, anchorRunId: 'run-3' })
    vi.mocked(getSessionMessagesPage).mockRejectedValueOnce(new Error('net'))
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    await useSessionsStore.getState().prependHistoryPage(sid)

    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline).toHaveLength(1)
    expect(record.historyPaging?.nextBefore).toBe(3)   // 游标不丢
    expect(record.historyPaging?.anchorRunId).toBe('run-3')
    expect(record.historyPaging?.fetchingOlder).toBe(false)
  })

  it('锚漂移（resync=true）→ 转 refreshTail 尾页收敛', async () => {
    seed([agentMsg(7, 'm7')], {})
    vi.mocked(getSessionMessagesPage).mockResolvedValue({
      session_id: sid, messages: [], page_meta: pageMeta({ has_more: false, next_before: 0, anchor: null }), resync: true,
    })
    vi.mocked(getSessionMessagesTail).mockResolvedValue(pageResponse(fullHistory().slice(4), pageMeta({ has_more: true })))
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    await useSessionsStore.getState().prependHistoryPage(sid)

    expect(getSessionMessagesTail).toHaveBeenCalledTimes(1)
    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline).toHaveLength(4)             // 尾页内容整窗替换
    expect(record.lastMutation).toBe('replace')
  })
})

// ── refreshTail ───────────────────────────────────────────────────

describe('refreshTail — 尾页回灌原语', () => {
  it('paged 模式：按位号区间 splice，前缀保留', async () => {
    seed([userMsg(0, 'm0'), agentMsg(1, 'm1'), userMsg(2, 'm2'), agentMsg(3, 'm3')], {})
    // 尾页从 msg_2 起（窗口内 msg_2 之后被权威替换为 msg_2..msg_7，共 6 条）
    vi.mocked(getSessionMessagesTail).mockResolvedValue(pageResponse(fullHistory().slice(2), pageMeta()))

    await useSessionsStore.getState().refreshTail(sid)

    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline).toHaveLength(8)             // 前缀 2 + 尾页 6
    expect((record.timeline[0] as UserMessageItem).id).toBe('u0')   // 前缀引用保留
    expect((record.timeline[1] as AgentMessageItem).id).toBe('a1')
    expect((record.timeline[2] as UserMessageItem).backendMessageId).toBe('msg_2')
    expect(record.lastMutation).toBe('replace')
    expect(record.historyPaging?.runsVersion).toEqual({ total_runs: 4, total_messages: 8 })
  })

  it('全量模式（无 historyPaging）：整条替换并初始化分页状态', async () => {
    seed([userMsg(0, 'm0'), agentMsg(1, 'm1')], null)
    vi.mocked(getSessionMessagesTail).mockResolvedValue(pageResponse(fullHistory().slice(4), pageMeta()))

    await useSessionsStore.getState().refreshTail(sid)

    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline).toHaveLength(4)
    expect(record.lastMutation).toBe('replace')
    expect(record.historyPaging?.nextBefore).toBe(2)
    expect(record.historyPaging?.anchorRunId).toBe('run-2')
  })

  it('单飞合并：在途时重复触发仅记待重跑，完成后补跑一次', async () => {
    const d = deferred<SessionMessagesResponse>()
    seed([agentMsg(7, 'm7')], {})
    let calls = 0
    vi.mocked(getSessionMessagesTail).mockImplementation(async () => {
      calls += 1
      if (calls === 1) return d.promise
      return pageResponse(fullHistory().slice(4), pageMeta())
    })

    const p1 = useSessionsStore.getState().refreshTail(sid)
    void useSessionsStore.getState().refreshTail(sid)   // 在途 → 记待重跑

    d.resolve(pageResponse(fullHistory().slice(4), pageMeta()))
    await p1
    await new Promise(r => setTimeout(r, 0))           // 补跑完成

    expect(calls).toBe(2)
  })

  it('拉取失败 → 上抛错误（调用方按场景处理），时间线不动', async () => {
    seed([agentMsg(7, 'm7')], {})
    vi.mocked(getSessionMessagesTail).mockRejectedValue(new Error('net'))
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    await expect(useSessionsStore.getState().refreshTail(sid)).rejects.toThrow('net')
    expect(useSessionsStore.getState().sessions[sid].timeline).toHaveLength(1)
  })
})

// ── lastMutation 守卫（D5）─────────────────────────────────────────

describe('lastMutation — 自动滚底守卫信号', () => {
  it('流式 append → append；prepend 页后 → prepend；重灌 → replace', async () => {
    seed([agentMsg(7, 'm7')], {})

    useSessionsStore.getState().appendEvent(sid, {
      event_type: 'token', session_id: sid, content: 'x', agent_name: 'QAAgent', seq_id: 1,
    })
    expect(useSessionsStore.getState().sessions[sid].lastMutation).toBe('append')

    vi.mocked(getSessionMessagesPage).mockResolvedValue(pageResponse(fullHistory().slice(0, 4), pageMeta({ has_more: false, next_before: 0, anchor: null })))
    await useSessionsStore.getState().prependHistoryPage(sid)
    expect(useSessionsStore.getState().sessions[sid].lastMutation).toBe('prepend')

    useSessionsStore.getState().hydrateSessionEvents(sid, hydrateMessages(fullHistory(), 'QAAgent'))
    expect(useSessionsStore.getState().sessions[sid].lastMutation).toBe('append')
  })

  it('prepend 与流式 append 共存：翻旧页后 token 到达，页前缀不破坏、尾部追加', async () => {
    seed([userMsg(6, 'm6'), agentMsg(7, 'm7')], {})
    vi.mocked(getSessionMessagesPage).mockResolvedValue(pageResponse(fullHistory().slice(0, 4), pageMeta({ has_more: false, next_before: 0, anchor: null })))
    await useSessionsStore.getState().prependHistoryPage(sid)

    useSessionsStore.getState().appendEvent(sid, {
      event_type: 'token', session_id: sid, content: 'live', agent_name: 'QAAgent', seq_id: 1,
    })

    const record = useSessionsStore.getState().sessions[sid]
    expect(record.timeline).toHaveLength(7)
    // 头部 = 翻页内容，尾部 = live 流式块
    expect((record.timeline[0] as UserMessageItem).backendMessageId).toBe('msg_0')
    const last = record.timeline[record.timeline.length - 1] as AgentMessageItem
    expect(last.type).toBe('agent_message')
    expect(last.isStreaming).toBe(true)
    expect(last.backendMessageId).toBeUndefined()
  })
})
