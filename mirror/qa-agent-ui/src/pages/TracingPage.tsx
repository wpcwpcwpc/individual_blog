import { useEffect, useState, useCallback, useMemo } from 'react'
import { RefreshCw } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { listTraces, type Trace, type ListTracesParams } from '@/api/tracing'
import { TraceFilterBar } from '@/components/tracing/TraceFilterBar'
import { TraceTable } from '@/components/tracing/TraceTable'
import { StatsCards } from '@/components/tracing/StatsCards'
import { TokenTrendChart } from '@/components/tracing/TokenTrendChart'
import { AgentBreakdownTable } from '@/components/tracing/AgentBreakdownTable'

type ViewMode = 'personal' | 'global'
type TabMode = 'list' | 'stats'
type AgentTypeFilter = 'all' | 'agent' | 'workflow'

export function TracingPage() {
  const user = useAuthStore(s => s.user)
  const isInternal = user?.role === 'internal'
  // normal 用户强制 personal 且不可切;internal 默认 global 可切
  const [viewMode, setViewMode] = useState<ViewMode>(isInternal ? 'global' : 'personal')
  const [tabMode, setTabMode] = useState<TabMode>('list')
  const [agentType, setAgentType] = useState<AgentTypeFilter>('all')
  const [traces, setTraces] = useState<Trace[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filters, setFilters] = useState<ListTracesParams>({})
  const limit = 20

  const fetchTraces = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: ListTracesParams = {
        ...filters,
        limit,
        page,
      }
      if (viewMode === 'personal' && user?.email) {
        params.user_id = user.email
      }
      if (agentType !== 'all') {
        params.agent_type = agentType
      }
      const res = await listTraces(params)
      setTraces(res.traces)
      setTotal(res.total)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load traces')
    } finally {
      setLoading(false)
    }
  }, [viewMode, user?.email, page, filters, agentType])

  useEffect(() => { fetchTraces() }, [fetchTraces])

  const agentTraces = useMemo(() =>
    traces.filter(t => t.agent_type === 'agent' || !t.agent_type),
  [traces])

  const workflowTraces = useMemo(() =>
    traces.filter(t => t.agent_type === 'workflow'),
  [traces])

  const totalPages = Math.max(1, Math.ceil(total / limit))

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>执行追踪</h1>
        <div className="flex items-center gap-3">
          {/* View toggle — 仅 internal 用户可见,normal 强制 personal */}
          {isInternal && (
          <div className="flex rounded-md overflow-hidden text-xs" style={{ border: '1px solid var(--border-color)' }}>
            <button
              onClick={() => { setViewMode('personal'); setPage(1) }}
              className="px-3 py-1.5 transition-colors"
              style={{
                backgroundColor: viewMode === 'personal' ? 'var(--accent-color)' : 'transparent',
                color: viewMode === 'personal' ? '#fff' : 'var(--text-secondary)',
              }}
            >
              👤 我的
            </button>
            <button
              onClick={() => { setViewMode('global'); setPage(1) }}
              className="px-3 py-1.5 transition-colors"
              style={{
                backgroundColor: viewMode === 'global' ? 'var(--accent-color)' : 'transparent',
                color: viewMode === 'global' ? '#fff' : 'var(--text-secondary)',
              }}
            >
              🌐 全局
            </button>
          </div>
          )}
          <button
            onClick={fetchTraces}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md transition-colors disabled:opacity-50"
            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {/* Agent/Workflow type toggle */}
      <div className="flex rounded-md overflow-hidden text-xs" style={{ border: '1px solid var(--border-color)', width: 'fit-content' }}>
        <button
          onClick={() => { setAgentType('all'); setPage(1) }}
          className="px-3 py-1.5 transition-colors"
          style={{
            backgroundColor: agentType === 'all' ? 'var(--accent-color)' : 'transparent',
            color: agentType === 'all' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          全部
        </button>
        <button
          onClick={() => { setAgentType('agent'); setPage(1) }}
          className="px-3 py-1.5 transition-colors"
          style={{
            backgroundColor: agentType === 'agent' ? 'var(--accent-color)' : 'transparent',
            color: agentType === 'agent' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          🤖 Agent 执行
        </button>
        <button
          onClick={() => { setAgentType('workflow'); setPage(1) }}
          className="px-3 py-1.5 transition-colors"
          style={{
            backgroundColor: agentType === 'workflow' ? 'var(--accent-color)' : 'transparent',
            color: agentType === 'workflow' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          🔄 Workflow 执行
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-4 text-sm" style={{ borderBottom: '1px solid var(--border-color)' }}>
        <button
          onClick={() => setTabMode('list')}
          className="pb-2 px-1 transition-colors"
          style={{
            color: tabMode === 'list' ? 'var(--accent-color)' : 'var(--text-muted)',
            borderBottom: tabMode === 'list' ? '2px solid var(--accent-color)' : '2px solid transparent',
          }}
        >
          执行列表
        </button>
        <button
          onClick={() => setTabMode('stats')}
          className="pb-2 px-1 transition-colors"
          style={{
            color: tabMode === 'stats' ? 'var(--accent-color)' : 'var(--text-muted)',
            borderBottom: tabMode === 'stats' ? '2px solid var(--accent-color)' : '2px solid transparent',
          }}
        >
          统计分析
        </button>
      </div>

      {/* Content */}
      {tabMode === 'list' ? (
        <>
          <TraceFilterBar filters={filters} onChange={(f) => { setFilters(f); setPage(1) }} agentType={agentType} onTypeChange={(t) => { setAgentType(t); setPage(1) }} />
          {error ? (
            <div className="text-center py-12">
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{error}</p>
              <button
                onClick={fetchTraces}
                className="mt-3 text-xs px-3 py-1.5 rounded-md"
                style={{ color: 'var(--accent-color)', border: '1px solid var(--accent-color)' }}
              >
                重试
              </button>
            </div>
          ) : traces.length === 0 && !loading ? (
            <div className="text-center py-12">
              <div className="text-4xl mb-2">📭</div>
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>暂无执行记录</p>
            </div>
          ) : (
            <>
              <TraceTable traces={traces} loading={loading} />
              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 pt-2">
                  <button
                    disabled={page <= 1}
                    onClick={() => setPage(p => p - 1)}
                    className="text-xs px-3 py-1 rounded disabled:opacity-30"
                    style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
                  >
                    上一页
                  </button>
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{page} / {totalPages}</span>
                  <button
                    disabled={page >= totalPages}
                    onClick={() => setPage(p => p + 1)}
                    className="text-xs px-3 py-1 rounded disabled:opacity-30"
                    style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
                  >
                    下一页
                  </button>
                </div>
              )}
            </>
          )}
        </>
      ) : (
        <div className="space-y-4">
          <StatsCards traces={traces} />
          <TokenTrendChart agentType={agentType} />
          <AgentBreakdownTable traces={agentTraces} groupKey="agent_id" title="按 Agent 分组" />
          <AgentBreakdownTable traces={workflowTraces} groupKey="workflow_id" title="按 Workflow 分组" />
        </div>
      )}
    </div>
  )
}
