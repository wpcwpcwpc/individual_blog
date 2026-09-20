import { useEffect, useRef, useState } from 'react'
import { useSessionsStore } from '@/store/sessions'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/utils/cn'
import { formatTokens } from '@/utils/formatTokens'
import { ContextUsagePopover } from './ContextUsagePopover'

interface Props {
  sessionId: string
}

/**
 * 会话页常驻上下文占用徽章（add-context-usage-visibility）。
 *
 * 环形占比（黑色细线底环 + 蓝色进度弧）+ `占比 | Tokens: 总消耗` 文本；
 * 点击弹出 ContextUsagePopover 查看详情。
 * 数据源：store.sessionUsage[sessionId].breakdown（run_complete 后由
 * refreshContextUsage 静默刷新）。无数据（端点失败/未加载）显示空环 + `—`。
 * 数字为 tokenizer 估算值（tooltip 标注）。
 */
export function ContextUsageBadge({ sessionId }: Props) {
  const breakdown = useSessionsStore(s => s.sessionUsage[sessionId]?.breakdown ?? null)
  const refreshContextUsage = useSessionsStore(s => s.refreshContextUsage)
  const [open, setOpen] = useState(false)
  const [popoverMaxH, setPopoverMaxH] = useState<number | undefined>(undefined)
  const anchorRef = useRef<HTMLDivElement>(null)

  // 首挂载静默拉一次（恢复/刷新页面后徽章不空白）
  useEffect(() => {
    void refreshContextUsage(sessionId)
  }, [sessionId, refreshContextUsage])

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (
        anchorRef.current &&
        !anchorRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const max = breakdown?.max_context_tokens ?? 0
  const used =
    breakdown == null
      ? null
      : (breakdown.categories.system_prompt ?? 0) +
        (breakdown.categories.tools ?? 0) +
        (breakdown.categories.mcp_tools ?? 0) +
        (breakdown.categories.messages ?? 0)
  const ratio = max > 0 && used != null ? used / max : null

  return (
    <div ref={anchorRef} className="relative flex-shrink-0">
      <Tooltip
        tip={`上下文占用（估算值）${ratio != null ? ` — ${(ratio * 100).toFixed(1)}%` : ' — 点击查看详情'}`}
        disabled={open}
      >
        <button
          onClick={() => {
            const next = !open
            setOpen(next)
            if (next) {
              // 面板向上展开：可用高度 = 徽章顶部到视口顶部的距离（留 16px 边距）
              const rect = anchorRef.current?.getBoundingClientRect()
              if (rect) setPopoverMaxH(Math.max(240, rect.top - 16))
              void refreshContextUsage(sessionId)
            }
          }}
          className={cn(
            'flex items-center gap-1 p-0.5 rounded-md transition-colors',
            'hover:bg-[hsl(var(--muted))]',
          )}
        >
          <UsageRing ratio={ratio} />
          <span className="text-xs font-medium tabular-nums">
            {ratio != null ? `${(ratio * 100).toFixed(ratio < 0.1 ? 1 : 0)}%` : '—'}
            {' | '}
            Tokens:{' '}
            {breakdown?.usage_totals ? formatTokens(breakdown.usage_totals.total_tokens) : '—'}
          </span>
        </button>
      </Tooltip>
      {open && (
        <ContextUsagePopover
          sessionId={sessionId}
          onClose={() => setOpen(false)}
          maxHeight={popoverMaxH}
        />
      )}
    </div>
  )
}

// ── UsageRing：占比环形 ────
// 底环黑色细线 + 蓝色进度弧；占比与 Tokens 文本由外部在环右侧渲染。
const RING_SIZE = 18
const RING_STROKE = 4

function UsageRing({ ratio }: { ratio: number | null }) {
  const r = (RING_SIZE - RING_STROKE) / 2
  const circumference = 2 * Math.PI * r
  const clamped = ratio != null ? Math.min(1, Math.max(0, ratio)) : null
  return (
    <span
      className="relative inline-flex items-center justify-center flex-shrink-0"
      style={{ width: RING_SIZE, height: RING_SIZE }}
    >
      <svg
        width={RING_SIZE}
        height={RING_SIZE}
        viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
        style={{ width: RING_SIZE, height: RING_SIZE, display: 'block' }}
      >
        <g transform={`rotate(-90 ${RING_SIZE / 2} ${RING_SIZE / 2})`}>
          <circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={r}
            fill="none"
            strokeWidth={RING_STROKE}
            stroke="#000"
          />
          <circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={r}
            fill="none"
            strokeWidth={RING_STROKE}
            strokeLinecap="round"
            strokeDasharray={
              clamped != null
                ? `${clamped * circumference} ${circumference}`
                : `0 ${circumference}`
            }
            stroke="#3b82f6"
          />
        </g>
      </svg>
    </span>
  )
}
