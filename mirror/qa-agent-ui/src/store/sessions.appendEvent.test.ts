/// <reference types="vitest/globals" />
import { useSessionsStore, isAborting, type SessionMeta } from '@/store/sessions'
import type { NormalizedEvent } from '@/types/events'

// ── Helpers ────────────────────────────────────────────────────────

function makeMeta(overrides: Partial<SessionMeta> = {}): SessionMeta {
  return {
    session_id: 'sid-test',
    agent_name: 'issue-testcase',
    mode: 'normal',
    status: 'running',
    created_at: Date.now(),
    ...overrides,
  }
}

function runErrorEvent(error: string): NormalizedEvent {
  return {
    event_type: 'run_error',
    session_id: 'sid-test',
    agent_name: 'issue-testcase',
    error,
  } as NormalizedEvent
}

// ── Live event path: appendEvent(run_error) → timeline + meta ──────

describe('sessions store appendEvent — run_error live path', () => {
  beforeEach(() => {
    useSessionsStore.setState({
      sessions: {},
      activeSessionId: null,
      draftContent: {},
    })
    useSessionsStore.getState().addSession(makeMeta())
  })

  it('pushes RunErrorItem with error text into timeline', () => {
    useSessionsStore.getState().appendEvent('sid-test', runErrorEvent('LLM 响应内容疑似上游限流错误'))

    const record = useSessionsStore.getState().sessions['sid-test']
    const last = record.timeline[record.timeline.length - 1]
    expect(last).toMatchObject({ type: 'run_error', error: 'LLM 响应内容疑似上游限流错误' })
  })

  it('sets meta status=failed and meta.error', () => {
    useSessionsStore.getState().appendEvent('sid-test', runErrorEvent('boom'))

    const meta = useSessionsStore.getState().sessions['sid-test'].meta
    expect(meta.status).toBe('failed')
    expect(meta.error).toBe('boom')
  })
})

// ── run_aborting 过渡态（fix-abort-latency spec 抽查）──────────────
// 覆盖:瞬态标记不改写 status / 冻结流式渲染 / 乐观+WS 双来源幂等 /
// 终态覆盖清除 / abort-complete 竞态收敛。

describe('sessions store appendEvent — run_aborting 过渡态', () => {
  beforeEach(() => {
    useSessionsStore.setState({
      sessions: {},
      activeSessionId: null,
      draftContent: {},
    })
    useSessionsStore.getState().addSession(makeMeta())
  })

  function abortingEvent(): NormalizedEvent {
    return {
      event_type: 'run_aborting',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
    } as NormalizedEvent
  }

  function tokenEvent(content: string): NormalizedEvent {
    return {
      event_type: 'token',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
      content,
    } as NormalizedEvent
  }

  it('冻结流式渲染并挂过渡标记,meta.status 保持 running（不改写后端真值）', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', tokenEvent('正在输出'))
    store.appendEvent('sid-test', abortingEvent())

    const record = useSessionsStore.getState().sessions['sid-test']
    expect(record.timeline[0]).toMatchObject({ type: 'agent_message', isStreaming: false })
    expect(record.timeline[record.timeline.length - 1]).toMatchObject({ type: 'run_aborting' })
    expect(record.meta.status).toBe('running')
    expect(isAborting(record)).toBe(true)
  })

  it('幂等:重复 run_aborting（乐观 dispatch + WS 双来源）只保留一个标记', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', abortingEvent())
    store.appendEvent('sid-test', abortingEvent())

    const timeline = useSessionsStore.getState().sessions['sid-test'].timeline
    expect(timeline.filter(t => t.type === 'run_aborting')).toHaveLength(1)
  })

  it('run_aborted 到达 → 清除过渡标记并收敛 aborted', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', tokenEvent('输出'))
    store.appendEvent('sid-test', abortingEvent())
    store.appendEvent('sid-test', {
      event_type: 'run_aborted',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
    } as NormalizedEvent)

    const record = useSessionsStore.getState().sessions['sid-test']
    expect(record.timeline.some(t => t.type === 'run_aborting')).toBe(false)
    expect(record.timeline[record.timeline.length - 1]).toMatchObject({ type: 'run_aborted' })
    expect(record.meta.status).toBe('aborted')
    expect(isAborting(record)).toBe(false)
  })

  it('竞态:run_aborting 后 run 正常完成 → 收敛 completed,不留中断态', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', abortingEvent())
    store.appendEvent('sid-test', {
      event_type: 'run_complete',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
      final_response: '正常完成',
      fs_id: '14802',
    } as NormalizedEvent)

    const record = useSessionsStore.getState().sessions['sid-test']
    expect(record.timeline.some(t => t.type === 'run_aborting')).toBe(false)
    expect(record.meta.status).toBe('completed')
  })

  it('新 run 起来（user 重发/恢复）→ 清除残留过渡标记', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', abortingEvent())
    store.appendEvent('sid-test', {
      event_type: 'run_started',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
    } as NormalizedEvent)

    const record = useSessionsStore.getState().sessions['sid-test']
    expect(record.timeline.some(t => t.type === 'run_aborting')).toBe(false)
    expect(record.meta.status).toBe('running')
  })

  it('markAborting 乐观入口等同 WS 事件（弱网兜底）', () => {
    useSessionsStore.getState().markAborting('sid-test', 'issue-testcase')

    const record = useSessionsStore.getState().sessions['sid-test']
    expect(record.timeline[record.timeline.length - 1]).toMatchObject({
      type: 'run_aborting',
      agent_name: 'issue-testcase',
    })
    expect(isAborting(record)).toBe(true)
  })
})

