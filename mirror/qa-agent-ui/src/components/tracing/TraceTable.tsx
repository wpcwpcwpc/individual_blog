/**
 * 执行追踪列表（add-trace-list-run-grouping：按 session+run 分组多段续跑记录）。
 *
 * 渲染规则：
 *  - 多段组（同一 session_id + run_id 的续跑段）→ TraceGroupRow 折叠组
 *  - 单段组 / 缺字段历史记录 → TraceRow 普通单行（视觉零变化）
 * 组折叠状态与段瀑布图展开状态均为页面瞬态（不进 store）。
 */

import { useMemo, useState } from 'react'
import type { Trace } from '@/api/tracing'
import { groupTraces } from './grouping'
import { TraceGroupRow } from './TraceGroupRow'
import { TraceRow } from './TraceRow'

interface Props {
  traces: Trace[]
  loading: boolean
}

export function TraceTable({ traces, loading }: Props) {
  const [expandedSpanId, setExpandedSpanId] = useState<string | null>(null)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())

  const groups = useMemo(() => groupTraces(traces), [traces])

  const toggleGroup = (key: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const toggleSpan = (traceId: string) => {
    setExpandedSpanId(prev => (prev === traceId ? null : traceId))
  }

  if (loading && traces.length === 0) {
    return (
      <div className="flex justify-center py-8">
        <div className="w-5 h-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="rounded-md overflow-hidden" style={{ border: '1px solid var(--border-color)' }}>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ backgroundColor: 'var(--surface-deep)', color: 'var(--text-muted)' }}>
            <th className="text-left px-3 py-2 font-medium">名称</th>
            <th className="text-left px-3 py-2 font-medium">用户</th>
            <th className="text-left px-3 py-2 font-medium">状态</th>
            <th className="text-right px-3 py-2 font-medium">耗时</th>
            <th className="text-right px-3 py-2 font-medium">Token</th>
            <th className="text-right px-3 py-2 font-medium">Spans</th>
            <th className="text-right px-3 py-2 font-medium">时间</th>
          </tr>
        </thead>
        <tbody>
          {groups.map(group => {
            if (group.isMulti) {
              return (
                <TraceGroupRow
                  key={group.key}
                  group={group}
                  expanded={expandedGroups.has(group.key)}
                  onToggle={() => toggleGroup(group.key)}
                  expandedSpanId={expandedSpanId}
                  onSpanToggle={toggleSpan}
                />
              )
            }
            const solo = group.segments[0]
            return (
              <TraceRow
                key={solo.trace_id}
                trace={solo}
                expanded={expandedSpanId === solo.trace_id}
                onToggle={() => toggleSpan(solo.trace_id)}
              />
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
