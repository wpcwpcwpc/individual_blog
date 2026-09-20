/**
 * 执行追踪列表按 session+run 分组（add-trace-list-run-grouping）。
 *
 * 背景：HITL 多审批 run 的每段续跑各产生一个 root span（trace_id），平铺渲染时
 * 一个逻辑 run 呈现为 N 行独立记录。本模块纯函数聚合「同一 session_id + run_id」
 * 的段，供 TraceTable 渲染折叠组。
 */

import type { Trace } from '@/api/tracing'

export interface TraceGroup {
  /** 组键：`${session_id}|${run_id}`；缺字段记录退化为 `trace:${trace_id}` */
  key: string
  sessionId?: string
  runId?: string
  /** 组内段按 start_time 升序（审批 → 执行 → 分析因果叙事） */
  segments: Trace[]
  /** 组内多于 1 段才渲染组 UI；单段组视觉零变化 */
  isMulti: boolean
}

export interface TraceGroupSummary {
  segmentTotal: number
  /** 任一段 ERROR → ERROR；否则取最新段状态 */
  status: string
  durationMs: number
  /** 组内各段 token_total 之和；全部缺失时为 null */
  tokenTotal: number | null
  spansTotal: number
  firstStart: string
  lastStart: string
  latestUserId?: string
}

/** 解析 ISO 时间为毫秒；非法值按 0 处理保持稳定排序 */
function timeOf(trace: Trace): number {
  const ms = Date.parse(trace.start_time)
  return Number.isNaN(ms) ? 0 : ms
}

/**
 * 按 `session_id + run_id` 聚合当前页 traces。
 *
 * 组在返回列表中的位置 = 组内最新段在输入序（start_time desc）中的位置
 * （Map 插入序保持输入顺序）；组内段按 start_time 升序重排。
 * 缺 `session_id` 或 `run_id` 的记录各自成「单段组」不参与合并。
 */
export function groupTraces(traces: Trace[]): TraceGroup[] {
  const groups = new Map<string, TraceGroup>()

  for (const trace of traces) {
    if (!trace.session_id || !trace.run_id) {
      const key = `trace:${trace.trace_id}`
      groups.set(key, { key, segments: [trace], isMulti: false })
      continue
    }

    const key = `${trace.session_id}|${trace.run_id}`
    const existing = groups.get(key)
    if (existing) {
      existing.segments.push(trace)
      existing.segments.sort((a, b) => timeOf(a) - timeOf(b))
      existing.isMulti = existing.segments.length > 1
    } else {
      groups.set(key, {
        key,
        sessionId: trace.session_id,
        runId: trace.run_id,
        segments: [trace],
        isMulti: false,
      })
    }
  }

  return Array.from(groups.values())
}

/** 聚合组信息（组头展示：段数 / 状态 / 合计耗时 / Token / Spans / 时间范围）。 */
export function summarizeGroup(group: TraceGroup): TraceGroupSummary {
  const { segments } = group
  const latest = segments[segments.length - 1]

  let durationMs = 0
  let spansTotal = 0
  let tokenSum = 0
  let tokenSeen = false
  for (const trace of segments) {
    durationMs += trace.duration_ms ?? 0
    spansTotal += trace.total_spans ?? 0
    if (trace.token_total != null) {
      tokenSum += trace.token_total
      tokenSeen = true
    }
  }

  return {
    segmentTotal: segments.length,
    status: segments.some(t => t.status === 'ERROR')
      ? 'ERROR'
      : (latest?.status ?? 'UNSET'),
    durationMs,
    tokenTotal: tokenSeen ? tokenSum : null,
    spansTotal,
    firstStart: segments[0]?.start_time ?? '',
    lastStart: latest?.start_time ?? '',
    latestUserId: latest?.user_id,
  }
}
