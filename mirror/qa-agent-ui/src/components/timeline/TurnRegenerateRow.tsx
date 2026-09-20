import { RefreshCw } from 'lucide-react'
import { useSessionsStore } from '@/store/sessions'
import { cn } from '@/utils/cn'

interface Props {
  sessionId: string
  /** 该轮用户卡片（U）的 timeline item id —— 重答锚（regenerateTurn 入参） */
  userItemId: string
}

/**
 * 轮次重答入口行（add-turn-regenerate D2/D6）。
 *
 * 挂载点：每轮 U 卡正下方 + 轮尾末卡下方（ExecutionTimeline 按 planTurnRows
 * 渲染），两处触发同一动作（store.regenerateTurn）——主操作，常显非 hover。
 *
 * 状态门槛（D6）：running / interrupt_pending 隐藏入口（MUST NOT 打断在跑的
 * run）；aborted 可点（中断后重答主用例）；truncateState busy 期间禁用防连点。
 */
export function TurnRegenerateRow({ sessionId, userItemId }: Props) {
  const sessionStatus = useSessionsStore(s => s.sessions[sessionId]?.meta.status)
  const truncateState = useSessionsStore(s => s.sessions[sessionId]?.truncateState)

  const isRunning = sessionStatus === 'running' || sessionStatus === 'interrupt_pending'
  const busy = truncateState === 'cutting' || truncateState === 'reconnecting' || truncateState === 'confirming'

  if (isRunning) return null

  const handleClick = async () => {
    try {
      await useSessionsStore.getState().regenerateTurn(sessionId, userItemId)
    } catch (e) {
      alert('重新生成失败: ' + (e instanceof Error ? e.message : '未知错误'))
    }
  }

  return (
    <div className="py-1">
      <button
        onClick={handleClick}
        disabled={busy}
        className="flex items-center gap-1.5 text-[11px] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--primary))] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <RefreshCw className={cn('w-3 h-3', busy && 'animate-spin')} />
        重新生成回复
      </button>
    </div>
  )
}
