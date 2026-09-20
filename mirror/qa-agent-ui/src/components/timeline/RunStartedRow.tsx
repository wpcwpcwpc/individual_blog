import type { RunStartedItem } from '@/types/events'
import { getAgentShortName } from '@/utils/agentColors'

export function RunStartedRow({ item }: { item: RunStartedItem }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="flex-1 h-px bg-[hsl(217.2_32.6%_17.5%)]" />
      <span className="text-xs text-slate-500 px-2 whitespace-nowrap">
        🚀 {getAgentShortName(item.agent_name)} 开始执行
      </span>
      <div className="flex-1 h-px bg-[hsl(217.2_32.6%_17.5%)]" />
    </div>
  )
}
