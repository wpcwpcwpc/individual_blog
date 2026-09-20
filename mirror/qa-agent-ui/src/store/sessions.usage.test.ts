/// <reference types="vitest/globals" />
import * as sessionsApi from '@/api/sessions'
import { useSessionsStore, type SessionMeta } from '@/store/sessions'
import type { RunCompleteEvent, CompressionEvent } from '@/types/events'

// 既有用例经 setState 替换 refreshContextUsage action（zustand 单例污染），
// 模块加载时先捕获真实实现供 404 分支用例使用。
const realRefreshContextUsage = useSessionsStore.getState().refreshContextUsage

// ── Fixtures ──────────────────────────────────────────────────────

const sid = 'sid-usage'

function seedSession() {
  const meta: SessionMeta = {
    session_id: sid,
    agent_name: 'QAAgent',
    mode: 'normal',
    status: 'idle',
    created_at: Date.now(),
  }
  useSessionsStore.setState({
    sessions: { [sid]: { meta, timeline: [], interrupt: null, _activeAgentName: null, loadStatus: 'idle' as const, lastActivatedSkills: [], lastSeqId: 0, hasUnreadInterrupt: false } },
    sessionUsage: {},
  })
}

function runCompleteEvent(usage: RunCompleteEvent['usage'], agentName = 'QAAgent'): RunCompleteEvent {
  return {
    event_type: 'run_complete',
    session_id: sid,
    agent_name: agentName,
    final_response: 'done',
    usage,
  }
}

const usage = { input_tokens: 1000, output_tokens: 500, total_tokens: 1500, cache_read_tokens: 200 }

// ── Tests ─────────────────────────────────────────────────────────

describe('sessionUsage（add-context-usage-visibility）', () => {
  beforeEach(() => {
    seedSession()
    vi.restoreAllMocks()
  })

  it('run_complete 带 usage → byAgent 累加，并触发静默 refresh', async () => {
    const refreshSpy = vi.fn().mockResolvedValue(undefined)
    // 拦截 refreshContextUsage：只验证被触发，不发真实请求
    vi.spyOn(useSessionsStore.getState(), 'refreshContextUsage').mockImplementation(refreshSpy)
    useSessionsStore.setState({ refreshContextUsage: refreshSpy })

    useSessionsStore.getState().appendEvent(sid, runCompleteEvent(usage))
    useSessionsStore.getState().appendEvent(sid, runCompleteEvent({ ...usage, total_tokens: 2000, input_tokens: 1200 }))

    const agg = useSessionsStore.getState().sessionUsage[sid]?.byAgent['QAAgent']
    expect(agg).toBeDefined()
    expect(agg!.runs).toBe(2)
    expect(agg!.input_tokens).toBe(2200)
    expect(agg!.total_tokens).toBe(3500)

    // microtask 后 refresh 被触发（每轮一次）
    await Promise.resolve()
    expect(refreshSpy.mock.calls.filter(c => c[0] === sid).length).toBe(2)
  })

  it('run_complete 不带 usage（上游缺失）→ 不累加不刷新', () => {
    const refreshSpy = vi.fn().mockResolvedValue(undefined)
    useSessionsStore.setState({ refreshContextUsage: refreshSpy })

    useSessionsStore.getState().appendEvent(sid, runCompleteEvent(null))

    expect(useSessionsStore.getState().sessionUsage[sid]).toBeUndefined()
    expect(refreshSpy).not.toHaveBeenCalled()
  })

  it('compression 事件 → compressed 标志置位（幂等），breakdown/byAgent 保留', () => {
    useSessionsStore.setState({
      sessionUsage: { [sid]: { byAgent: { QAAgent: { input_tokens: 1, output_tokens: 1, total_tokens: 2, runs: 1 } }, breakdown: null, compressed: false } },
    })

    const evt: CompressionEvent = {
      event_type: 'compression',
      session_id: sid,
      stage: 'completed',
      tool_results_compressed: 3,
    }
    useSessionsStore.getState().appendEvent(sid, evt)
    useSessionsStore.getState().appendEvent(sid, evt)

    const st = useSessionsStore.getState().sessionUsage[sid]
    expect(st?.compressed).toBe(true)
    expect(st?.byAgent['QAAgent'].runs).toBe(1)
  })

  it('多 agent：按 agent_name 分组累加', () => {
    const refreshSpy = vi.fn().mockResolvedValue(undefined)
    useSessionsStore.setState({ refreshContextUsage: refreshSpy })

    useSessionsStore.getState().appendEvent(sid, runCompleteEvent(usage, 'Coordinator'))
    useSessionsStore.getState().appendEvent(sid, runCompleteEvent(usage, 'WorkerA'))

    const byAgent = useSessionsStore.getState().sessionUsage[sid]?.byAgent
    expect(Object.keys(byAgent!)).toEqual(expect.arrayContaining(['Coordinator', 'WorkerA']))
    expect(byAgent!['Coordinator'].runs).toBe(1)
    expect(byAgent!['WorkerA'].runs).toBe(1)
  })

  it('removeSession 级联清理 sessionUsage', () => {
    useSessionsStore.setState({
      sessionUsage: { [sid]: { byAgent: {}, breakdown: null, compressed: false } },
    })
    useSessionsStore.getState().removeSession(sid)
    expect(useSessionsStore.getState().sessionUsage[sid]).toBeUndefined()
  })

  it('refreshContextUsage 404 静默 / 非 404 保留告警（harden-session-history-paging）', async () => {
    seedSession()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const getSpy = vi.spyOn(sessionsApi, 'getContextUsage')

    // 404 = 新会话未落库 / 已删会话（设计内降级）→ 静默
    getSpy.mockRejectedValueOnce(Object.assign(new Error('Request failed with status 404'), { response: { status: 404 } }))
    await realRefreshContextUsage(sid)
    expect(warnSpy).not.toHaveBeenCalled()

    // 真实故障（500）→ 保留 warn
    getSpy.mockRejectedValueOnce(Object.assign(new Error('Request failed with status 500'), { response: { status: 500 } }))
    await realRefreshContextUsage(sid)
    expect(warnSpy).toHaveBeenCalledTimes(1)
  })
})
