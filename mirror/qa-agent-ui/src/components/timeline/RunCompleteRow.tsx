import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { RunCompleteItem } from '@/types/events'

export function RunCompleteRow({ item }: { item: RunCompleteItem }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 py-1">
        <div className="flex-1 h-px bg-green-500/30" />
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1.5 text-xs text-green-700 px-2 whitespace-nowrap hover:text-green-600 transition-colors"
        >
          🏁 执行完成
          {item.final_response && <ChevronDown className={`w-3 h-3 transition-transform ${expanded ? 'rotate-180' : ''}`} />}
        </button>
        <div className="flex-1 h-px bg-green-500/30" />
      </div>
      {expanded && item.final_response && (
        <div className="rounded-lg border border-green-500/20 bg-[#f0fdf4] px-4 py-3 text-sm text-slate-700">
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.final_response}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}
