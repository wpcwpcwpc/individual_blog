import type { InterruptMarkerItem } from '@/types/events'

export function InterruptMarkerRow({ item }: { item: InterruptMarkerItem }) {
  const label = item.interrupt_type === 'preview_confirm'
    ? `⏸️ 等待审核: ${item.tool_name ?? '工具执行'}`
    : item.interrupt_type === 'result_verify'
      ? '⏸️ 等待结果验收'
      : '⏸️ 等待计划确认'

  return (
    <div className="flex items-center gap-2 py-1">
      <div className="flex-1 h-px bg-amber-500/30" />
      <span className="text-xs text-amber-400 px-2 whitespace-nowrap">{label}</span>
      <div className="flex-1 h-px bg-amber-500/30" />
    </div>
  )
}
