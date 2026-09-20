/**
 * 澄清卡组件。
 *
 * 两态渲染：feedback（ask_user 结构化选项，支持 multi_select + 自由输入备选）
 * / form（get_user_input 动态表单，按 field_type 渲染、全字段必填）。
 * 提交 → continueRun 携带 clarifications 作答（后端 requirements 注入路径，
 * 防静默护栏 409/422 由 onError 呈现）→ SSE 事件回灌 timeline → 本地定格。
 *
 * 回放：message 历史派生卡（sessions.ts deriveClarifyCardFromHistory）不带
 * run_id → 挂载时经 fetchPendingClarification 自愈对齐（模式同 ApprovalCard
 * 的 refreshApprovals 自愈）。已答卡（answer 非空）只读展示，无提交入口。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  HelpCircle,
  Loader2,
  Send,
} from 'lucide-react'
import {
  continueRun,
  fetchPendingClarification,
  type ClarificationAnswerPayload,
} from '@/api/approvals'
import { useSessionsStore } from '@/store/sessions'
import type { ClarifyCardItem, ClarifyField, ClarifyQuestion } from '@/types/events'
import { cn } from '@/utils/cn'

interface Props {
  item: ClarifyCardItem
  sessionId: string
}

const KIND_LABELS: Record<string, string> = {
  feedback: '澄清 · 选项',
  form: '澄清 · 表单',
}

const KIND_STYLES: Record<string, string> = {
  feedback: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
  form: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
}

/** 表单原始输入 → 按 field_type 强转（提交时调用，非法值抛错给 UI） */
function coerceFieldValue(field: ClarifyField, raw: string): unknown {
  const t = (field.field_type || 'str').toLowerCase()
  const trimmed = raw.trim()
  if (t === 'bool') return trimmed === 'true' || trimmed === '1' || trimmed === 'yes' || trimmed === 'on'
  if (t === 'int') {
    const n = Number(trimmed)
    if (!Number.isInteger(n)) throw new Error(`字段 ${field.name} 需要整数`)
    return n
  }
  if (t === 'float') {
    const n = Number(trimmed)
    if (Number.isNaN(n)) throw new Error(`字段 ${field.name} 需要数字`)
    return n
  }
  if (t === 'list' || t === 'dict') {
    try {
      return JSON.parse(trimmed)
    } catch {
      throw new Error(`字段 ${field.name} 需要合法 JSON（${t}）`)
    }
  }
  return raw
}

