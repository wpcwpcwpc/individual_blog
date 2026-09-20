/**
 * 执行追踪单行（从 TraceTable 抽取，add-trace-list-run-grouping）。
 *
 * 两种使用形态：
 *  - 普通单行（单段组 / 缺字段历史记录）— 视觉与抽取前完全一致
 *  - 组内段行 — 通过 segmentIndex/segmentTotal 显示「段 N/总数」徽标
 */

import dayjs from 'dayjs'
import type { Trace } from '@/api/tracing'
import { SpanWaterfall } from './SpanWaterfall'

export const statusColors: Record<string, string> = {
  OK: '#22c55e',
  ERROR: '#ef4444',
  UNSET: '#94a3b8',
}

// Display the operator username. Callers that authenticate with an email
// (or an email-derived user id) get the local part before @ as the username.
// Unauthenticated / AgentOS calls omit user_id — default to "qa-agent".
export function formatUser(userId?: string | null): string {
  if (!userId) return 'qa-agent'
  if (userId.includes('@')) return userId.split('@')[0]
  return userId
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

export interface TraceRowProps {
  trace: Trace
  expanded: boolean
  onToggle: () => void
  /** 组内序号（1-based）；单行不传则不渲染段徽标 */
  segmentIndex?: number
  segmentTotal?: number
}

export function TraceRow({ trace, expanded, onToggle, segmentIndex, segmentTotal }: TraceRowProps) {
  const showSegmentBadge = segmentIndex != null && segmentTotal != null

  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer transition-colors"
        style={{ borderBottom: '1px solid var(--border-color)' }}
        onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--surface-deep)')}
        onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
      >
        <td className="px-3 py-2 truncate max-w-[200px]" style={{ color: 'var(--text-primary)' }}>
          {trace.name || trace.trace_id.slice(0, 8)}
          {showSegmentBadge && (
            <span
              className="inline-block ml-1.5 px-1 py-0.5 rounded text-[10px] font-medium align-middle"
              style={{ color: 'var(--text-muted)', border: '1px solid var(--border-color)' }}
            >
              段 {segmentIndex}/{segmentTotal}
            </span>
          )}
        </td>
        <td className="px-3 py-2 truncate max-w-[140px]" style={{ color: 'var(--text-secondary)' }}>
          {formatUser(trace.user_id)}
        </td>
        <td className="px-3 py-2">
          <span
            className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium"
            style={{ backgroundColor: `${statusColors[trace.status] || '#64748b'}20`, color: statusColors[trace.status] || '#64748b' }}
          >
            {trace.status}
          </span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>
          {formatDuration(trace.duration_ms)}
        </td>
        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {trace.token_total != null ? trace.token_total.toLocaleString() : '-'}
          {/* add-tracing-cache-token-display：缓存命中次要标注；
              字段缺失（历史 trace）显示 —，存在且为 0 显示 0 */}
          {trace.token_total != null && (
            <div className="text-[10px]">
              缓存 {trace.token_cache_read != null ? trace.token_cache_read.toLocaleString() : '—'}
            </div>
          )}
        </td>
        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {trace.total_spans}
        </td>
        <td className="px-3 py-2 text-right" style={{ color: 'var(--text-muted)' }}>
          {dayjs(trace.start_time).format('MM-DD HH:mm')}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} className="p-0">
            <SpanWaterfall traceId={trace.trace_id} traceDurationMs={trace.duration_ms} traceStartTime={trace.start_time} />
          </td>
        </tr>
      )}
    </>
  )
}
