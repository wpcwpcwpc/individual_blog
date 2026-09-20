import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { InterruptPayload } from '@/types/api'
import { reviewInterrupt } from '@/api/sessions'
import { streamPool } from '@/services/streamPool'
import { useSessionsStore } from '@/store/sessions'
import { InterruptCountdown } from './InterruptCountdown'

interface Props {
  sessionId: string
  payload: InterruptPayload
}

export function L3ResultPanel({ sessionId, payload }: Props) {
  const { result_summary, agent_name, agent_type, game_version, module } =
    payload.payload as {
      result_summary: string
      agent_name: string
      agent_type: string
      game_version?: string
      module?: string
    }

  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [notesError, setNotesError] = useState(false)

  const setInterrupt = useSessionsStore(s => s.setInterrupt)
  const lastSeqId = useSessionsStore(s => s.sessions[sessionId]?.lastSeqId ?? 0)

  const submit = async (action: string) => {
    // notes required for fail
    if (action === 'fail' && !notes.trim()) {
      setNotesError(true)
      return
    }
    setNotesError(false)
    setLoading(true)
    try {
      // Ensure WS connection is alive before review (fire-and-forget)
      streamPool.ensureConnection(sessionId, lastSeqId)
      await reviewInterrupt(sessionId, {
        action,
        notes: notes || undefined,
      })
      setInterrupt(sessionId, null)
    } catch (err) {
      console.error('[L3Panel] review failed', err)
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
          <span className="font-semibold text-sm text-slate-200">结果验收</span>
        </div>
        <InterruptCountdown payload={payload} />
      </div>

      {/* Agent info bar */}
      <div className="flex flex-wrap gap-2 px-4 py-2 bg-[hsl(217.2_32.6%_8%)] border-b border-[hsl(217.2_32.6%_17.5%)] flex-shrink-0">
        <span className="text-xs text-slate-400">
          <span className="text-slate-600">Agent:</span> {agent_name}
        </span>
        <span className="text-xs text-slate-400">
          <span className="text-slate-600">类型:</span> {agent_type}
        </span>
        {game_version && (
          <span className="text-xs text-slate-400">
            <span className="text-slate-600">版本:</span> {game_version}
          </span>
        )}
        {module && (
          <span className="text-xs text-slate-400">
            <span className="text-slate-600">模块:</span> {module}
          </span>
        )}
      </div>

      {/* Result summary (Markdown) */}
      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        <div className="prose prose-invert prose-sm max-w-none text-slate-200">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {result_summary ?? '*（无结果内容）*'}
          </ReactMarkdown>
        </div>
      </div>

      {/* Notes + Actions */}
      <div className="flex-shrink-0 px-4 py-3 border-t border-[hsl(217.2_32.6%_17.5%)] space-y-3">
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            QA 备注{' '}
            {notesError && <span className="text-red-400">（标记失败时备注必填）</span>}
          </label>
          <textarea
            value={notes}
            onChange={e => { setNotes(e.target.value); setNotesError(false) }}
            placeholder="填写验收备注..."
            rows={2}
            className={`w-full bg-[hsl(217.2_32.6%_10%)] border rounded-md px-3 py-2 text-xs text-slate-300 placeholder:text-slate-600 focus:outline-none resize-none ${notesError ? 'border-red-500/50 focus:border-red-500' : 'border-[hsl(217.2_32.6%_17.5%)] focus:border-slate-500'}`}
          />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button onClick={() => submit('rerun')} disabled={loading}
            className="py-2 rounded-md border border-blue-500/30 text-blue-400 hover:bg-blue-500/10 text-sm font-medium transition-colors disabled:opacity-50">
            🔄 重跑
          </button>
          <button onClick={() => submit('investigate')} disabled={loading}
            className="py-2 rounded-md border border-orange-500/30 text-orange-400 hover:bg-orange-500/10 text-sm font-medium transition-colors disabled:opacity-50">
            🔍 调查
          </button>
          <button onClick={() => submit('fail')} disabled={loading}
            className="py-2 rounded-md border border-red-500/30 text-red-400 hover:bg-red-500/10 text-sm font-medium transition-colors disabled:opacity-50">
            ❌ 标记失败
          </button>
          <button onClick={() => submit('pass')} disabled={loading}
            className="py-2 rounded-md bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors disabled:opacity-50">
            ✅ 通过
          </button>
        </div>
      </div>
    </div>
  )
}
