import { useState } from 'react'
import { AlertTriangle, Loader2, X } from 'lucide-react'

interface Props {
  sessionTitle: string
  isRunning: boolean
  onConfirm: () => void | Promise<void>
  onCancel: () => void
}

export function ConfirmDeleteModal({ sessionTitle, isRunning, onConfirm, onCancel }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleConfirm = async () => {
    setLoading(true)
    setError(null)
    try {
      await onConfirm()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败，请重试')
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={loading ? undefined : onCancel} />

      {/* Modal — forced light theme via inline styles */}
      <div
        className="relative rounded-xl shadow-2xl w-full max-w-md mx-4 p-6"
        style={{
          backgroundColor: 'var(--surface-1)',
          border: '1px solid var(--border-color)',
          color: 'var(--text-primary)',
        }}
      >
        {/* Close button */}
        <button
          onClick={onCancel}
          disabled={loading}
          className="absolute top-4 right-4 disabled:opacity-50 transition-colors"
          style={{ color: 'var(--text-muted)' }}
        >
          <X className="w-4 h-4" />
        </button>

        {/* Icon + Title */}
        <div className="flex items-center gap-3 mb-4">
          <div
            className="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center"
            style={{ backgroundColor: '#fee2e2' }}
          >
            <AlertTriangle className="w-5 h-5" style={{ color: '#dc2626' }} />
          </div>
          <h2
            className="text-lg font-semibold"
            style={{ color: 'var(--text-primary)' }}
          >
            删除会话
          </h2>
        </div>

        {/* Body */}
        <div className="space-y-3 mb-6">
          {isRunning && (
            <div
              className="flex items-start gap-2 p-3 rounded-lg"
              style={{
                backgroundColor: '#fffbeb',
                border: '1px solid #fcd34d',
              }}
            >
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: '#d97706' }} />
              <p className="text-sm" style={{ color: '#92400e' }}>Agent 正在运行，将被强制终止。</p>
            </div>
          )}

          <p className="text-sm" style={{ color: 'var(--text-primary)' }}>
            此操作将永久删除该会话及其所有执行记录，无法恢复。
          </p>

          <div
            className="p-2.5 rounded-lg"
            style={{
              backgroundColor: 'var(--surface-2)',
              border: '1px solid var(--border-color)',
            }}
          >
            <p className="text-xs truncate" style={{ color: 'var(--text-secondary)' }}>
              会话：<span className="font-medium" style={{ color: 'var(--text-primary)' }}>{sessionTitle}</span>
            </p>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div
            className="mb-4 p-3 rounded-lg"
            style={{ backgroundColor: '#fef2f2', border: '1px solid #fca5a5' }}
          >
            <p className="text-sm" style={{ color: '#b91c1c' }}>{error}</p>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-lg transition-colors disabled:opacity-50"
            style={{
              color: 'var(--text-primary)',
              backgroundColor: 'var(--surface-2)',
              border: '1px solid var(--border-color)',
            }}
          >
            取消
          </button>
          <button
            onClick={handleConfirm}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-lg transition-colors disabled:opacity-50 flex items-center gap-2 min-w-[100px] justify-center"
            style={{ backgroundColor: '#dc2626', color: '#ffffff' }}
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                删除中…
              </>
            ) : (
              '确认删除'
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