function ClarifyCard({ item, sessionId }: Props) {
  const [expanded, setExpanded] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [healedRunId, setHealedRunId] = useState<string | null>(null)
  // feedback：question → 已选 label 列表
  const [selected, setSelected] = useState<Record<string, string[]>>({})
  // feedback：question → 自由输入备选
  const [custom, setCustom] = useState<Record<string, string>>({})
  // form：field name → 原始输入
  const [values, setValues] = useState<Record<string, string>>({})

  const appendEvent = useSessionsStore(s => s.appendEvent)
  const setMetaStatus = useSessionsStore(s => s.updateSessionMeta)
  const markClarifyAnswered = useSessionsStore(s => s.markClarifyAnswered)
  const agentName = item.tool_name ? 'CodingAgent' : ''

  const answered = item.answer !== null

  // 回放卡 run_id 自愈：message 历史不带 run_id，从待答澄清端点对齐
  // （tool_call_id 匹配才采信——不匹配说明该卡所属 run 已被后续轮次取代）。
  useEffect(() => {
    if (item.run_id || answered) return
    let cancelled = false
    fetchPendingClarification(sessionId)
      .then(pending => {
        if (cancelled || !pending) return
        if (pending.tool_call_id === item.tool_call_id && pending.run_id) {
          setHealedRunId(pending.run_id)
        }
      })
      .catch(() => {
        // ignore: 自愈失败维持现状（用户重发消息可解锁）
      })
    return () => {
      cancelled = true
    }
  }, [item.run_id, item.tool_call_id, answered, sessionId])

  const effectiveRunId = item.run_id || healedRunId || ''

  const allQuestionsAnswered = useMemo(
    () =>
      item.kind === 'form' ||
      item.questions.every(q => {
        const picks = selected[q.question] ?? []
        return picks.length > 0 || (custom[q.question] ?? '').trim() !== ''
      }),
    [item.kind, item.questions, selected, custom],
  )

  const formComplete = useMemo(
    () =>
      item.kind !== 'form' ||
      item.fields.every(f => {
        const raw = (values[f.name] ?? '').trim()
        return f.field_type.toLowerCase() === 'bool' || raw !== ''
      }),
    [item.kind, item.fields, values],
  )

  const buildPayload = useCallback((): ClarificationAnswerPayload => {
    if (item.kind === 'feedback') {
      const selections: Record<string, string[]> = {}
      for (const q of item.questions) {
        const customText = (custom[q.question] ?? '').trim()
        const picks = selected[q.question] ?? []
        selections[q.question] = customText ? [...picks, customText] : picks
      }
      return { tool_call_id: item.tool_call_id, selections }
    }
    const out: Record<string, unknown> = {}
    for (const f of item.fields) {
      out[f.name] = coerceFieldValue(f, values[f.name] ?? '')
    }
    return { tool_call_id: item.tool_call_id, values: out }
  }, [item.kind, item.tool_call_id, item.questions, item.fields, selected, custom, values])

  /** 提交作答 + 续跑（SSE 事件回灌 timeline，镜像 ApprovalCard 通道） */
  const handleSubmit = useCallback(() => {
    if (busy || answered || !effectiveRunId) return
    if (!allQuestionsAnswered || !formComplete) {
      setError('所有问题/字段都必须填写后才能继续')
      return
    }
    setBusy(true)
    setError(null)
    let payload: ClarificationAnswerPayload
    try {
      payload = buildPayload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
      return
    }
    const answer = payload.selections
      ? { selections: payload.selections }
      : { values: (payload.values as Record<string, unknown>) ?? {} }
    let runStartedSent = false
    let sawTerminal = false
    continueRun(
      sessionId,
      effectiveRunId,
      ev => {
        if (!runStartedSent) {
          runStartedSent = true
          appendEvent(sessionId, {
            event_type: 'run_started',
            session_id: sessionId,
            agent_name: agentName,
          })
        }
        if (ev.event === 'RunContent') {
          if (typeof ev.content === 'string' && ev.content) {
            appendEvent(sessionId, { event_type: 'token', session_id: sessionId, content: ev.content })
          }
          if (typeof ev.reasoning_content === 'string' && ev.reasoning_content) {
            appendEvent(sessionId, { event_type: 'reasoning_delta', session_id: sessionId, content: ev.reasoning_content })
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
          appendEvent(sessionId, { event_type: 'run_error', session_id: sessionId, error: String(ev.content ?? 'continue error') })
          setMetaStatus(sessionId, { status: 'failed', error: String(ev.content ?? 'continue error') })
        } else if (ev.event === 'RunCancelled') {
          sawTerminal = true
          appendEvent(sessionId, { event_type: 'run_aborted', session_id: sessionId, agent_name: agentName })
        } else if (ev.event === 'RunPaused') {
          sawTerminal = true
          const tools = (ev.tools as Array<Record<string, unknown>> | undefined) ?? []
          const clarifyTool = [...tools]
            .reverse()
            .find(t => t?.user_feedback_schema != null || t?.user_input_schema != null)
          const approvalTool = tools.find(t => t?.approval_type != null || t?.approval_id != null)
          if (clarifyTool && !approvalTool) {
            // 续跑通道的下一个澄清门（对齐 stream_adapter 分流规则）
            const isFeedback = Array.isArray(clarifyTool.user_feedback_schema)
            appendEvent(sessionId, {
              event_type: 'clarification_request',
              session_id: sessionId,
              run_id: String(ev.run_id ?? ''),
              tool_call_id: String(clarifyTool.tool_call_id ?? ''),
              tool_name: (clarifyTool.tool_name as string) ?? null,
              kind: isFeedback ? 'feedback' : 'form',
              questions: isFeedback
                ? (clarifyTool.user_feedback_schema as ClarifyCardItem['questions'])
                : [],
              fields: !isFeedback
                ? (clarifyTool.user_input_schema as unknown as ClarifyCardItem['fields'])
                : [],
              tool_args: (clarifyTool.tool_args as Record<string, unknown>) ?? null,
            })
          } else if (approvalTool) {
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
      msg => {
        setError(msg)
        setBusy(false)
      },
      () => {
        // SSE 流结束无终态事件 = 连接异常中断（对齐 ApprovalCard 兜底）
        if (!sawTerminal) {
          const msg = '续跑连接中断（未收到完成事件）——请重新发送消息继续'
          appendEvent(sessionId, { event_type: 'run_error', session_id: sessionId, error: msg })
          setMetaStatus(sessionId, { status: 'failed', error: msg })
        }
        setBusy(false)
      },
      [payload],
    )
    // 作答定格本地即时生效（回放权威源 = message 历史，见 deriveClarifyCardFromHistory）
    markClarifyAnswered(sessionId, item.tool_call_id, answer)
  }, [
    busy, answered, effectiveRunId, allQuestionsAnswered, formComplete, buildPayload,
    sessionId, agentName, item.tool_call_id, appendEvent, setMetaStatus, markClarifyAnswered,
  ])

  const toggleOption = (question: ClarifyQuestion, label: string) => {
    setSelected(prev => {
      const picks = prev[question.question] ?? []
      if (question.multi_select) {
        return {
          ...prev,
          [question.question]: picks.includes(label) ? picks.filter(l => l !== label) : [...picks, label],
        }
      }
      return { ...prev, [question.question]: picks.includes(label) ? [] : [label] }
    })
  }

  const renderAnswerSummary = () => {
    const parts: string[] = []
    if (item.answer?.selections) {
      for (const [q, labels] of Object.entries(item.answer.selections)) {
        parts.push(`${q}：${labels.join('、') || '—'}`)
      }
    }
    if (item.answer?.values) {
      for (const [name, value] of Object.entries(item.answer.values)) {
        parts.push(`${name}：${typeof value === 'string' ? value : JSON.stringify(value)}`)
      }
    }
    return parts.length > 0 ? parts : ['已作答']
  }

  return (
    <div
      className={cn(
        'rounded-lg border text-sm overflow-hidden',
        answered
          ? 'border-indigo-500/40'
          : 'border-indigo-500/50 bg-indigo-500/5',
      )}
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
        <HelpCircle className="h-4 w-4 flex-shrink-0 text-indigo-400" />
        <span className={cn('rounded border px-1.5 py-0.5 text-xs font-medium', KIND_STYLES[item.kind] ?? KIND_STYLES.feedback)}>
          {KIND_LABELS[item.kind] ?? item.kind}
        </span>
        <span className="truncate font-medium text-slate-200">
          {item.kind === 'feedback' && item.questions.length > 0
            ? item.questions[0].header || item.questions[0].question
            : item.kind === 'form' && item.fields.length > 0
              ? `需要补充 ${item.fields.length} 项信息`
              : '需要你的输入'}
        </span>
        <div className="flex-1" />
        {answered ? (
          <span className="flex items-center gap-1 text-xs text-indigo-400">
            <CheckCircle2 className="h-3.5 w-3.5" /> 已作答
          </span>
        ) : (
          <span className="text-xs text-indigo-400">待回答</span>
        )}
      </button>

      {/* Body */}
      {expanded && (
        <div className="space-y-3 border-t border-[hsl(var(--border))] px-3 py-2">
          {/* feedback：结构化选项 */}
          {item.questions.map(q => (
            <div key={q.question} className="space-y-1.5">
              <p className="text-xs text-slate-400">
                {q.header ? <span className="mr-1 rounded bg-[hsl(var(--secondary))] px-1 py-0.5 text-[11px] text-slate-300">{q.header}</span> : null}
                {q.question}
                {q.multi_select ? <span className="ml-1 text-[11px] text-slate-500">（可多选）</span> : null}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {q.options.map(opt => {
                  const active = (selected[q.question] ?? []).includes(opt.label)
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      disabled={answered || busy}
                      onClick={() => toggleOption(q, opt.label)}
                      title={opt.description ?? undefined}
                      className={cn(
                        'rounded-full border px-2.5 py-1 text-xs transition-colors',
                        active
                          ? 'border-indigo-400 bg-indigo-500/20 text-indigo-200'
                          : 'border-[hsl(var(--border))] text-slate-300 hover:border-indigo-400/50',
                      )}
                    >
                      {opt.label}
                    </button>
                  )
                })}
              </div>
              {!answered && (
                <input
                  type="text"
                  value={custom[q.question] ?? ''}
                  onChange={e => setCustom(prev => ({ ...prev, [q.question]: e.target.value }))}
                  placeholder="或自行输入…"
                  className="w-full rounded border border-[hsl(var(--border))] bg-transparent px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-indigo-400/60 focus:outline-none"
                />
              )}
            </div>
          ))}

          {/* form：动态表单 */}
          {item.fields.map(f => {
            const t = (f.field_type || 'str').toLowerCase()
            const raw = values[f.name] ?? ''
            return (
              <div key={f.name} className="space-y-1">
                <p className="text-xs text-slate-400">
                  <span className="font-mono text-slate-300">{f.name}</span>
                  <span className="ml-1 text-[11px] text-slate-500">({f.field_type})</span>
                  {f.description ? <span className="ml-2 text-slate-500">{f.description}</span> : null}
                </p>
                {t === 'list' || t === 'dict' ? (
                  <textarea
                    disabled={answered || busy}
                    value={raw}
                    onChange={e => setValues(prev => ({ ...prev, [f.name]: e.target.value }))}
                    placeholder={t === 'list' ? '["a", "b"]' : '{"key": "value"}'}
                    rows={2}
                    className="w-full rounded border border-[hsl(var(--border))] bg-transparent px-2 py-1 font-mono text-xs text-slate placeholder:text-slate-600 focus:border-indigo-400/60 focus:outline-none"
                  />
                ) : (
                  <input
                    type={t === 'int' || t === 'float' ? 'number' : 'text'}
                    disabled={answered || busy}
                    value={raw}
                    onChange={e => setValues(prev => ({ ...prev, [f.name]: e.target.value }))}
                    className="w-full rounded border border-[hsl(var(--border))] bg-transparent px-2 py-1 text-xs text-slate-200 focus:border-indigo-400/60 focus:outline-none"
                  />
                )}
              </div>
            )
          })}

          {/* 已答摘要 */}
          {answered && (
            <div className="space-y-0.5 rounded bg-[hsl(222.2_84%_4%)] p-2 text-xs text-slate-300">
              {renderAnswerSummary().map(line => (
                <p key={line}>{line}</p>
              ))}
            </div>
          )}

          {error && <p className="text-xs text-red-400">{error}</p>}

          {!answered && (
            <button
              type="button"
              disabled={busy || !effectiveRunId}
              onClick={handleSubmit}
              className={cn(
                'flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors',
                busy
                  ? 'bg-indigo-500/30 text-indigo-200'
                  : 'bg-indigo-500 text-white hover:bg-indigo-400',
                !effectiveRunId && 'cursor-not-allowed opacity-50',
              )}
              title={!effectiveRunId ? 'run 信息缺失（重发消息可解锁）' : undefined}
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
              提交并继续
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default ClarifyCard
