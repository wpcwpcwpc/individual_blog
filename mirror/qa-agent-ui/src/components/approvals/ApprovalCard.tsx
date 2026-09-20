/**
 * 审批卡组件（通用）。
 *
 * kind 徽标 + content_md 渲染 + 替换预览（旧串 → 新串）+ 批准/拒绝 + 意见输入 +
 * 决议定格。决议后调 continue 续跑（SSE 事件回灌 timeline）。
 * 数据源：RunPausedEvent 携带的审批工具（approval_type 标记项）；approval_id
 * 缺失时从待办审批（codingStore）自愈匹配，匹配到即恢复可决议。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileCode2,
  Loader2,
  Play,
  ShieldQuestion,
  Terminal,
  XCircle,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { continueRun, getApproval, resolveApproval, type Approval } from '@/api/approvals'
import { useCodingStore } from '@/stores/codingStore'
import { useSessionsStore } from '@/store/sessions'
import type { ApprovalPendingItem } from '@/types/events'
import { apiErrorMessage } from '@/utils/apiError'
import { cn } from '@/utils/cn'

interface Props {
  item: ApprovalPendingItem
  sessionId: string
}

/** 审批门 kind（语义与后端 tools/coding_tools.py 的 kind 一致） */
const KIND_LABELS: Record<string, string> = {
  plan: '① 实现方案',
  design: '② 技术选型',
  file_write: '③ 整文件写入',
  file_edit: '④ 精确替换',
  command: '⑤ 不可逆命令',
}

const KIND_STYLES: Record<string, string> = {
  plan: 'bg-blue-500/15 text-blue-300 border-blue-500/30',
  design: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
  file_write: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  file_edit: 'bg-teal-500/15 text-teal-300 border-teal-500/30',
  command: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
}

function deriveKind(toolName: string | null, args: Record<string, unknown>): string {
  if (toolName === 'request_approval') return String(args.kind ?? 'plan')
  if (toolName === 'write_file') return 'file_write'
  if (toolName === 'edit_file') return 'file_edit'
  if (toolName === 'run_command') return 'command'
  return 'plan'
}

// diff 行配色走 inline style：浅色主题全局覆盖规则（index.css
// `body, body * { color: var(--text-primary) }` 等 unlayered 规则）会使
// Tailwind 颜色类失效，inline style 是唯一可靠通道（同决议态背景先例）。
// hex: red-50/red-700/red-500 与 green-50/green-700/green-500，浅色主题
// GitHub 式高对比。
const DIFF_ROW_STYLES = {
  del: { background: '#fef2f2', color: '#b91c1c', borderLeft: '2px solid #ef4444', paddingLeft: 6 },
  add: { background: '#f0fdf4', color: '#15803d', borderLeft: '2px solid #22c55e', paddingLeft: 6 },
  ctx: { color: '#888', paddingLeft: 8 },
} as const

/** 行级 diff（轻量：set-based 高亮，不引外部依赖） */
function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const { rows, addCount, delCount } = useMemo(() => {
    const oldLines = oldText ? oldText.split('\n') : []
    const newLines = newText ? newText.split('\n') : []
    const oldSet = new Set(oldLines)
    const newSet = new Set(newLines)
    const all: { kind: 'ctx' | 'del' | 'add'; text: string }[] = []
    for (const l of oldLines) all.push({ kind: newSet.has(l) && oldLines.filter(x => x === l).length === newLines.filter(x => x === l).length ? 'ctx' : 'del', text: l })
    for (const l of newLines) {
      if (oldSet.has(l) && all.filter(r => r.text === l && r.kind === 'del').length === 0) continue
      if (!oldSet.has(l)) all.push({ kind: 'add', text: l })
    }
    return {
      rows: all.slice(0, 400),
      addCount: all.filter(r => r.kind === 'add').length,
      delCount: all.filter(r => r.kind === 'del').length,
    }
  }, [oldText, newText])

  return (
    <div className="rounded bg-[hsl(222.2_84%_4%)] p-2 max-h-72 overflow-auto text-xs font-mono">
      {(addCount > 0 || delCount > 0) && (
        <div className="mb-1 flex gap-1">
          {addCount > 0 && (
            <span className="rounded px-1.5 py-0.5 text-[10px] font-medium" style={{ background: '#f0fdf4', color: '#15803d' }}>
              +{addCount} 行
            </span>
          )}
          {delCount > 0 && (
            <span className="rounded px-1.5 py-0.5 text-[10px] font-medium" style={{ background: '#fef2f2', color: '#b91c1c' }}>
              -{delCount} 行
            </span>
          )}
        </div>
      )}
      {rows.map((r, i) => (
        <div key={i} className="whitespace-pre-wrap leading-5" style={DIFF_ROW_STYLES[r.kind]}>
          <span className="inline-block w-4 select-none font-bold opacity-70">
            {r.kind === 'del' ? '-' : r.kind === 'add' ? '+' : ' '}
          </span>
          {r.text || ' '}
        </div>
      ))}
    </div>
  )
}

