import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ChevronDown } from 'lucide-react'
import { useSessionsStore } from '@/store/sessions'
import { isHistoryPaginationEnabled } from '@/utils/historyPaging'
import { planTurnRows } from '@/utils/turns'
import { AgentMessageBubble } from './AgentMessageBubble'
import { ThinkingBlock } from './ThinkingBlock'
import { UserMessageBubble } from './UserMessageBubble'
import { ToolCallCard } from './ToolCallCard'
import { RunStartedRow } from './RunStartedRow'
import { RunCompleteRow } from './RunCompleteRow'
import { ErrorRow } from './ErrorRow'
import { InterruptMarkerRow } from './InterruptMarkerRow'
import { RunAbortedRow } from './RunAbortedRow'
import { RunAbortingRow } from './RunAbortingRow'
import { StaleDisconnectBanner } from './StaleDisconnectBanner'
import { ApprovalCard } from '@/components/approvals/ApprovalCard'
import ClarifyCard from '@/components/approvals/ClarifyCard'
import { ContextUsageBadge } from '@/components/session/ContextUsageBadge'
import { TurnRegenerateRow } from './TurnRegenerateRow'
import type { TimelineItem } from '@/types/events'

// 稳定空数组引用，避免 selector `?? []` 每次新建引用触发 useSyncExternalStore 无限循环
const EMPTY_TIMELINE: TimelineItem[] = []

// add-session-history-pagination D7：哨兵触发防抖（ms）
const SENTINEL_DEBOUNCE_MS = 100
// D4 滚动锚定：用户已滚离顶部超过该阈值（px）时跳过补偿
const ANCHOR_SKIP_TOP_PX = 50

interface Props {
  sessionId: string
}

