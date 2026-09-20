import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { HealthCard } from '@/components/system/HealthCard'
import { ConfigCard } from '@/components/system/ConfigCard'
import { ModelSlotsCard } from '@/components/system/ModelSlotsCard'
import { MCPStatusCard } from '@/components/system/MCPStatusCard'
import { getConfig, getModelSlots } from '@/api/discovery'
import { listMCPServers } from '@/api/mcp'
import { useAppStore } from '@/store/app'
import type { ConfigResponse, ModelSlotsResponse, MCPServerInfo } from '@/types/api'

export function SystemPage() {
  const health = useAppStore(s => s.health)
  const refreshHealth = useAppStore(s => s.refreshHealth)
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [modelSlots, setModelSlots] = useState<ModelSlotsResponse | null>(null)
  const [mcpServers, setMcpServers] = useState<MCPServerInfo[] | null>(null)
  const [loading, setLoading] = useState(false)

  const loadAll = async () => {
    setLoading(true)
    try {
      const [cfg, slots, mcp] = await Promise.allSettled([
        getConfig(),
        getModelSlots(),
        listMCPServers(),
      ])
      setConfig(cfg.status === 'fulfilled' ? cfg.value : null)
      setModelSlots(slots.status === 'fulfilled' ? slots.value : null)
      setMcpServers(mcp.status === 'fulfilled' ? mcp.value : null)
      await refreshHealth()
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadAll() }, [])

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-200">系统状态</h1>
        <button
          onClick={loadAll}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-md border border-[hsl(217.2_32.6%_17.5%)] hover:border-slate-500 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      <HealthCard health={health} />
      {config && <ConfigCard config={config} />}
      {modelSlots && <ModelSlotsCard data={modelSlots} />}
      <MCPStatusCard servers={mcpServers} />
    </div>
  )
}
