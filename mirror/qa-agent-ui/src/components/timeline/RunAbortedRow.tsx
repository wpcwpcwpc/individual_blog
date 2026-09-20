import type { RunAbortedItem } from '@/types/events'

export function RunAbortedRow({ item }: { item: RunAbortedItem }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="flex-1 h-px bg-orange-500/30" />
      <span className="flex items-center gap-1.5 text-xs text-orange-400 px-2 whitespace-nowrap">
        ⚠️ 执行已被用户中断
        {item.agent_name && (
          <span className="text-orange-400/60">({item.agent_name})</span>
        )}
      </span>
      <div className="flex-1 h-px bg-orange-500/30" />
    </div>
  )
}
