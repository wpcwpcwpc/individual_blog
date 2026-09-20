/// <reference types="vitest/globals" />
import { groupTraces, summarizeGroup } from '@/components/tracing/grouping'
import type { Trace } from '@/api/tracing'

// ── Helpers ────────────────────────────────────────────────────────

function makeTrace(overrides: Partial<Trace> & Pick<Trace, 'trace_id' | 'start_time'>): Trace {
  return {
    name: 'CodingAgent',
    status: 'OK',
    end_time: overrides.start_time,
    duration_ms: 1000,
    total_spans: 3,
    error_count: 0,
    token_total: 500,
    created_at: overrides.start_time,
    ...overrides,
  }
}

const SID = 'sess-0001'
const RID = 'run-0002'

// segments in desc order (newest first) as backend returns
function multiSegmentRun(): Trace[] {
  return [
    makeTrace({ trace_id: 't3', start_time: '2026-09-01T07:20:45Z', session_id: SID, run_id: RID, duration_ms: 3000, token_total: 700 }),
    makeTrace({ trace_id: 't1', start_time: '2026-09-01T06:19:08Z', session_id: SID, run_id: RID, duration_ms: 1000, token_total: 500 }),
    makeTrace({ trace_id: 't2', start_time: '2026-09-01T06:23:39Z', session_id: SID, run_id: RID, duration_ms: 2000, token_total: 600 }),
  ]
}

// ── groupTraces ────────────────────────────────────────────────────

describe('groupTraces — session+run 分组', () => {
  it('多段续跑 run 聚合为一个组且组内按 start_time 升序', () => {
    const groups = groupTraces(multiSegmentRun())

    expect(groups).toHaveLength(1)
    expect(groups[0].isMulti).toBe(true)
    expect(groups[0].sessionId).toBe(SID)
    expect(groups[0].runId).toBe(RID)
    expect(groups[0].segments.map(s => s.trace_id)).toEqual(['t1', 't2', 't3'])
  })

  it('单段记录不成组（isMulti=false，无组 UI 数据形态）', () => {
    const groups = groupTraces([
      makeTrace({ trace_id: 'solo', start_time: '2026-09-01T08:00:00Z', session_id: SID, run_id: 'run-other' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].isMulti).toBe(false)
    expect(groups[0].segments).toHaveLength(1)
  })

  it('缺 run_id 或 session_id 的记录降级为独立单段组', () => {
    const groups = groupTraces([
      makeTrace({ trace_id: 'no-run', start_time: '2026-09-01T08:00:00Z', session_id: SID }),
      makeTrace({ trace_id: 'no-sess', start_time: '2026-09-01T08:01:00Z', run_id: RID }),
      ...multiSegmentRun(),
    ])

    expect(groups).toHaveLength(3)
    const standaloneKeys = groups.filter(g => !g.isMulti).map(g => g.key)
    expect(standaloneKeys).toContain('trace:no-run')
    expect(standaloneKeys).toContain('trace:no-sess')
  })

  it('组位置 = 组内最新段在输入 desc 序中的位置', () => {
    const groups = groupTraces([
      ...multiSegmentRun(), // 组内最新段 t3 在首位 → 组排第一
      makeTrace({ trace_id: 'other', start_time: '2026-09-01T07:15:00Z', session_id: 'sess-x', run_id: 'run-x' }),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].key).toBe(`${SID}|${RID}`)
    expect(groups[1].segments[0].trace_id).toBe('other')
  })

  it('不同 session 下相同 run_id 不合并（双键判定）', () => {
    const groups = groupTraces([
      makeTrace({ trace_id: 'a', start_time: '2026-09-01T07:00:00Z', session_id: 'sess-a', run_id: 'run-same' }),
      makeTrace({ trace_id: 'b', start_time: '2026-09-01T07:01:00Z', session_id: 'sess-b', run_id: 'run-same' }),
    ])

    expect(groups).toHaveLength(2)
  })
})

// ── summarizeGroup ─────────────────────────────────────────────────

describe('summarizeGroup — 组头聚合', () => {
  it('合计耗时 / Token / Spans 为各段之和', () => {
    const [group] = groupTraces(multiSegmentRun())
    const summary = summarizeGroup(group)

    expect(summary.segmentTotal).toBe(3)
    expect(summary.durationMs).toBe(6000)
    expect(summary.tokenTotal).toBe(1800)
    expect(summary.spansTotal).toBe(9)
  })

  it('任一段 ERROR 则组状态为 ERROR，否则取最新段状态', () => {
    const [okGroup] = groupTraces([
      makeTrace({ trace_id: 'e1', start_time: '2026-09-01T07:00:00Z', session_id: SID, run_id: RID, status: 'OK' }),
      makeTrace({ trace_id: 'e2', start_time: '2026-09-01T07:05:00Z', session_id: SID, run_id: RID, status: 'UNSET' }),
    ])
    expect(summarizeGroup(okGroup).status).toBe('UNSET')

    const [errGroup] = groupTraces([
      makeTrace({ trace_id: 'e1', start_time: '2026-09-01T07:00:00Z', session_id: SID, run_id: RID, status: 'ERROR' }),
      makeTrace({ trace_id: 'e2', start_time: '2026-09-01T07:05:00Z', session_id: SID, run_id: RID, status: 'OK' }),
    ])
    expect(summarizeGroup(errGroup).status).toBe('ERROR')
  })

  it('全部段缺失 token_total 时合计为 null', () => {
    const [group] = groupTraces([
      makeTrace({ trace_id: 't1', start_time: '2026-09-01T07:00:00Z', session_id: SID, run_id: RID, token_total: undefined }),
      makeTrace({ trace_id: 't2', start_time: '2026-09-01T07:05:00Z', session_id: SID, run_id: RID, token_total: undefined }),
    ])

    expect(summarizeGroup(group).tokenTotal).toBeNull()
  })

  it('时间范围与用户取首段 start / 最新段', () => {
    const [group] = groupTraces(multiSegmentRun())
    const summary = summarizeGroup(group)

    expect(summary.firstStart).toBe('2026-09-01T06:19:08Z')
    expect(summary.lastStart).toBe('2026-09-01T07:20:45Z')
    expect(summary.latestUserId).toBeUndefined()
  })
})
