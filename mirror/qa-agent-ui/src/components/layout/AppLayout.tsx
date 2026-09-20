import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { useResizable } from '@/hooks/useResizable'
import { useSidebarStore } from '@/store/sidebar'
import { ResizeHandle } from '@/components/ui/ResizeHandle'

const COLLAPSED_WIDTH = 56

interface AppLayoutProps {
  children: ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  const { width: sidebarWidth, isDragging, handleProps } = useResizable({
    defaultWidth: 240,
    minWidth: 180,
    maxWidth: 400,
    storageKey: 'qa-ui:sidebar-width',
    direction: 'right',
  })
  const collapsed = useSidebarStore(s => s.collapsed)
  const actualWidth = collapsed ? COLLAPSED_WIDTH : sidebarWidth

  return (
    <div className="flex h-screen bg-[hsl(var(--background))] text-[hsl(var(--foreground))] overflow-hidden">
      {/* Sidebar — 折叠态宽度 56px, 展开态可拖拽调整 */}
      <aside
        className="flex-shrink-0 flex flex-col transition-[width] duration-200"
        style={{ width: actualWidth, backgroundColor: 'var(--surface-3)', borderRight: '1px solid var(--border-color)' }}
      >
        <Sidebar />
      </aside>

      {/* Resize handle — 折叠时隐藏 */}
      {!collapsed && <ResizeHandle handleProps={handleProps} isDragging={isDragging} />}

      {/* Main content area */}
      <main className="flex-1 overflow-hidden flex flex-col">
        {children}
      </main>
    </div>
  )
}