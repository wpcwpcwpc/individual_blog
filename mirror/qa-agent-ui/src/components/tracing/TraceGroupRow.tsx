/**
 * 执行追踪多段续跑组行（add-trace-list-run-grouping）。
 *
 * 同一 session_id + run_id 的多条 trace（HITL 多审批 run 的各续跑段）折叠为
 * 一个组：组头为合成聚合行（段数徽标 / run·session 短 id / 状态 / 合计 /
 * 时间范围），展开后渲染带「段 N/总数」徽标的段行（复用 TraceRow）。
 * 上下文连续性见该 change 的 design.md — 分组仅是展示层聚合。
 */

import dayjs from 'dayjs'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { TraceGroup } from './grouping'
import { summarizeGroup } from './grouping'
import { TraceRow, formatDuration, formatUser, statusColors } from './TraceRow'

export interface TraceGroupRowProps {
  group: TraceGroup
  /** 组折叠状态（父级页面瞬态，允许多组同时展开） */
  expanded: boolean
  onToggle: () => void
  /** 段行瀑布图展开 — 沿用列表级单选行为 */
  expandedSpanId: string | null
  onSpanToggle: (traceId: string) => void
}

export function TraceGroupRow({ group, expanded, onToggle, expandedSpanId, onSpanToggle }: TraceGroupRowProps) {
  const summary = summarizeGroup(group)
  const agentName = group.segments[group.segments.length - 1].name
  const shortRun = group.runId?.slice(0, 8) ?? ''
  const shortSession = group.sessionId?.slice(0, 8) ?? ''

  const first = dayjs(summary.firstStart)
  const last = dayjs(summary.lastStart)
  const timeRange = first.isSame(last, 'day')
    ? `${first.format('MM-DD HH:mm')}→${last.format('HH:mm')}`
    : `${first.format('MM-DD HH:mm')}→${last.format('MM-DD HH:mm')}`

  const statusColor = statusColors[summary.status] || '#64748b'

  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer transition-colors"
        style={{ borderBottom: '1px solid var(--border-color)', backgroundColor: 'var(--surface-deep)' }}
        onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--surface)')}
        onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'var(--surface-deep)')}
      >
        <td className="px-3 py-2" style={{ color: 'var(--text-primary)' }}>
          <span className="inline-flex items-center gap-1.5">
            {expanded
              ? <ChevronDown className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--text-muted)' }} />
              : <ChevronRight className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--text-muted)' }} />}
            <span className="truncate max-w-[140px]">{agentName || shortRun}</span>
            <span
              className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium"
              style={{ backgroundColor: 'var(--accent-color)', color: '#fff' }}
            >
              {summary.segmentTotal} 段
            </span>
            <span className="text-[10px] truncate max-w-[140px]" style={{ color: 'var(--text-muted)' }}>
              run {shortRun}·s {shortSession}
            </span>
          </span>
        </td>
        <td className="px-3 py-2 truncate max-w-[140px]" style={{ color: 'var(--text-secondary)' }}>
          {formatUser(summary.latestUserId)}
        </td>
        <td className="px-3 py-2">
          <span
            className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium"
            style={{ backgroundColor: `${statusColor}20`, color: statusColor }}
          >
            {summary.status}
          </span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>
          {formatDuration(summary.durationMs)}
          <span className="ml-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>合计</span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {summary.tokenTotal != null ? summary.tokenTotal.toLocaleString() : '-'}
        </td>
        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {summary.spansTotal}
        </td>
        <td className="px-3 py-2 text-right whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
          {timeRange}
        </td>
      </tr>
      {expanded &&
        group.segments.map((segment, index) => (
          <TraceRow
            key={segment.trace_id}
            trace={segment}
            expanded={expandedSpanId === segment.trace_id}
            onToggle={() => onSpanToggle(segment.trace_id)}
            segmentIndex={index + 1}
            segmentTotal={summary.segmentTotal}
          />
        ))}
    </>
  )
}
