import { Tooltip } from "@/components/ui/Tooltip";
import { useState } from 'react';
import { AlertTriangle, Hash, Cpu, Layers, Trash2, Pause, Square, Loader2 } from 'lucide-react';
import { abortSession } from '@/api/sessions';
import { useSessionsStore, isAborting } from '@/store/sessions';
import { cn } from '@/utils/cn';
import { DisplaySettingsButton } from './DisplaySettingsButton';
import { DotsLoader } from './SessionStatusBadge';
import { STATUS_CONFIG } from '@/utils/sessionStatus';

// 状态符文/映射单一来源迁移
// （DotsLoader → SessionStatusBadge，STATUS_CONFIG → utils/sessionStatus），
// 此处 re-export 保持既有 import 路径兼容。
export { DotsLoader } from './SessionStatusBadge';
export { STATUS_CONFIG } from '@/utils/sessionStatus';

interface Props {
  sessionId: string;
  onDelete: () => void;
}
export function SessionHeader({
  sessionId,
  onDelete
}: Props) {
  const record = useSessionsStore(s => s.sessions[sessionId]);
  const [aborting, setAborting] = useState(false);
  if (!record) return null;
  const {
    meta
  } = record;
  // fix-abort-latency D2：「中断中」过渡标记（终态到达自动清除）
  const abortingRun = isAborting(record);
  const handleAbort = async () => {
    if (aborting) return;
    setAborting(true);
    try {
      await abortSession(sessionId);
      // fix-abort-latency D3：POST 成功即乐观渲染「中断中」，不等 run_aborted
      useSessionsStore.getState().markAborting(sessionId);
    } catch (err) {
      console.error('[SessionHeader] abort failed', err);
    } finally {
      setAborting(false);
    }
  };
  const statusConfig = STATUS_CONFIG[meta.status];
  const StatusIcon = statusConfig.icon;
  return <div className="flex items-center gap-4 px-4 py-3 border-b border-[hsl(var(--border))] bg-[hsl(var(--card))] flex-shrink-0">
      {/* Status icon + Agent name */}
      <div className="flex items-center gap-2">
        {StatusIcon ? <StatusIcon className={cn('w-4 h-4', statusConfig.color)} /> : <DotsLoader className={cn('w-4 h-4', statusConfig.color)} />}
        <span className="font-semibold text-sm text-[hsl(var(--foreground))]">{meta.agent_name}</span>
        <span className={cn('text-xs px-1.5 py-0.5 rounded font-medium', meta.mode === 'coordinator' ? 'bg-violet-500/10 text-[hsl(var(--primary))]' : 'bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]')}>
          {meta.mode === 'coordinator' ? 'Coordinator' : 'Normal'}
        </span>
      </div>

      {/* Divider */}
      <div className="h-4 w-px bg-[hsl(var(--border))]" />

      {/* Meta info */}
      <div className="flex items-center gap-3 text-xs text-[hsl(var(--muted-foreground))]">
        <span className="flex items-center gap-1">
          <Hash className="w-3 h-3" />
          {meta.session_id.slice(0, 8)}
        </span>
        {meta.game_version && <span className="flex items-center gap-1">
            <Cpu className="w-3 h-3" />
            {meta.game_version}
          </span>}
        {meta.module && <span className="flex items-center gap-1">
            <Layers className="w-3 h-3" />
            {meta.module}
          </span>}
      </div>

      {/* Status label */}
      <div className={cn('text-xs font-medium', statusConfig.color)}>
        {statusConfig.label}
      </div>

      {/* Interrupt pending banner */}
      {meta.status === 'interrupt_pending' && <div className="flex items-center gap-1.5 bg-amber-500/10 border border-amber-500/30 rounded-md px-2 py-1 text-xs text-amber-400">
          <AlertTriangle className="w-3.5 h-3.5" />
          等待审核
        </div>}

      {/* Interrupted banner */}
      {meta.status === 'aborted' && <div className="flex items-center gap-1.5 bg-orange-500/10 border border-orange-500/30 rounded-md px-2 py-1 text-xs text-orange-400">
          <Pause className="w-3.5 h-3.5" />
          会话已中断，可发送消息恢复执行
        </div>}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Abort button — visible when running */}
      {meta.status === 'running' && <Tooltip tip={abortingRun ? '正在中断' : '中断执行'}><button onClick={handleAbort} disabled={aborting || abortingRun} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50">
          {aborting || abortingRun ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
          {abortingRun ? '中断中' : '中断'}
        </button></Tooltip>}

      {/* Display settings */}
      <DisplaySettingsButton />

      {/* Delete button */}
      <Tooltip tip="删除会话"><button onClick={onDelete} className="p-1.5 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors">
        <Trash2 className="w-4 h-4" />
      </button></Tooltip>
    </div>;
}
