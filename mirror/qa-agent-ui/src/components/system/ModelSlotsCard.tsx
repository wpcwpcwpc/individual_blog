import type { ModelSlotsResponse } from '@/types/api'

interface Props {
  data: ModelSlotsResponse
}

export function ModelSlotsCard({ data }: Props) {
  return (
    <div className="space-y-4">
      {/* Slot configs */}
      <div className="rounded-xl border border-[hsl(217.2_32.6%_17.5%)] p-4">
        <h2 className="text-sm font-semibold text-slate-200 mb-3">模型槽位配置</h2>
        <div className="divide-y divide-[hsl(217.2_32.6%_13%)]">
          {Object.entries(data.slots).map(([slot, cfg]) => (
            <div key={slot} className="py-2 flex items-start gap-4">
              <span className="text-xs font-mono text-violet-300 w-24 flex-shrink-0">{slot}</span>
              <div className="space-y-0.5">
                <p className="text-xs text-slate-300">{cfg.model}</p>
                {cfg.base_url && <p className="text-xs text-slate-600 font-mono">{cfg.base_url}</p>}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Agent → slot mapping */}
      <div className="rounded-xl border border-[hsl(217.2_32.6%_17.5%)] p-4">
        <h2 className="text-sm font-semibold text-slate-200 mb-3">Agent 槽位映射</h2>
        <div className="divide-y divide-[hsl(217.2_32.6%_13%)]">
          {Object.entries(data.agent_slots).map(([agent, { slot }]) => (
            <div key={agent} className="py-2 flex items-center gap-4">
              <span className="text-xs text-slate-400 flex-1">{agent}</span>
              <span className="text-xs font-mono text-violet-300 bg-violet-500/10 px-2 py-0.5 rounded">
                {slot}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