export function ExecutionTimeline({ sessionId }: Props) {
  const timeline = useSessionsStore(s => s.sessions[sessionId]?.timeline ?? EMPTY_TIMELINE)
  const historyPaging = useSessionsStore(s => s.sessions[sessionId]?.historyPaging)
  const lastMutation = useSessionsStore(s => s.sessions[sessionId]?.lastMutation)
  const parentRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const [isAutoScroll, setIsAutoScroll] = useState(true)
  const [pageError, setPageError] = useState(false)
  const lastScrollTop = useRef(0)

  const pagingEnabled = historyPaging != null && isHistoryPaginationEnabled()

  const virtualizer = useVirtualizer({
    count: timeline.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 80,
    overscan: 5,
    // D4：测量缓存键从 index 改为 item.id —— prepend 后 index 平移不再导致
    // 测量错位/滚动跳动（id 全局唯一且跨 prepend 稳定）
    getItemKey: index => timeline[index]?.id ?? `idx-${index}`,
  })

  // add-turn-regenerate D2：轮次重答入口位（U 卡下方 + 轮尾，同一动作）
  const turnRowPlan = useMemo(() => planTurnRows(timeline), [timeline])

  // ── D7 触顶翻页：顶部哨兵 + 在途守卫 + 100ms 防抖 ──────────────────
  const triggerPrepend = useCallback(async () => {
    const paging = useSessionsStore.getState().sessions[sessionId]?.historyPaging
    if (!paging || paging.fetchingOlder || !paging.hasMore) return
    // D4 滚动锚定基线：store 更新前记录（commit 后不可再取到旧值）
    const el = parentRef.current
    if (el) scrollAnchorRef.current = { height: el.scrollHeight, top: el.scrollTop }
    const ok = await useSessionsStore.getState().prependHistoryPage(sessionId)
    setPageError(!ok)
  }, [sessionId])

  const scrollAnchorRef = useRef<{ height: number; top: number } | null>(null)

  useEffect(() => {
    if (!pagingEnabled) return
    const sentinel = sentinelRef.current
    const root = parentRef.current
    if (!sentinel || !root) return

    let debounceTimer: ReturnType<typeof setTimeout> | null = null
    const io = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          if (debounceTimer) clearTimeout(debounceTimer)
          debounceTimer = setTimeout(() => {
            void triggerPrepend()
          }, SENTINEL_DEBOUNCE_MS)
        }
      },
      { root, threshold: 0 },
    )
    io.observe(sentinel)
    return () => {
      io.disconnect()
      if (debounceTimer) clearTimeout(debounceTimer)
    }
  }, [pagingEnabled, triggerPrepend])

  // ── D4 prepend 滚动锚定：双 rAF + scrollTop 补偿（用户滚走跳过）──────
  const prevMutationRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    const wasPrepend = prevMutationRef.current === 'prepend'
    prevMutationRef.current = lastMutation
    if (lastMutation !== 'prepend' || !wasPrepend) return

    const el = parentRef.current
    const anchor = scrollAnchorRef.current
    if (!el || !anchor) return
    scrollAnchorRef.current = null

    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        const delta = el.scrollHeight - anchor.height
        if (delta > 0 && anchor.top <= ANCHOR_SKIP_TOP_PX) {
          el.scrollTop = anchor.top + delta
        }
      })
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [lastMutation, timeline.length])

  // ── D5 自动滚底守卫：仅 'append' 且用户在底部时跟随 ─────────────────
  // 'prepend'（翻旧页）与 'replace'（truncate/重灌）不拽回底部。
  useEffect(() => {
    if (
      isAutoScroll
      && timeline.length > 0
      && lastMutation !== 'prepend'
      && lastMutation !== 'replace'
    ) {
      virtualizer.scrollToIndex(timeline.length - 1, { align: 'end' })
    }
  }, [timeline.length, isAutoScroll, lastMutation, virtualizer])

  // Detect manual scroll-up
  const handleScroll = () => {
    const el = parentRef.current
    if (!el) return
    const scrollingUp = el.scrollTop < lastScrollTop.current
    lastScrollTop.current = el.scrollTop
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50
    if (scrollingUp && !atBottom) {
      setIsAutoScroll(false)
    } else if (atBottom) {
      setIsAutoScroll(true)
    }
  }

  if (timeline.length === 0) {
    return (
      <div className="relative flex-1 min-h-0 flex flex-col">
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-slate-600">
            <div className="text-4xl mb-2">💬</div>
            <p className="text-sm">发送任务消息后，Agent 执行过程将在此实时展示</p>
          </div>
        </div>
        {/* StaleDisconnectBanner renders null when no stale disconnect;
            wrapper collapses to 0 height so empty-state layout is unaffected. */}
        <div className="px-4">
          <StaleDisconnectBanner sessionId={sessionId} />
        </div>
        {/* 底部左下角：上下文占用徽章（分隔最后一张消息卡片与底部边框） */}
        <div className="flex-shrink-0 flex items-center px-4 pb-1.5">
          <ContextUsageBadge sessionId={sessionId} />
        </div>
      </div>
    )
  }

  return (
    <div className="relative flex-1 min-h-0 flex flex-col">
      <div
        ref={parentRef}
        className="flex-1 min-h-0 overflow-y-auto px-4 py-3"
        onScroll={handleScroll}
      >
        {/* D7 顶部哨兵：hasMore 时占位可见即触发翻页 */}
        {pagingEnabled && historyPaging?.hasMore && (
          <div ref={sentinelRef} className="py-2 text-center text-xs text-slate-500">
            {historyPaging.fetchingOlder ? '加载历史中…' : '︱'}
          </div>
        )}
        {/* D7 翻页失败内联重试（不砸窗口，游标不丢） */}
        {pagingEnabled && pageError && (
          <button
            onClick={() => { setPageError(false); void triggerPrepend() }}
            className="mx-auto my-1 block text-xs text-red-400 hover:text-red-300 underline underline-offset-2"
          >
            加载失败 · 重试
          </button>
        )}
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {virtualizer.getVirtualItems().map(virtualItem => {
            const item = timeline[virtualItem.index] as TimelineItem
            return (
              <div
                key={virtualItem.key}
                data-index={virtualItem.index}
                ref={virtualizer.measureElement}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${virtualItem.start}px)`,
                }}
                className="pb-2"
              >
                <TimelineItemRenderer item={item} sessionId={sessionId} />
                {turnRowPlan.userRowAfterIdx.has(virtualItem.index) && item.type === 'user_message' && (
                  <TurnRegenerateRow sessionId={sessionId} userItemId={item.id} />
                )}
                {turnRowPlan.tailRowAfterIdx.has(virtualItem.index) && (
                  <TurnRegenerateRow sessionId={sessionId} userItemId={turnRowPlan.tailRowAfterIdx.get(virtualItem.index)!} />
                )}
              </div>
            )
          })}
        </div>
        {/* Stale disconnect banner — inline at end of timeline (design.md D5).
            Returns null when staleDisconnect is false or session not running. */}
        <StaleDisconnectBanner sessionId={sessionId} />
      </div>

      {/* 底部左下角：上下文占用徽章（分隔最后一张消息卡片与底部边框） */}
      <div className="flex-shrink-0 flex items-center px-4 pb-1.5">
        <ContextUsageBadge sessionId={sessionId} />
      </div>

      {/* Scroll-to-bottom button */}
      {!isAutoScroll && (
        <button
          onClick={() => {
            setIsAutoScroll(true)
            virtualizer.scrollToIndex(timeline.length - 1, { align: 'end' })
          }}
          className="absolute bottom-4 right-4 flex items-center gap-1.5 bg-[hsl(217.2_32.6%_17.5%)] hover:bg-[hsl(217.2_32.6%_22%%)] text-slate-300 text-xs px-3 py-1.5 rounded-full shadow-lg border border-[hsl(217.2_32.6%_25%)] transition-colors"
        >
          <ChevronDown className="w-3.5 h-3.5" />
          回到底部
        </button>
      )}
    </div>
  )
}

function TimelineItemRenderer({ item, sessionId }: { item: TimelineItem; sessionId: string }) {
    switch (item.type) {
    case 'agent_message':  return <AgentMessageBubble item={item} sessionId={sessionId} />
    case 'agent_thinking': return <ThinkingBlock item={item} />
    case 'user_message':   return <UserMessageBubble item={item} sessionId={sessionId} />
    case 'tool_call':      return <ToolCallCard item={item} />
    case 'run_started':    return <RunStartedRow item={item} />
    case 'run_complete':   return <RunCompleteRow item={item} />
    case 'run_error':      return <ErrorRow item={item} />
    case 'run_aborted':    return <RunAbortedRow item={item} />
    case 'run_aborting':   return <RunAbortingRow item={item} />
    case 'interrupt_marker': return <InterruptMarkerRow item={item} />
    case 'approval_pending': return <ApprovalCard item={item} sessionId={sessionId} />
    case 'clarify_request': return <ClarifyCard item={item} sessionId={sessionId} />
    default:               return null
  }
}
