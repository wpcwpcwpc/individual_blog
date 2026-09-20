import { lazy, Suspense, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { InterruptPayload } from '@/types/api'
import { reviewInterrupt } from '@/api/sessions'
import { streamPool } from '@/services/streamPool'
import { useSessionsStore } from '@/store/sessions'
import { InterruptCountdown } from './InterruptCountdown'

// Lazy load Monaco to avoid large initial bundle
const MonacoEditor = lazy(() => import('@monaco-editor/react').then(m => ({ default: m.default })))

// Primary content key priority (matches backend permission_hook.py)
const PRIMARY_KEYS = ['command', 'script', 'content', 'code', 'query']

function inferLanguage(toolName: string): string {
  if (toolName === 'bash') return 'shell'
  return 'plaintext'
}

interface Props {
  sessionId: string
  payload: InterruptPayload
}

export function L2PreviewPanel({ sessionId, payload }: Props) {
  const { tool_name, tool_args } = payload.payload as {
    tool_name: string
    tool_args: Record<string, unknown>
    preview?: string
    mode?: string
    note?: string
  }

  // Find primary editable field
  const primaryKey = PRIMARY_KEYS.find(k => k in (tool_args ?? {}))
  const primaryValue = primaryKey ? String((tool_args as Record<string, unknown>)[primaryKey] ?? '') : null

  const [editorContent, setEditorContent] = useState(primaryValue ?? '')
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [showRawArgs, setShowRawArgs] = useState(false)

  const setInterrupt = useSessionsStore(s => s.setInterrupt)
  const lastSeqId = useSessionsStore(s => s.sessions[sessionId]?.lastSeqId ?? 0)
  const language = inferLanguage(tool_name ?? '')

  const submit = async (action: string) => {
    setLoading(true)
    try {
      // Ensure WS connection is alive before review (fire-and-forget)
      streamPool.ensureConnection(sessionId, lastSeqId)
      await reviewInterrupt(sessionId, {
        action,
        modified_content: action === 'modify' ? editorContent : undefined,
        notes: notes || undefined,
      })
      setInterrupt(sessionId, null)
    } catch (err) {
      console.error('[L2Panel] review failed', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[hsl(217.2_32.6%_17.5%)] flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-amber-400">⚠️</span>
          <span className="font-semibold text-sm text-amber-300">操作审核</span>
          <span className="bg-amber-500/20 text-amber-300 border border-amber-500/30 text-xs px-2 py-0.5 rounded font-mono">
            {tool_name}
          </span>
        </div>
        <InterruptCountdown payload={payload} />
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {/* Raw args (collapsible) */}
        <div>
          <button
            onClick={() => setShowRawArgs(!showRawArgs)}
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 transition-colors mb-1.5"
          >
            {showRawArgs ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            原始参数（只读）
          </button>
          {showRawArgs && (
            <pre className="text-xs text-slate-400 bg-[hsl(222.2_84%_4%)] rounded-md p-3 overflow-x-auto max-h-32">
              {JSON.stringify(tool_args, null, 2)}
            </pre>
          )}
        </div>

        {/* Editable content */}
        {primaryKey && (
          <div className="flex-shrink-0">
            <p className="text-xs text-slate-400 mb-1.5">
              可编辑字段：<span className="font-mono text-amber-300">{primaryKey}</span>
            </p>
            <div className="rounded-md overflow-hidden border border-[hsl(217.2_32.6%_17.5%)]" style={{ height: 260 }}>
              <Suspense fallback={
                <div className="h-full flex items-center justify-center text-xs text-slate-500">
                  加载编辑器...
                </div>
              }>
                <MonacoEditor
                  height="260px"
                  language={language}
                  theme="vs-dark"
                  value={editorContent}
                  onChange={v => setEditorContent(v ?? '')}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 12,
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    wordWrap: 'on',
                  }}
                />
              </Suspense>
            </div>
          </div>
        )}

        {!primaryKey && (
          <div className="text-xs text-slate-500 bg-[hsl(217.2_32.6%_8%)] rounded-md p-3">
            此工具无主内容字段，仅展示参数（如需修改请取消后手动调整）
          </div>
        )}

        {/* Notes */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">审核备注（可选）</label>
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            placeholder="填写审核备注..."
            rows={2}
            className="w-full bg-[hsl(217.2_32.6%_10%)] border border-[hsl(217.2_32.6%_17.5%)] rounded-md px-3 py-2 text-xs text-slate-300 placeholder:text-slate-600 focus:outline-none focus:border-amber-500/50 resize-none"
          />
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2 px-4 py-3 border-t border-[hsl(217.2_32.6%_17.5%)] flex-shrink-0">
        <button
          onClick={() => submit('cancel')}
          disabled={loading}
          className="flex-1 py-2 rounded-md border border-red-500/30 text-red-400 hover:bg-red-500/10 text-sm font-medium transition-colors disabled:opacity-50"
        >
          ❌ 取消
        </button>
        <button
          onClick={() => submit('modify')}
          disabled={loading || !primaryKey}
          className="flex-1 py-2 rounded-md border border-amber-500/30 text-amber-300 hover:bg-amber-500/10 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          ✏️ 修改后执行
        </button>
        <button
          onClick={() => submit('confirm')}
          disabled={loading}
          className="flex-1 py-2 rounded-md bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
        >
          ✅ 确认执行
        </button>
      </div>
    </div>
  )
}
