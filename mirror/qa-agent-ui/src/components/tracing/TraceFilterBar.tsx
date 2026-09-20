import type { ListTracesParams } from '@/api/tracing'

type AgentTypeFilter = 'all' | 'agent' | 'workflow'

interface Props {
  filters: ListTracesParams
  onChange: (filters: ListTracesParams) => void
  agentType: AgentTypeFilter
  onTypeChange: (type: AgentTypeFilter) => void
}

export function TraceFilterBar({ filters, onChange, agentType, onTypeChange }: Props) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {/* Agent/Workflow type toggle */}
      <div className="flex rounded-md overflow-hidden text-xs" style={{ border: '1px solid var(--border-color)' }}>
        <button
          onClick={() => onTypeChange('all')}
          className="px-2.5 py-1.5 transition-colors"
          style={{
            backgroundColor: agentType === 'all' ? 'var(--accent-color)' : 'transparent',
            color: agentType === 'all' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          全部
        </button>
        <button
          onClick={() => onTypeChange('agent')}
          className="px-2.5 py-1.5 transition-colors"
          style={{
            backgroundColor: agentType === 'agent' ? 'var(--accent-color)' : 'transparent',
            color: agentType === 'agent' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          Agent
        </button>
        <button
          onClick={() => onTypeChange('workflow')}
          className="px-2.5 py-1.5 transition-colors"
          style={{
            backgroundColor: agentType === 'workflow' ? 'var(--accent-color)' : 'transparent',
            color: agentType === 'workflow' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          Workflow
        </button>
      </div>

      <input
        type="text"
        placeholder="Agent ID"
        value={filters.agent_id || ''}
        onChange={e => onChange({ ...filters, agent_id: e.target.value || undefined })}
        className="text-xs px-2.5 py-1.5 rounded-md bg-transparent outline-none"
        style={{ border: '1px solid var(--border-color)', color: 'var(--text-primary)', width: '140px' }}
      />
      <select
        value={filters.status || ''}
        onChange={e => onChange({ ...filters, status: e.target.value || undefined })}
        className="text-xs px-2.5 py-1.5 rounded-md bg-transparent outline-none"
        style={{ border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
      >
        <option value="">全部状态</option>
        <option value="OK">OK</option>
        <option value="ERROR">ERROR</option>
        <option value="UNSET">UNSET</option>
      </select>
      <input
        type="date"
        value={filters.start_time?.slice(0, 10) || ''}
        onChange={e => {
          const v = e.target.value
          onChange({ ...filters, start_time: v ? `${v}T00:00:00Z` : undefined })
        }}
        className="text-xs px-2.5 py-1.5 rounded-md bg-transparent outline-none"
        style={{ border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
      />
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>~</span>
      <input
        type="date"
        value={filters.end_time?.slice(0, 10) || ''}
        onChange={e => {
          const v = e.target.value
          onChange({ ...filters, end_time: v ? `${v}T23:59:59Z` : undefined })
        }}
        className="text-xs px-2.5 py-1.5 rounded-md bg-transparent outline-none"
        style={{ border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
      />
    </div>
  )
}
