/**
 * InfoModal — 替代 window.alert() 的轻量 React 信息弹窗。
 * 完全用 inline style 实现，不依赖 Tailwind 颜色类，主题跟随 CSS 变量。
 * 模式与项目内其它轻量弹窗一致：单 OK 按钮 + 状态图标。
 */
import { useEffect } from 'react'
import { AlertCircle, CheckCircle2, Info } from 'lucide-react'

export type InfoModalVariant = 'success' | 'error' | 'info'

interface Props {
  title: string
  message?: string
  /** 长内容（如文件名、错误详情），等宽字体展示 */
  detail?: string
  variant?: InfoModalVariant
  okText?: string
  onClose: () => void
}

const VARIANT_CFG: Record<InfoModalVariant, {
  Icon: typeof Info
  iconColor: string
  iconBg: string
}> = {
  success: { Icon: CheckCircle2, iconColor: '#16a34a', iconBg: 'rgba(22, 163, 74, 0.12)' },
  error: { Icon: AlertCircle, iconColor: '#dc2626', iconBg: 'rgba(220, 38, 38, 0.12)' },
  info: { Icon: Info, iconColor: 'var(--accent-color)', iconBg: 'rgba(91, 106, 245, 0.12)' },
}

export function InfoModal({
  title,
  message,
  detail,
  variant = 'info',
  okText = '知道了',
  onClose,
}: Props) {
  const { Icon, iconColor, iconBg } = VARIANT_CFG[variant]

  // Esc 关闭 + 自动聚焦 OK 按钮（无障碍 + Enter 提交）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Enter') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

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
        onClick={onClose}
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
          maxWidth: '440px',
          margin: '0 16px',
          color: 'var(--text-primary)',
        }}
      >
        {/* Title + Icon */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', marginBottom: '14px' }}>
          <div
            style={{
              flexShrink: 0,
              width: 32,
              height: 32,
              borderRadius: '50%',
              backgroundColor: iconBg,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Icon style={{ width: 18, height: 18, color: iconColor }} />
          </div>
          <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text-primary)', paddingTop: 4 }}>
            {title}
          </h3>
        </div>

        {/* Message */}
        {message && (
          <p style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text-secondary)', margin: '0 0 10px' }}>
            {message}
          </p>
        )}

        {/* Detail — mono font, selectable, scrollable for long content */}
        {detail && (
          <div
            style={{
              padding: '8px 12px',
              fontSize: 12,
              lineHeight: 1.5,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              color: 'var(--text-primary)',
              backgroundColor: 'var(--surface-2)',
              border: '1px solid var(--border-color)',
              borderRadius: '6px',
              maxHeight: 180,
              overflow: 'auto',
              wordBreak: 'break-all',
              userSelect: 'text',
            }}
          >
            {detail}
          </div>
        )}

        {/* Actions */}
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '18px' }}>
          <button
            onClick={onClose}
            autoFocus
            style={{
              padding: '7px 20px',
              fontSize: 13,
              borderRadius: 8,
              border: 'none',
              backgroundColor: 'var(--accent-color)',
              color: '#ffffff',
              cursor: 'pointer',
            }}
          >
            {okText}
          </button>
        </div>
      </div>
    </div>
  )
}
