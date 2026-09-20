import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { InterruptPayload } from '@/types/api'
import { resumeSession, abortSession } from '@/api/sessions'
import { streamPool } from '@/services/streamPool'
import { useSessionsStore } from '@/store/sessions'
import { InterruptCountdown } from './InterruptCountdown'

interface Props {
  sessionId: string
  payload: InterruptPayload
}

export function ReviewGatePanel({ sessionId, payload }: Props) {
  const { phase, artifact_type, llm_summary, completed_phases } =
    payload.payload as {
      phase?: string
      artifact_type?: string
      llm_summary?: string
      completed_phases?: string[]
    }

  const [feedback, setFeedback] = useState('')
  const [loading, setLoading] = useState(false)
  const [feedbackError, setFeedbackError] = useState(false)

  const setInterrupt = useSessionsStore(s => s.setInterrupt)
  const lastSeqId = useSessionsStore(s => s.sessions[sessionId]?.lastSeqId ?? 0)

  const handleApprove = async () => {
    setFeedbackError(false)
    setLoading(true)
    // harden-resume-interrupt-chain 3.1 — optimistic close BEFORE the network;
    // rollback restores the card (with typed feedback) on failure.
    setInterrupt(sessionId, null)
    try {
      // Ensure WS connection is alive before resume (fire-and-forget)
      streamPool.ensureConnection(sessionId, lastSeqId)
      await resumeSession(sessionId, {
        type: 'review_approved',
        approved: true,
        message: feedback || undefined,
      })
    } catch (err) {
      console.error('[ReviewGatePanel] approve failed', err)
      setInterrupt(sessionId, payload)
    } finally {
      setLoading(false)
    }
  }

  const handleReject = async () => {
    if (!feedback.trim()) {
      setFeedbackError(true)
      return
    }
    setFeedbackError(false)
    setLoading(true)
    setInterrupt(sessionId, null)
    try {
      // Ensure WS connection is alive before resume (fire-and-forget)
      streamPool.ensureConnection(sessionId, lastSeqId)
      await resumeSession(sessionId, {
        type: 'review_approved',
        approved: false,
        message: feedback,
      })
    } catch (err) {
      console.error('[ReviewGatePanel] reject failed', err)
      setInterrupt(sessionId, payload)
    } finally {
      setLoading(false)
    }
  }

  const handleAbort = async () => {
    setLoading(true)
    setInterrupt(sessionId, null)
    try {
      await abortSession(sessionId)
      // fix-abort-latency D3：POST 成功即乐观渲染「中断中」（timeline 过渡标记）；
      // 状态收敛（→ aborted）仍由 WS run_aborted 事件唯一决定（3.2）。
      useSessionsStore.getState().markAborting(sessionId)
    } catch (err) {
      console.error('[ReviewGatePanel] abort failed', err)
      setInterrupt(sessionId, payload)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[hsl(217.2_32.6%_17.5%)] flex-shrink-0">
        <div className="flex items-center gap-2">
          <span>📋</span>
          <span className="font-semibold text-sm text-slate-200">阶段审核</span>
        </div>
        <InterruptCountdown payload={payload} />
      </div>

      {/* Phase info bar */}
      <div className="flex flex-wrap gap-2 px-4 py-2 bg-[hsl(217.2_32.6%_8%)] border-b border-[hsl(217.2_32.6%_17.5%)] flex-shrink-0">
        {phase && (
          <span className="text-xs text-slate-400">
            <span className="text-slate-600">阶段:</span> {phase}
          </span>
        )}
        {artifact_type && (
          <span className="text-xs text-slate-400">
            <span className="text-slate-600">产出类型:</span>{' '}
            <span className="bg-violet-500/20 text-violet-300 px-1.5 py-0.5 rounded text-[10px] font-medium">
              {artifact_type}
            </span>
          </span>
        )}
        {completed_phases && completed_phases.length > 0 && (
          <span className="text-xs text-slate-400">
            <span className="text-slate-600">已完成:</span> {completed_phases.join(' → ')}
          </span>
        )}
      </div>

      {/* LLM Summary (Markdown) */}
      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        <div className="prose prose-invert prose-sm max-w-none text-slate-200">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {llm_summary ?? '*（无审核内容）*'}
          </ReactMarkdown>
        </div>
      </div>

      {/* Feedback + Actions */}
      <div className="flex-shrink-0 px-4 py-3 border-t border-[hsl(217.2_32.6%_17.5%)] space-y-3">
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            审核意见{' '}
            {feedbackError && <span className="text-red-400">（驳回时意见必填）</span>}
          </label>
          <textarea
            value={feedback}
            onChange={e => { setFeedback(e.target.value); setFeedbackError(false) }}
            placeholder="填写审核意见（通过时可选，驳回时必填）..."
            rows={2}
            className={`w-full bg-[hsl(217.2_32.6%_10%)] border rounded-md px-3 py-2 text-xs text-slate-300 placeholder:text-slate-600 focus:outline-none resize-none ${feedbackError ? 'border-red-500/50 focus:border-red-500' : 'border-[hsl(217.2_32.6%_17.5%)] focus:border-slate-500'}`}
          />
        </div>

        <div className="grid grid-cols-3 gap-2">
          <button
            onClick={handleAbort}
            disabled={loading}
            className="py-2 rounded-md border border-red-500/30 text-red-400 hover:bg-red-500/10 text-sm font-medium transition-colors disabled:opacity-50"
          >
            🛑 终止
          </button>
          <button
            onClick={handleReject}
            disabled={loading}
            className="py-2 rounded-md border border-orange-500/30 text-orange-400 hover:bg-orange-500/10 text-sm font-medium transition-colors disabled:opacity-50"
          >
            ❌ 驳回
          </button>
          <button
            onClick={handleApprove}
            disabled={loading}
            className="py-2 rounded-md bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
          >
            ✅ 通过
          </button>
        </div>
      </div>
    </div>
  )
}
