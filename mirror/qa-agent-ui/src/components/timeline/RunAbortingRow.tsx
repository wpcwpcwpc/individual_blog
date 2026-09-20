import type { RunAbortingItem } from '@/types/events'

/**
 * 「中断中」过渡行（fix-abort-latency D2）。
 *
 * 后端中断信号已触发、收口（落库 + 终态）未完成期间的占位行；`run_aborted`
 * 到达时被 store 清除并替换为 {@link RunAbortedRow}。
 */
export function RunAbortingRow({ item }: { item: RunAbortingItem }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="flex-1 h-px bg-orange-500/20" />
      <span className="flex items-center gap-1.5 text-xs text-orange-400/80 px-2 whitespace-nowrap">
        <span className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse" />
        正在中断…
        {item.agent_name && (
          <span className="text-orange-400/60">({item.agent_name})</span>
        )}
      </span>
      <div className="flex-1 h-px bg-orange-500/20" />
    </div>
  )
}
