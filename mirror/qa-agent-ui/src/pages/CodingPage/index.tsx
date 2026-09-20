/**
 * 编码工作台页面。
 *
 * 布局：单一对话流（ExecutionTimeline + MessageInputBar，复用既有会话基建：
 * 模型 slot 选择 / 会话恢复 / 断连恢复）。审批与澄清卡在 timeline 内承载决议，
 * 页头给出待审批徽标（会话恢复时 pending 审批可见可处理）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Hammer, Loader2, Plus, ShieldQuestion, Trash2 } from 'lucide-react'
import { createSession, deleteSession, getSessionMessages, getSessionMessagesTail } from '@/api/sessions'
import { CODING_AGENT_ID } from '@/config/agentIds'
import { ConfirmDeleteModal } from '@/components/common/ConfirmDeleteModal'
import { MessageInputBar } from '@/components/session/MessageInputBar'
import { SessionStatusBadge } from '@/components/session/SessionStatusBadge'
import { Tooltip } from '@/components/ui/Tooltip'
import { ExecutionTimeline } from '@/components/timeline/ExecutionTimeline'
import { streamPool } from '@/services/streamPool'
import { useAuthStore } from '@/store/auth'
import { useSessionsStore } from '@/store/sessions'
import { useCodingStore } from '@/stores/codingStore'
import { cn } from '@/utils/cn'
import { hydrateMessages } from '@/utils/hydrateHistory'
import { isHistoryPaginationEnabled } from '@/utils/historyPaging'
import type { SessionStatus } from '@/types/api'

// ── 工作台会话删除（交互与主列表 SessionList.DeleteButton 一致）────

function CodingSessionDeleteButton({ sessionId, status, title }: {
  sessionId: string
  status: SessionStatus
  title?: string
}) {
  const [showModal, setShowModal] = useState(false)
  const removeSession = useSessionsStore(s => s.removeSession)
  const email = useAuthStore(s => s.user?.email)

  const handleConfirm = () => {
    if (!email) throw new Error('用户未登录')
    streamPool.closeConnection(sessionId)
    setShowModal(false)
    // fix-session-io-blocking：乐观删除——UI 先行，HTTP 后台发出；失败时告警
    // 并从服务端对账（resyncDeletedSession）。
    removeSession(sessionId)
    deleteSession(email, sessionId).catch(() => {
      console.warn(`[CodingPage] deleteSession failed for ${sessionId} — reconciling from server`)
      void useSessionsStore.getState().resyncDeletedSession(email, sessionId)
    })
  }

  return (
    <>
      <Tooltip tip="删除会话">
        <button
          onClick={() => setShowModal(true)}
          className="flex-shrink-0 rounded p-1 text-slate-500 opacity-0 transition-all hover:bg-red-500/20 hover:text-red-400 group-hover:opacity-100"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </Tooltip>
      {showModal && (
        <ConfirmDeleteModal
          sessionTitle={title || sessionId.slice(0, 8)}
          isRunning={status === 'running'}
          onConfirm={handleConfirm}
          onCancel={() => setShowModal(false)}
        />
      )}
    </>
  )
}

function CodingLanding() {
  const navigate = useNavigate()
  const sessions = useSessionsStore(s => s.sessions)
  const addSession = useSessionsStore(s => s.addSession)
  const setActiveSession = useSessionsStore(s => s.setActiveSession)
  const user = useAuthStore(s => s.user)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const codingSessions = useMemo(
    () =>
      Object.values(sessions)
        .filter(s => s.meta.agent_name === CODING_AGENT_ID)
        .sort((a, b) => (b.meta.created_at ?? 0) - (a.meta.created_at ?? 0))
        .slice(0, 12),
    [sessions],
  )

  const handleCreate = useCallback(async () => {
    setCreating(true)
    setCreateError(null)
    try {
      const created = await createSession({ agent_name: CODING_AGENT_ID, mode: 'normal' })
      addSession({
        session_id: created.session_id,
        agent_name: CODING_AGENT_ID,
        mode: 'normal',
        status: 'idle',
        created_at: Date.now(),
      })
      setActiveSession(created.session_id)
      navigate(`/coding/${created.session_id}`)
    } catch (err: unknown) {
      // harden-create-session-entry D4：入口保持阻塞等待，但失败必须可见 +
      // 不产生 unhandled rejection（原先无 catch，用户只看到按钮复位）
      const msg = err instanceof Error ? err.message : '创建工作台会话失败，请检查后端连接'
      console.warn('[CodingPage] createSession failed: %s', msg)
      setCreateError(msg)
    } finally {
      setCreating(false)
    }
  }, [addSession, setActiveSession, navigate])

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-8">
      <div className="space-y-2 text-center">
        <Hammer className="mx-auto h-10 w-10 text-amber-400" />
        <h1 className="text-xl font-semibold text-slate-100">编码工作台</h1>
        <p className="text-sm text-slate-400">
          用自然语言描述需求，AI 读代码、改代码、跑命令、验结果；
          方案、写盘与不可逆命令在动手前弹审批卡，由你点头后执行。
        </p>
        <button
          onClick={() => void handleCreate()}
          disabled={creating || !user?.email}
          className="mx-auto flex items-center gap-2 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-500 disabled:opacity-50"
        >
          {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          新建编码会话
        </button>
        {createError && (
          <p className="text-xs text-red-400">{createError}</p>
        )}
      </div>

      {codingSessions.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-slate-500">最近的工作台会话</p>
          {codingSessions.map(s => (
            <div
              key={s.meta.session_id}
              className="group flex w-full items-center gap-2 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-3 py-2 text-sm transition-colors hover:bg-[hsl(var(--secondary))]"
            >
              <button
                onClick={() => navigate(`/coding/${s.meta.session_id}`)}
                className="min-w-0 flex-1 truncate text-left text-slate-300"
              >
                {s.meta.title || `${s.meta.session_id.slice(0, 12)}…`}
              </button>
              <span className="flex-shrink-0 text-xs text-slate-600">{s.meta.status}</span>
              <CodingSessionDeleteButton
                sessionId={s.meta.session_id}
                status={s.meta.status}
                title={s.meta.title}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CodingWorkspace() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const sessions = useSessionsStore(s => s.sessions)
  const pendingApprovals = useCodingStore(s => s.pendingApprovals)
  const refreshApprovals = useCodingStore(s => s.refreshApprovals)
  const appendApprovalCards = useSessionsStore(s => s.appendApprovalCards)

  const record = sessionId ? sessions[sessionId] : undefined

  // 审批待办刷新（进页面一次）
  useEffect(() => {
    if (!sessionId) return
    void refreshApprovals(sessionId)
  }, [sessionId, refreshApprovals])

  // 断连恢复：pending 审批以卡片形式回灌对话流 timeline（store 内按
  // approval_id 去重）。依赖 timeline 长度——hydrateSessionEvents 重建
  // timeline 后自动补挂（时间线竞态自愈）。
  useEffect(() => {
    if (!sessionId || pendingApprovals.length === 0) return
    appendApprovalCards(sessionId, pendingApprovals)
  }, [sessionId, pendingApprovals, appendApprovalCards, record?.timeline.length, record?.loadStatus])

  // run 终态对账兜底：run 暂停/结束即刷新审批待办，配合上方回灌 effect——
  // 即使 WS approval_pending 事件被漏收（连接抖动/续跑通道异常），卡片仍会在
  // 对话流出现（approval_pending 到达时 sessions store 也会触发一次刷新）。
  useEffect(() => {
    if (!sessionId || record?.meta.status !== 'completed') return
    void refreshApprovals(sessionId)
  }, [sessionId, record?.meta.status, refreshApprovals])

  // 会话历史加载（SessionPage 同款：paged 尾页 / kill-switch 全量）
  useEffect(() => {
    if (!sessionId || !record) return
    if (record.loadStatus === 'idle' && record.timeline.length === 0) {
      const store = useSessionsStore.getState()
      store.setLoadStatus(sessionId, 'loading_messages')
      const load = isHistoryPaginationEnabled()
        ? getSessionMessagesTail(sessionId).then(res => {
            useSessionsStore.getState().hydrateTailPage(
              sessionId,
              hydrateMessages(res.messages, record.meta.agent_name),
              res.page_meta ?? null,
            )
          })
        : getSessionMessages(sessionId).then(res => {
            const events = hydrateMessages(res.messages, record.meta.agent_name)
            useSessionsStore.getState().hydrateSessionEvents(sessionId, events)
          })
      void load.catch(() => store.setLoadStatus(sessionId, 'error'))
    }
  }, [sessionId, record])

  if (!sessionId || !record) {
    return <CodingLanding />
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-[hsl(var(--border))] px-4 py-2">
        <Hammer className="h-4 w-4 text-amber-400" />
        <span className="text-sm font-medium text-slate-200">编码工作台</span>
        <span className="truncate font-mono text-xs text-slate-600">{sessionId.slice(0, 10)}…</span>
        <SessionStatusBadge sessionId={sessionId} />
        <div className="flex-1" />
        {pendingApprovals.length > 0 && (
          <span className="flex items-center gap-1 rounded bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300">
            <ShieldQuestion className="h-3 w-3" />
            待审批 {pendingApprovals.length}
          </span>
        )}
        <button
          onClick={() => navigate('/coding')}
          className="rounded bg-[hsl(var(--secondary))] px-2 py-1 text-xs text-slate-300 hover:bg-[hsl(var(--accent))]"
        >
          返回工作台
        </button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        <ExecutionTimeline sessionId={sessionId} />
      </div>
      <MessageInputBar sessionId={sessionId} />
    </div>
  )
}

export function CodingPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  return (
    <div className={cn('h-full min-h-0', !sessionId && 'overflow-y-auto')}>
      {sessionId ? <CodingWorkspace /> : <CodingLanding />}
    </div>
  )
}
