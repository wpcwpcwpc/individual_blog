import type { RunErrorItem } from '@/types/events'

export function ErrorRow({ item }: { item: RunErrorItem }) {
  return (
    <div className="error-card rounded-lg border border-red-300 bg-red-50 px-4 py-3">
      <div className="flex items-center gap-2 text-sm text-red-700 font-medium mb-1">
        ❌ 执行错误
      </div>
      <pre className="text-xs text-red-800 whitespace-pre-wrap">{item.error}</pre>
    </div>
  )
}
