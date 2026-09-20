import type { Trace } from '@/api/tracing'

interface Props {
  traces: Trace[]
}

export function StatsCards({ traces }: Props) {
  const totalTokens = traces.reduce((sum, t) => sum + (t.token_total || 0), 0)
  // add-tracing-cache-token-display：缓存命中 / 实际消耗副行。
  // 全部 trace 均无 token_cache_read 字段（历史数据）→ 整体展示 —。
  const cacheRead = traces.reduce((sum, t) => sum + (t.token_cache_read || 0), 0)
  const hasCacheData = traces.some(t => t.token_cache_read != null)
  const cacheRate = totalTokens > 0 ? ((cacheRead / totalTokens) * 100).toFixed(1) : '0'
  const effectiveTokens = Math.max(0, totalTokens - cacheRead)
  const tokenSub = hasCacheData
    ? `缓存命中 ${cacheRead.toLocaleString()}（${cacheRate}%）· 实际消耗 ${effectiveTokens.toLocaleString()}`
    : '缓存命中 —'
  const avgDuration = traces.length > 0
    ? Math.round(traces.reduce((sum, t) => sum + t.duration_ms, 0) / traces.length)
    : 0
  const errorRate = traces.length > 0
    ? ((traces.filter(t => t.status === 'ERROR').length / traces.length) * 100).toFixed(1)
    : '0'

  const cards: { label: string; value: string; color: string; sub?: string }[] = [
    { label: 'Token 总消耗', value: totalTokens.toLocaleString(), color: '#8b5cf6', sub: tokenSub },
    { label: '平均耗时', value: formatDuration(avgDuration), color: '#3b82f6' },
    { label: '错误率', value: `${errorRate}%`, color: '#ef4444' },
  ]

  return (
    <div className="grid grid-cols-3 gap-4">
      {cards.map(card => (
        <div
          key={card.label}
          className="rounded-lg p-4"
          style={{ backgroundColor: 'var(--surface-deep)', border: '1px solid var(--border-color)' }}
        >
          <p className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>{card.label}</p>
          <p className="text-xl font-semibold tabular-nums" style={{ color: card.color }}>{card.value}</p>
          {card.sub && (
            <p className="text-[11px] mt-1 tabular-nums" style={{ color: 'var(--text-muted)' }}>{card.sub}</p>
          )}
        </div>
      ))}
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}
