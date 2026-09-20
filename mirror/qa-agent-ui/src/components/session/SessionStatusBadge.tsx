/**
 * 会话运行状态徽标。
 *
 * DotsLoader 动态符文与状态映射的渲染组件：普通会话 header 与编码工作台 header
 * 共用同一份语义；running 态附「中断」按钮，复用 POST /sessions/{id}/abort
 * （对消息发送与审批续跑通道均生效）。状态映射常量在
 * `@/utils/sessionStatus`（单一来源）。
 */
import { useState } from 'react'
import { Loader2, Square } from 'lucide-react'
import { abortSession } from '@/api/sessions'
import { useSessionsStore, isAborting } from '@/store/sessions'
import { STATUS_CONFIG } from '@/utils/sessionStatus'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/utils/cn'

/** Three-dot bouncing loader — the conventional "in progress" waiting
 *  animation used across modern web UIs. Replaces the previous RefreshCw
 *  spinning arrow, which read as a "refresh" affordance, not "waiting". */
export function DotsLoader({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-[3px]', className)} role="status" aria-label="进行中">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="w-[3px] h-[3px] rounded-full bg-current animate-[dots-bounce_1.2s_ease-in-out_infinite]"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
    </span>
  )
}

interface Props {
  sessionId: string;
}

/** 会话状态徽标：状态符文（running 为动态 DotsLoader）+ 文案 + running 中断按钮。 */
export function SessionStatusBadge({ sessionId }: Props) {
  const status = useSessionsStore(s => s.sessions[sessionId]?.meta.status);
  const abortingRun = useSessionsStore(s => isAborting(s.sessions[sessionId]));
  const [aborting, setAborting] = useState(false);
  if (!status) return null;

  const statusConfig = STATUS_CONFIG[status];
  const StatusIcon = statusConfig.icon;

  const handleAbort = async () => {
    if (aborting) return;
    setAborting(true);
    try {
      await abortSession(sessionId);
      // fix-abort-latency D3：POST 成功即乐观渲染「中断中」，不等 run_aborted
      useSessionsStore.getState().markAborting(sessionId);
    } catch (err) {
      console.error('[SessionStatusBadge] abort failed', err);
    } finally {
      setAborting(false);
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      {StatusIcon ? (
        <StatusIcon className={cn('w-4 h-4', statusConfig.color)} />
      ) : (
        <DotsLoader className={cn('w-4 h-4', statusConfig.color)} />
      )}
      <span className={cn('text-xs font-medium', abortingRun ? 'text-orange-400' : statusConfig.color)}>
        {abortingRun ? '中断中…' : statusConfig.label}
      </span>
      {status === 'running' && (
        <Tooltip tip={abortingRun ? '正在中断' : '中断执行'}>
          <button
            onClick={() => void handleAbort()}
            disabled={aborting || abortingRun}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50"
          >
            {aborting || abortingRun ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
            {abortingRun ? '中断中' : '中断'}
          </button>
        </Tooltip>
      )}
    </div>
  );
}