export function ApprovalCard({ item, sessionId }: Props) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resolution, setResolution] = useState(item.resolution)
  const [expanded, setExpanded] = useState(true)

  const markResolved = useCodingStore(s => s.markResolved)
  const pendingApprovals = useCodingStore(s => s.pendingApprovals)
  const approvalsLoading = useCodingStore(s => s.approvalsLoading)
  const refreshApprovals = useCodingStore(s => s.refreshApprovals)
  const appendEvent = useSessionsStore(s => s.appendEvent)
  const setMetaStatus = useSessionsStore(s => s.updateSessionMeta)
  // continue 回灌事件的 agent_name：meta 缺失时用通用兜底名，避免续跑输出
  // 挂在无意义标题下、与审批卡割裂
  const agentName = useSessionsStore(
    s => s.sessions[sessionId]?.meta.agent_name ?? 'CodingAgent',
  )

  // approval_id 缺失（事件回放/旧版事件/run_response.tools 误取前置工具）→
  // 从待办审批自愈：run_id → tool_name → 唯一 pending 逐级匹配，拿回
  // resolve 凭据与权威审批载荷（spec「审批流转与持久化」）。
  const missingId = !item.approval_id
  useEffect(() => {
    if (missingId) void refreshApprovals(sessionId)
  }, [missingId, sessionId, refreshApprovals])

  const healed = useMemo<Approval | null>(() => {
    if (!missingId || pendingApprovals.length === 0) return null
    if (item.run_id) {
      const byRun = pendingApprovals.find(a => a.run_id === item.run_id)
      if (byRun) return byRun
    }
    if (item.tool_name) {
      const byTool = pendingApprovals.find(a => a.tool_name === item.tool_name)
      if (byTool) return byTool
    }
    return pendingApprovals.length === 1 ? pendingApprovals[0] : null
  }, [missingId, pendingApprovals, item.run_id, item.tool_name])

  const approvalId = item.approval_id ?? healed?.id ?? null
  // DB 待办记录是权威载荷源（事件侧 args 可能取错 tools[0]）——heal 时整体替换
  const toolName = healed ? healed.tool_name : item.tool_name
  const args = ((healed ? healed.tool_args : item.tool_args) ?? {}) as Record<string, unknown>
  const runId = item.run_id || healed?.run_id || ''

  // 刷新回放对账（2026-08-28 真机 bug）：决议状态只存前端内存，刷新后回放的
  // approval_pending 事件无决议字段 → 已决议卡片回退「待审批」，点击触发后端
  // 409。后端 agno_approvals 表是权威源：挂载时按 approval_id 对账，非 pending
  // 即定格。catch：对账失败不阻塞渲染（点决议时后端 409 兜底）。
  useEffect(() => {
    const id = item.approval_id
    if (!id || resolution !== null) return
    let cancelled = false
    getApproval(id)
      .then((record) => {
        if (cancelled || record.status === 'pending') return
        if (record.status === 'approved' || record.status === 'rejected') {
          setResolution({ status: record.status, note: record.resolution_data?.note })
        } else {
          // expired/cancelled 无批准/拒绝语义，按拒绝定格并注明，防再点 409
          setResolution({ status: 'rejected', note: `审批已失效（${record.status}）` })
        }
        markResolved(record.id, record.status)
      })
      .catch(() => {
        // ignore: 对账失败维持现状，卡片仍可点（后端 409 文案兜底）
      })
    return () => {
      cancelled = true
    }
  }, [item.approval_id, resolution, markResolved])

  const kind = deriveKind(toolName, args)
  const kindLabel = KIND_LABELS[kind] ?? kind
  const kindStyle = KIND_STYLES[kind] ?? KIND_STYLES.plan

  // 标题：方案/选型取 title，写盘取路径，命令取命令原文
  const title = String(args.title ?? args.path ?? args.file_path ?? args.command ?? '审批请求')
  const contentMd = String(args.content_md ?? args.content ?? '')

  // 写盘 / 替换卡的目标路径；命令卡的 purpose（本次操作要达成什么）
  const filePath = kind === 'file_write' || kind === 'file_edit'
    ? String(args.path ?? args.file_path ?? '')
    : ''
  const purpose = String(args.purpose ?? '')

  /** 决议 + 续跑（SSE 事件回灌 timeline） */
  const handleResolve = useCallback(
    async (status: 'approved' | 'rejected') => {
      if (!approvalId || busy) return
      setBusy(true)
      setError(null)
      try {
        await resolveApproval(approvalId, status, note)
        setResolution({ status, note: note || undefined })
        markResolved(approvalId, status)
        // resolve 后 continue 续跑（run 自动从暂停点推进）；heal 兜底的
        // run_id 缺失时无法续跑——用户改从待办记录所在 run 处理或重发消息
        if (runId) {
          // 首个 SSE 事件到达即回灌 run_started，
          // 驱动 status='running'（输入框切中断按钮 + 工作台 header 运行符文）。
          // SSE 通道不经 WS，此前状态停留 completed 导致续跑全程「不可中断」。
          let runStartedSent = false
          // 终态跟踪：SSE 流结束但没收到任何终态事件（RunCompleted/Paused/
          // Error/Cancelled）= 连接异常中断（如后端续跑崩溃）→ 兜底提示，
          // 不再让用户面对「输出到一半静默停止」的无解释界面。
          let sawTerminal = false
          continueRun(
            sessionId,
            runId,
            ev => {
              if (!runStartedSent) {
                runStartedSent = true
                appendEvent(sessionId, {
                  event_type: 'run_started',
                  session_id: sessionId,
                  agent_name: agentName,
                })
              }
              // agno SSE 事件 → NormalizedEvent 回灌 timeline
              // RunContent 同帧分流（add-thinking-stream-display）：content →
              // token；reasoning_content → 思考增量。混合帧两者都回灌，
              // 与主路径（WS）行为镜像。
              if (ev.event === 'RunContent') {
                if (typeof ev.content === 'string' && ev.content) {
                  appendEvent(sessionId, {
                    event_type: 'token',
                    session_id: sessionId,
                    content: ev.content,
                  })
                }
                if (typeof ev.reasoning_content === 'string' && ev.reasoning_content) {
                  appendEvent(sessionId, {
                    event_type: 'reasoning_delta',
                    session_id: sessionId,
                    content: ev.reasoning_content,
                  })
                }
              } else if (ev.event === 'ToolCallStarted' && ev.tool) {
                appendEvent(sessionId, {
                  event_type: 'tool_start',
                  session_id: sessionId,
                  tool_name: String(ev.tool.tool_name ?? 'unknown'),
                  tool_call_id: String(ev.tool.tool_call_id ?? ''),
                  inputs: (ev.tool.tool_args as Record<string, unknown>) ?? {},
                })
              } else if (ev.event === 'ToolCallCompleted' && ev.tool) {
                appendEvent(sessionId, {
                  event_type: 'tool_end',
                  session_id: sessionId,
                  tool_name: String(ev.tool.tool_name ?? 'unknown'),
                  tool_call_id: String(ev.tool.tool_call_id ?? ''),
                  outputs: ev.tool.result ?? ev.content,
                  elapsed_ms: 0,
                })
              } else if (ev.event === 'RunCompleted') {
                sawTerminal = true
                appendEvent(sessionId, {
                  event_type: 'run_complete',
                  session_id: sessionId,
                  final_response: typeof ev.content === 'string' ? ev.content : '',
                })
                setMetaStatus(sessionId, { status: 'completed' })
              } else if (ev.event === 'RunError') {
                sawTerminal = true
                appendEvent(sessionId, {
                  event_type: 'run_error',
                  session_id: sessionId,
                  error: String(ev.content ?? 'continue error'),
                })
                // 续跑失败（含 agno RunNotFoundError — run 未落库）→ 会话置
                // failed，输入框/状态徽标可见，而不是停留在 running
                setMetaStatus(sessionId, { status: 'failed', error: String(ev.content ?? 'continue error') })
              } else if (ev.event === 'RunCancelled') {
                sawTerminal = true
                // D3 对接：后端 abort watcher 取消续跑 → aborted 终态
                appendEvent(sessionId, {
                  event_type: 'run_aborted',
                  session_id: sessionId,
                  agent_name: agentName,
                })
              } else if (ev.event === 'RunPaused') {
                sawTerminal = true
                // 续跑通道的下一个审批门（2026-08-28 真机：此前续跑暂停事件
                // 被丢弃 → 卡片只在刷新后出现）。工具选取与 stream_adapter
                // 同规则：approval_type/approval_id 标记项优先，回落 tools[0]。
                const tools = (ev.tools as Array<Record<string, unknown>> | undefined) ?? []
                const approvalTool =
                  tools.find(t => t?.approval_type != null || t?.approval_id != null) ?? tools[0]
                if (approvalTool) {
                  appendEvent(sessionId, {
                    event_type: 'approval_pending',
                    session_id: sessionId,
                    run_id: String(ev.run_id ?? ''),
                    approval_id: (approvalTool.approval_id as string) ?? null,
                    tool_name: (approvalTool.tool_name as string) ?? null,
                    tool_args: (approvalTool.tool_args as Record<string, unknown>) ?? null,
                    approval_type: (approvalTool.approval_type as string) ?? null,
                  })
                }
              }
            },
            msg => setError(msg),
            () => {
              // SSE 流结束且无任何终态事件 = 连接异常中断（后端续跑崩溃/
              // 网络断开）。兜底置 failed 并在 timeline 留痕，避免「输出到
              // 一半静默停止」无解释（2026-08-31 真机主诉）。
              if (sawTerminal) return
              const msg = '续跑连接中断（未收到完成事件）——请重新发送消息继续'
              appendEvent(sessionId, {
                event_type: 'run_error',
                session_id: sessionId,
                error: msg,
              })
              setMetaStatus(sessionId, { status: 'failed', error: msg })
            },
          )
        }
      } catch (err) {
        setError(apiErrorMessage(err))
      } finally {
        setBusy(false)
      }
    },
    [approvalId, runId, sessionId, busy, note, markResolved, appendEvent, setMetaStatus, agentName],
  )

  const resolved = resolution !== null

  return (
    <div
      className={cn(
        'rounded-lg border text-sm overflow-hidden',
        resolved
          ? resolution.status === 'approved'
            ? 'border-green-500/40'
            : 'border-red-500/40'
          : 'border-amber-500/50 bg-amber-500/5',
      )}
      style={
        // 决议态背景走 inline style：全局 CSS 有 `body [class*="bg-green-"] *
        // { color:#fff !important }` 兜底规则，容器带 bg-green-* 类会把整卡
        // 文字强制纯白 → 白底白字不可读（2026-08-28 真机）。
        resolved
          ? { backgroundColor: resolution.status === 'approved' ? '#f0fdf4' : '#fef2f2' } // hex: green-50 / red-50 浅底，配合深字（全局 text-primary）
          : undefined
      }
    >
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[hsl(var(--secondary))]"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-slate-500" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-slate-500" />
        )}
        <ShieldQuestion className="h-4 w-4 flex-shrink-0 text-amber-400" />
        <span className={cn('rounded border px-1.5 py-0.5 text-xs font-medium', kindStyle)}>{kindLabel}</span>
        <span className="truncate font-medium text-slate-200">{title}</span>
        <div className="flex-1" />
        {resolved ? (
          resolution.status === 'approved' ? (
            <span className="flex items-center gap-1 text-xs text-green-400">
              <CheckCircle2 className="h-3.5 w-3.5" /> 已批准
            </span>
          ) : (
            <span className="flex items-center gap-1 text-xs text-red-400">
              <XCircle className="h-3.5 w-3.5" /> 已拒绝
            </span>
          )
        ) : (
          <span className="text-xs text-amber-400">待审批</span>
        )}
      </button>

      {/* Body */}
      {expanded && (
        <div className="space-y-2 border-t border-[hsl(var(--border))] px-3 py-2">
          {/* 不可逆命令：命令原文 + 目的 */}
          {kind === 'command' && (
            <div className="space-y-1">
              <p className="text-xs text-slate-500">待执行命令</p>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded bg-[hsl(222.2_84%_4%)] p-2 text-xs text-slate-300">
                <Terminal className="mr-1 inline h-3 w-3" />
                {String(args.command ?? '')}
              </pre>
              <p className="text-xs text-slate-500">
                目的：<span className="text-slate-300">{purpose || '—'}</span>
              </p>
              <p className="text-xs text-slate-500">
                不可逆操作：批准即执行，无法撤销。
              </p>
            </div>
          )}

          {/* 方案/选型：content_md 渲染 */}
          {contentMd && (
            <div className="prose-invert max-h-80 max-w-none overflow-y-auto rounded bg-[hsl(222.2_84%_4%)] p-2 text-xs text-slate-300 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{contentMd}</ReactMarkdown>
            </div>
          )}

          {/* 整文件写入：目标路径 + 全文预览（旧内容对比端点已随业务通道下线） */}
          {kind === 'file_write' && (
            <div className="space-y-1">
              <p className="flex items-center gap-1 text-xs text-slate-500">
                <FileCode2 className="h-3 w-3" /> 写入文件：{filePath || '（未提供路径）'}
              </p>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-[hsl(222.2_84%_4%)] p-2 text-xs text-slate-300">
                {String(args.content ?? '')}
              </pre>
              {purpose && (
                <p className="text-xs text-slate-500">
                  目的：<span className="text-slate-300">{purpose}</span>
                </p>
              )}
            </div>
          )}

          {/* 精确替换：旧串 → 新串 */}
          {kind === 'file_edit' && (
            <div className="space-y-1">
              <p className="flex items-center gap-1 text-xs text-slate-500">
                <FileCode2 className="h-3 w-3" /> 替换预览（旧 → 新）：{filePath || '（未提供路径）'}
              </p>
              <DiffView
                oldText={String(args.old_string ?? '')}
                newText={String(args.new_string ?? '')}
              />
              {purpose && (
                <p className="text-xs text-slate-500">
                  目的：<span className="text-slate-300">{purpose}</span>
                </p>
              )}
            </div>
          )}

          {/* 决议定格：展示意见 */}
          {resolved ? (
            resolution.note ? (
              <p className="text-xs text-slate-500">
                意见：<span className="text-slate-300">{resolution.note}</span>
              </p>
            ) : null
          ) : (
            <>
              <textarea
                value={note}
                onChange={e => setNote(e.target.value)}
                placeholder={kind === 'file_write' || kind === 'file_edit' || kind === 'command' ? '拒绝意见（agent 将按意见修改后重新提交）' : '意见（可选）'}
                rows={2}
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(222.2_84%_4%)] px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={() => void handleResolve('approved')}
                  disabled={busy || !approvalId}
                  className="flex items-center gap-1 rounded bg-green-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-green-500 disabled:opacity-50"
                >
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                  批准
                </button>
                <button
                  onClick={() => void handleResolve('rejected')}
                  disabled={busy || !approvalId}
                  className="flex items-center gap-1 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
                >
                  <XCircle className="h-3.5 w-3.5" />
                  拒绝
                </button>
                <span className="flex items-center gap-1 text-[11px] text-slate-600">
                  <Play className="h-3 w-3" /> 决议后自动续跑
                </span>
              </div>
              {!approvalId && (
                <p className="text-xs text-amber-500">
                  {approvalsLoading
                    ? '正在同步待办审批…'
                    : '未在待办中匹配到对应审批（可能已处理或已过期）——刷新页面后待办审批会自动回显到对话流'}
                </p>
              )}
            </>
          )}

          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
      )}
    </div>
  )
}
