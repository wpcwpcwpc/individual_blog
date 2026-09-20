import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * ErrorBoundary — global render-error trap.
 *
 * Prevents a single uncaught render exception (e.g. a status/field mismatch
 * between backend and frontend) from unmounting the whole React tree and
 * leaving a blank white page. Shows a recoverable fallback instead.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.warn('[ErrorBoundary] Uncaught render error:', error, info.componentStack)
  }

  private handleReload = (): void => {
    window.location.reload()
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="fixed inset-0 flex flex-col items-center justify-center gap-4 bg-[hsl(var(--background))] p-6 text-center">
          <AlertTriangle className="w-10 h-10 text-amber-400" />
          <div className="space-y-1">
            <p className="text-sm font-semibold text-[hsl(var(--foreground))]">页面渲染出错</p>
            <p className="text-xs text-[hsl(var(--muted-foreground))] max-w-md break-all">
              {this.state.error.message}
            </p>
          </div>
          <button
            onClick={this.handleReload}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium bg-[hsl(var(--primary))] text-white hover:opacity-90 transition-opacity"
          >
            <RotateCw className="w-3.5 h-3.5" />
            刷新重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
