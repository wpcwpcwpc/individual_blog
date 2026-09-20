import type { Trace } from '@/api/tracing'

interface Props {
  traces: Trace[]
  groupKey: 'agent_id' | 'workflow_id'
  title: string
}

interface DimStats {
  key: string
  count: number
  avgDuration: number
  errorCount: number
  totalTokens: number
}

export function AgentBreakdownTable({ traces, groupKey, title }: Props) {
  const grouped = new Map<string, Trace[]>()
  for (const t of traces) {
    const key = (t[groupKey] as string) || '(unknown)'
    if (!grouped.has(key)) grouped.set(key, [])
    grouped.get(key)!.push(t)
  }

  const stats: DimStats[] = Array.from(grouped.entries()).map(([key, items]) => ({
    key,
    count: items.length,
    avgDuration: Math.round(items.reduce((s, t) => s + t.duration_ms, 0) / items.length),
    errorCount: items.filter(t => t.status === 'ERROR').length,
    totalTokens: items.reduce((s, t) => s + (t.token_total || 0), 0),
  }))

  stats.sort((a, b) => b.count - a.count)

  if (stats.length === 0) {
    return null
  }

  const colTitle = groupKey === 'workflow_id' ? 'Workflow' : 'Agent'

  return (
    <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border-color)' }}>
      <div className="px-4 py-2" style={{ backgroundColor: 'var(--surface-deep)' }}>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{title}</p>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ backgroundColor: 'var(--surface-deep)', color: 'var(--text-muted)' }}>
            <th className="text-left px-4 py-2 font-medium">{colTitle}</th>
            <th className="text-right px-4 py-2 font-medium">执行数</th>
            <th className="text-right px-4 py-2 font-medium">Token</th>
            <th className="text-right px-4 py-2 font-medium">平均耗时</th>
            <th className="text-right px-4 py-2 font-medium">错误数</th>
          </tr>
        </thead>
        <tbody>
          {stats.map(s => (
            <tr key={s.key} style={{ borderTop: '1px solid var(--border-color)' }}>
              <td className="px-4 py-2" style={{ color: 'var(--text-primary)' }}>{s.key}</td>
              <td className="px-4 py-2 text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{s.count}</td>
              <td className="px-4 py-2 text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{s.totalTokens.toLocaleString()}</td>
              <td className="px-4 py-2 text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{formatDuration(s.avgDuration)}</td>
              <td className="px-4 py-2 text-right tabular-nums" style={{ color: s.errorCount > 0 ? '#ef4444' : 'var(--text-muted)' }}>{s.errorCount}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}
