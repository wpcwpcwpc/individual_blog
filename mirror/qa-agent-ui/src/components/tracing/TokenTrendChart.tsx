import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { listDailyTokens, type DailyTokenEntry } from '@/api/tracing'
import { useAuthStore } from '@/store/auth'

interface Props {
  agentType?: 'all' | 'agent' | 'workflow'
}

type ViewMode = 'personal' | 'global'

export function TokenTrendChart({ agentType = 'all' }: Props) {
  const user = useAuthStore(s => s.user)
  const isInternal = user?.role === 'internal'
  // mirror TracingPage default: normal = personal, internal = global
  const viewMode: ViewMode = isInternal ? 'global' : 'personal'

  const [entries, setEntries] = useState<DailyTokenEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    listDailyTokens({
      days: 7,
      ...(viewMode === 'personal' && user?.email ? { user_id: user.email } : {}),
      ...(agentType !== 'all' ? { agent_type: agentType } : {}),
    })
      .then(res => {
        if (cancelled) return
        setEntries(res.days)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : 'Failed to load token trend')
      })
      .finally(() => {
        if (cancelled) return
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [viewMode, user?.email, agentType])

  const maxTokens = Math.max(1, ...entries.map(d => d.token_total))

  return (
    <div className="rounded-lg p-4" style={{ backgroundColor: 'var(--surface-deep)', border: '1px solid var(--border-color)' }}>
      <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>Token 消耗趋势（最近 7 天）</p>
      {error ? (
        <div className="h-[100px] flex items-center justify-center">
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{error}</p>
        </div>
      ) : (
        <div className="flex items-end gap-2 h-[100px]">
          {entries.map(({ day, token_total }) => (
            <div key={day} className="flex-1 flex flex-col items-center gap-1">
              <div className="w-full flex items-end justify-center" style={{ height: '80px' }}>
                <div
                  className="w-full max-w-[24px] rounded-t-sm transition-all"
                  style={{
                    height: `${(token_total / maxTokens) * 100}%`,
                    minHeight: token_total > 0 ? '4px' : '0px',
                    backgroundColor: '#8b5cf6',
                    opacity: loading ? 0.4 : 0.8,
                  }}
                />
              </div>
              <span className="text-[10px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
                {dayjs(day).format('MM/DD')}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
