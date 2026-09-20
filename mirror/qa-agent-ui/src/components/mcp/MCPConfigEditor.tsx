import { lazy, Suspense, useState, useEffect } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { getMCPConfig, updateMCPConfig } from '@/api/mcp'
import { useMCPStore } from '@/store/mcp'

// Lazy load Monaco to avoid large initial bundle
const MonacoEditor = lazy(() => import('@monaco-editor/react').then(m => ({ default: m.default })))

export function MCPConfigEditor() {
  const setShowConfigEditor = useMCPStore(s => s.setShowConfigEditor)
  const fetchServers = useMCPStore(s => s.fetchServers)

  const [content, setContent] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Fetch config on mount
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setFetchError(null)
      try {
        const cfg = await getMCPConfig()
        if (cancelled) return
        setContent(JSON.stringify(cfg, null, 2))
      } catch (err: unknown) {
        if (cancelled) return
        setFetchError(err instanceof Error ? err.message : '获取配置失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const handleSave = async () => {
    setSaveError(null)

    // Validate JSON
    let parsed: unknown
    try {
      parsed = JSON.parse(content)
    } catch {
      setSaveError('JSON 格式无效，请检查语法')
      return
    }

    setSaving(true)
    try {
      await updateMCPConfig(parsed as ReturnType<typeof JSON.parse>)
      await fetchServers()
      setShowConfigEditor(false)
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : '保存配置失败')
    } finally {
      setSaving(false)
    }
  }

  const handleCancel = () => {
    setShowConfigEditor(false)
  }

  // Fetch error state
  if (fetchError) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-4">
        <p className="text-sm text-red-400">{fetchError}</p>
        <button
          onClick={() => {
            setFetchError(null)
            setLoading(true)
            getMCPConfig()
              .then(cfg => { setContent(JSON.stringify(cfg, null, 2)); setLoading(false) })
              .catch(err => { setFetchError(err instanceof Error ? err.message : '获取配置失败'); setLoading(false) })
          }}
          className="text-xs text-violet-400 hover:text-violet-300 underline"
        >
          重试
        </button>
      </div>
    )
  }

  // Loading state
  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center gap-2">
        <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
        <span className="text-sm text-slate-400">加载配置...</span>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Warning banner */}
      <div className="flex items-start gap-2 px-3 py-2 bg-amber-500/10 border-b border-amber-500/20">
        <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0 mt-0.5" />
        <p className="text-[11px] text-amber-400/80 leading-tight">
          保存将全量替换现有 MCP 配置，请仔细检查内容。
        </p>
      </div>

      {/* Monaco editor */}
      <div className="flex-1 min-h-0">
        <Suspense fallback={
          <div className="h-full flex items-center justify-center">
            <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
          </div>
        }>
          <MonacoEditor
            height="100%"
            language="json"
            theme="vs-dark"
            value={content}
            onChange={(v) => setContent(v ?? '')}
            options={{
              minimap: { enabled: false },
              fontSize: 12,
              lineNumbers: 'on',
              scrollBeyondLastLine: false,
              wordWrap: 'on',
              tabSize: 2,
              automaticLayout: true,
            }}
          />
        </Suspense>
      </div>

      {/* Save error */}
      {saveError && (
        <div className="px-3 py-1.5 bg-red-500/10 border-t border-red-500/20">
          <p className="text-[11px] text-red-400">{saveError}</p>
        </div>
      )}

      {/* Footer actions */}
      <div className="flex gap-2 px-3 py-2 border-t border-[hsl(217.2_32.6%_17.5%)]">
        <button
          type="button"
          onClick={handleCancel}
          className="flex-1 py-1.5 rounded text-xs text-slate-400 border border-[hsl(217.2_32.6%_20%)] hover:text-white transition-colors"
        >
          取消
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="flex-1 py-1.5 rounded text-xs font-medium bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white transition-colors"
        >
          {saving ? '保存中...' : '保存配置'}
        </button>
      </div>
    </div>
  )
}
