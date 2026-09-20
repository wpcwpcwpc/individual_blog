/**
 * ConfirmModal — 替代 window.confirm() 的轻量 React 确认弹窗。
 * 完全用 inline style 实现，不依赖 Tailwind 颜色类，主题跟随 CSS 变量。
 */
import { AlertTriangle } from 'lucide-react'

interface Props {
  message: string
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmModal({ message, onConfirm, onCancel }: Props) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {/* Backdrop */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundColor: 'rgba(0,0,0,0.5)',
        }}
        onClick={onCancel}
      />

      {/* Modal card */}
      <div
        style={{
          position: 'relative',
          backgroundColor: 'var(--surface-1)',
          border: '1px solid var(--border-color)',
          borderRadius: '12px',
          boxShadow: '0 8px 32px rgba(0,0,0,0.15)',
          padding: '24px',
          width: '100%',
          maxWidth: '400px',
          margin: '0 16px',
          color: 'var(--text-primary)',
        }}
      >
        {/* Icon + Message */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', marginBottom: '20px' }}>
          <div
            style={{
              flexShrink: 0,
              width: 36,
              height: 36,
              borderRadius: '50%',
              backgroundColor: '#fef3c7',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <AlertTriangle style={{ width: 18, height: 18, color: '#d97706' }} />
          </div>
          <p style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--text-primary)', margin: 0, paddingTop: 6 }}>
            {message}
          </p>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            style={{
              padding: '7px 16px',
              fontSize: 13,
              borderRadius: 8,
              border: '1px solid var(--border-color)',
              backgroundColor: 'var(--surface-2)',
              color: 'var(--text-primary)',
              cursor: 'pointer',
            }}
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            style={{
              padding: '7px 16px',
              fontSize: 13,
              borderRadius: 8,
              border: 'none',
              backgroundColor: '#dc2626',
              color: '#ffffff',
              cursor: 'pointer',
            }}
          >
            确认
          </button>
        </div>
      </div>
    </div>
  )
}
