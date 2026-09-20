import { cn } from '@/utils/cn'
import type { MCPServerInfo } from '@/types/api'

interface Props {
  servers: MCPServerInfo[] | null
}

const STATUS_DOT: Record<MCPServerInfo['status'], string> = {
  connected:    'bg-emerald-500',
  disconnected: 'bg-red-500',
  error:        'bg-amber-500',
}

export function MCPStatusCard({ servers }: Props) {
  if (!servers) {
    return (
      <div className="rounded-xl border border-[hsl(217.2_32.6%_17.5%)] p-4">
        <h2 className="text-sm font-semibold text-slate-400 mb-3">MCP 外部工具服务</h2>
        <p className="text-xs text-slate-600">加载中...</p>
      </div>
    )
  }

  const connected = servers.filter(s => s.status === 'connected').length

  return (
    <div className="rounded-xl border border-[hsl(217.2_32.6%_17.5%)] p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">MCP 外部工具服务</h2>
        <span className="text-xs text-slate-400">
          {connected}/{servers.length} 在线
        </span>
      </div>

      {/* Server rows */}
      {servers.length === 0 ? (
        <p className="text-xs text-slate-600">暂无 MCP 服务</p>
      ) : (
        <div className="space-y-2">
          {servers.map(server => (
            <div key={server.name} className="flex items-center gap-2.5">
              <span className={cn('w-2 h-2 rounded-full flex-shrink-0', STATUS_DOT[server.status])} />
              <span className="text-xs text-slate-300 truncate">{server.name}</span>
              <span className="text-[10px] font-mono text-slate-600">{server.transport}</span>
              {server.status === 'connected' && (
                <span className="text-[10px] text-slate-500">{server.tool_count} 工具</span>
              )}
              {server.status === 'error' && server.error && (
                <span className="text-[10px] text-red-400/70 truncate ml-auto">{server.error}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Navigation hint */}
      <p className="text-[11px] text-slate-600 mt-3">在会话页面管理 MCP 服务</p>
    </div>
  )
}
