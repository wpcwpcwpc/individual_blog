import type { SessionStatus } from '@/types/api'
import { AlertTriangle, CheckCircle, Clock, Pause, XCircle } from 'lucide-react'

/**
 * Session status visual mapping — shared between SessionList (sidebar) and the
 * session views. Extracted from SessionList.tsx to avoid module-internal
 * constant duplication.
 */
export const STATUS_COLORS: Record<SessionStatus, string> = {
  idle:               'bg-slate-500',
  running:            'bg-blue-400 animate-pulse',
  interrupt_pending:  'bg-amber-400 animate-pulse',
  aborted:            'bg-amber-500',
  completed:          'bg-green-400',
  failed:             'bg-red-400',
  inactive:           'bg-slate-500',
  // 前端本地 UI 态（harden-create-session-entry）：新建会话提交后的占位卡
  creating:           'bg-violet-400 animate-pulse',
}

export const STATUS_LABELS: Record<SessionStatus, string> = {
  idle:               '空闲',
  running:            '运行中',
  interrupt_pending:  '待审核',
  aborted:            '已中断',
  completed:          '已完成',
  failed:             '失败',
  inactive:           '未激活',
  creating:           '创建中',
}

/**
 * 状态徽标完整映射 — 图标（running 为 null，
 * 由 DotsLoader 动态符文替代）+ 文案 + 文案色。SessionStatusBadge 与
 * SessionHeader 共用（自 SessionHeader 抽取的单一来源）。
 */
export const STATUS_CONFIG: Record<SessionStatus, {
  label: string;
  icon: typeof CheckCircle | null;
  color: string;
}> = {
  idle: {
    label: STATUS_LABELS.idle,
    icon: Clock,
    color: 'text-slate-400',
  },
  running: {
    label: STATUS_LABELS.running,
    icon: null,
    color: 'text-blue-400',
  },
  interrupt_pending: {
    label: STATUS_LABELS.interrupt_pending,
    icon: AlertTriangle,
    color: 'text-amber-400',
  },
  completed: {
    label: STATUS_LABELS.completed,
    icon: CheckCircle,
    color: 'text-green-400',
  },
  failed: {
    label: STATUS_LABELS.failed,
    icon: XCircle,
    color: 'text-red-400',
  },
  inactive: {
    label: STATUS_LABELS.inactive,
    icon: Clock,
    color: 'text-slate-400',
  },
  aborted: {
    label: STATUS_LABELS.aborted,
    icon: Pause,
    color: 'text-orange-400',
  },
  // 占位态：图标由占位卡自渲染（spinner），此处仅保证 Record 完整性
  creating: {
    label: STATUS_LABELS.creating,
    icon: null,
    color: 'text-violet-400',
  },
}
