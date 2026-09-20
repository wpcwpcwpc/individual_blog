/**
 * ThinkingBlock — 思考折叠块（add-thinking-stream-display design D4）。
 *
 * 状态机：streaming 展开（尾部渐进 + 用户可手动收起，收起后增量静默追加
 * 不强制展开）→ settled 自动收起为摘要行（点击标题重展开回看全文）。
 * 内容纯文本渲染（不渲染 markdown），多段思考由 timeline 分段天然界定。
 */
import { useEffect, useRef, useState } from 'react'
import { Brain, ChevronRight } from 'lucide-react'
import type { AgentThinkingItem } from '@/types/events'
import { cn } from '@/utils/cn'

interface Props {
  item: AgentThinkingItem
}

function formatDuration(ms: number): string {
  const s = Math.max(1, Math.round(ms / 1000))
  if (s < 60) return `${s} 秒`
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`
}

export function ThinkingBlock({ item }: Props) {
  // null = 跟随自动状态（流式展开/结束收起）；true = 用户手动收起；false = 用户手动展开
  const [userToggle, setUserToggle] = useState<boolean | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  // 流式期间展开（除非用户手动收起）；settled 收起（除非用户手动展开）
  const expanded = item.isStreaming ? userToggle !== true : userToggle === false

  // 流式期间内容区尾部跟随（渐进渲染，max-height 内滚动条贴底）
  useEffect(() => {
    if (item.isStreaming && expanded && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [item.content, item.isStreaming, expanded])

  const charCount = item.content.length
  // 历史重建块 startedAt/endedAt 同值（落库只有单一 created_at）→ 时长不可知，
  // 零时长时仅展示字数（live 路径真实首尾差恒 > 0，不受影响）。
  const durationMs = (item.endedAt ?? Date.now()) - item.startedAt
  const durationText = durationMs > 0 ? ` · ${formatDuration(durationMs)}` : ''
  const summary = item.isStreaming
    ? `思考中 · ${charCount} 字`
    : `已深度思考${durationText} · ${charCount} 字`

  return (
    <div className="py-1">
      <div className={cn('rounded-lg border min-w-0', 'bg-[hsl(var(--card))] border-[hsl(var(--border))]')}>
        <button
          type="button"
          aria-expanded={expanded}
          // 当前展开 → 点击后归「手动收起」(true)；当前收起 → 归「手动展开」(false)
          onClick={() => setUserToggle(expanded)}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-[11px] text-[hsl(var(--muted-foreground))] transition-colors hover:bg-[hsl(var(--secondary))]"
        >
          <Brain className={cn('h-3.5 w-3.5 shrink-0', item.isStreaming && 'animate-pulse text-violet-400')} />
          <span className="truncate">{summary}</span>
          <ChevronRight className={cn('ml-auto h-3.5 w-3.5 shrink-0 transition-transform', expanded && 'rotate-90')} />
        </button>
        {expanded && (
          <div
            ref={contentRef}
            className="max-h-64 overflow-y-auto whitespace-pre-wrap px-3 pb-2 text-xs leading-relaxed text-[hsl(var(--muted-foreground))]"
          >
            {item.content}
            {item.isStreaming && <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-violet-400 align-baseline" />}
          </div>
        )}
      </div>
    </div>
  )
}