// ── reasoning_delta 思考块（add-thinking-stream-display spec 抽查）───
// 覆盖:增量合并进流式思考块 / token 到达 settle / 多段独立成块 /
// run_started 界定新段 / 回放恢复(带 seq_id 重放)。

describe('sessions store appendEvent — reasoning_delta 思考块', () => {
  beforeEach(() => {
    useSessionsStore.setState({
      sessions: {},
      activeSessionId: null,
      draftContent: {},
    })
    useSessionsStore.getState().addSession(makeMeta())
  })

  function reasoningEvent(content: string, seqId?: number): NormalizedEvent {
    return {
      event_type: 'reasoning_delta',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
      content,
      ...(seqId !== undefined ? { seq_id: seqId } : {}),
    } as NormalizedEvent
  }

  function tokenEvent(content: string): NormalizedEvent {
    return {
      event_type: 'token',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
      content,
    } as NormalizedEvent
  }

  function toolStartEvent(): NormalizedEvent {
    return {
      event_type: 'tool_start',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
      tool_name: 'grep',
      tool_call_id: 'tc-1',
      inputs: {},
    } as NormalizedEvent
  }

  it('连续 reasoning_delta 合并进同一流式思考块', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', reasoningEvent('思考A'))
    store.appendEvent('sid-test', reasoningEvent('思考B'))

    const timeline = useSessionsStore.getState().sessions['sid-test'].timeline
    const thinking = timeline.filter(t => t.type === 'agent_thinking')
    expect(thinking).toHaveLength(1)
    expect(thinking[0]).toMatchObject({ type: 'agent_thinking', content: '思考A思考B', isStreaming: true })
  })

  it('token 到达 → 思考块 settle(isStreaming=false, endedAt 就绪),正文气泡独立', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', reasoningEvent('思考'))
    store.appendEvent('sid-test', tokenEvent('正文'))

    const timeline = useSessionsStore.getState().sessions['sid-test'].timeline
    expect(timeline).toHaveLength(2)
    expect(timeline[0]).toMatchObject({ type: 'agent_thinking', isStreaming: false })
    expect((timeline[0] as { endedAt: number | null }).endedAt).toBeGreaterThan(0)
    expect(timeline[1]).toMatchObject({ type: 'agent_message', content: '正文', isStreaming: true })
  })

  it('思考→输出→再思考:多段独立成块,互不合并', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', reasoningEvent('第一段思考'))
    store.appendEvent('sid-test', tokenEvent('正文'))
    store.appendEvent('sid-test', reasoningEvent('第二段思考'))

    const timeline = useSessionsStore.getState().sessions['sid-test'].timeline
    const thinking = timeline.filter(t => t.type === 'agent_thinking')
    expect(thinking).toHaveLength(2)
    expect(thinking[0]).toMatchObject({ content: '第一段思考', isStreaming: false })
    expect(thinking[1]).toMatchObject({ content: '第二段思考', isStreaming: true })
  })

  it('tool_start 到达 → 流式思考块 settle', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', reasoningEvent('思考'))
    store.appendEvent('sid-test', toolStartEvent())

    const timeline = useSessionsStore.getState().sessions['sid-test'].timeline
    const thinking = timeline.find(t => t.type === 'agent_thinking')
    expect(thinking).toMatchObject({ isStreaming: false })
  })

  it('run_started 界定新段:残留流式思考块 settle,新段思考新块', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', reasoningEvent('旧段思考'))
    store.appendEvent('sid-test', {
      event_type: 'run_started',
      session_id: 'sid-test',
      agent_name: 'issue-testcase',
    } as NormalizedEvent)
    store.appendEvent('sid-test', reasoningEvent('新段思考'))

    const timeline = useSessionsStore.getState().sessions['sid-test'].timeline
    const thinking = timeline.filter(t => t.type === 'agent_thinking')
    expect(thinking).toHaveLength(2)
    expect(thinking[0]).toMatchObject({ content: '旧段思考', isStreaming: false })
    expect(thinking[1]).toMatchObject({ content: '新段思考', isStreaming: true })
  })

  it('回放恢复:带 seq_id 的 reasoning_delta 重放重建思考块(刷新场景)', () => {
    const store = useSessionsStore.getState()
    store.appendEvent('sid-test', reasoningEvent('回放思考A', 1))
    store.appendEvent('sid-test', reasoningEvent('回放思考B', 2))
    store.appendEvent('sid-test', tokenEvent('回放正文'))

    const timeline = useSessionsStore.getState().sessions['sid-test'].timeline
    const thinking = timeline.find(t => t.type === 'agent_thinking')
    expect(thinking).toMatchObject({ content: '回放思考A回放思考B', isStreaming: false })
  })
})
