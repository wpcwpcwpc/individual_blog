import { useState, useCallback, useRef, useEffect } from 'react'

interface UseResizableOptions {
  /** Default panel size in px */
  defaultWidth: number
  /** Minimum allowed size in px */
  minWidth: number
  /** Maximum allowed size in px */
  maxWidth: number
  /** localStorage key for persistence (omit to disable persistence) */
  storageKey?: string
  /**
   * Resize direction:
   * - 'right': handle on right edge, dragging right increases width (Sidebar)
   * - 'left': handle on left edge, dragging left increases width (InterruptPanel)
   * - 'up': handle on top edge, dragging up increases height (InputBar)
   * - 'down': handle on bottom edge, dragging down increases height
   */
  direction?: 'right' | 'left' | 'up' | 'down'
}

interface HandleProps {
  onPointerDown: (e: React.PointerEvent) => void
}

interface UseResizableReturn {
  /** Current panel size in px (width or height depending on direction) */
  width: number
  /** Whether the user is currently dragging */
  isDragging: boolean
  /** Props to spread on the ResizeHandle element */
  handleProps: HandleProps
}

function readStorage(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key)
    if (raw !== null) {
      const v = Number(raw)
      if (Number.isFinite(v) && v > 0) return v
    }
  } catch { /* ignore */ }
  return fallback
}

/** Whether the direction operates on the vertical (Y) axis */
function isVertical(dir: string): boolean {
  return dir === 'up' || dir === 'down'
}

/** Cursor style for the given direction */
function cursorForDirection(dir: string): string {
  return isVertical(dir) ? 'row-resize' : 'col-resize'
}

export function useResizable(options: UseResizableOptions): UseResizableReturn {
  const { defaultWidth, minWidth, maxWidth, storageKey, direction = 'right' } = options

  const [width, setWidth] = useState<number>(() =>
    storageKey ? readStorage(storageKey, defaultWidth) : defaultWidth
  )
  const [isDragging, setIsDragging] = useState(false)

  // Refs to avoid stale closures in pointer event handlers
  const startPosRef = useRef(0)
  const startWidthRef = useRef(0)
  const directionRef = useRef(direction)
  directionRef.current = direction

  // Clamp helper
  const clamp = useCallback(
    (v: number) => Math.min(maxWidth, Math.max(minWidth, v)),
    [minWidth, maxWidth],
  )

  // ── Pointer event handlers (attached to window during drag) ──

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      const dir = directionRef.current
      const vertical = isVertical(dir)
      const clientPos = vertical ? e.clientY : e.clientX
      const delta = clientPos - startPosRef.current

      let newSize: number
      switch (dir) {
        case 'right':
          newSize = startWidthRef.current + delta
          break
        case 'left':
          newSize = startWidthRef.current - delta
          break
        case 'up':
          newSize = startWidthRef.current - delta
          break
        case 'down':
          newSize = startWidthRef.current + delta
          break
        default:
          newSize = startWidthRef.current + delta
      }
      setWidth(clamp(newSize))
    },
    [clamp],
  )

  const onPointerUp = useCallback(
    () => {
      setIsDragging(false)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)

      // Remove global cursor override
      document.body.style.removeProperty('cursor')
      document.body.style.removeProperty('user-select')

      // Remove drag overlay
      const overlay = document.getElementById('resize-drag-overlay')
      overlay?.remove()
    },
    [onPointerMove],
  )

  // Persist to localStorage when drag ends (width has settled)
  const prevDragging = useRef(false)
  useEffect(() => {
    if (prevDragging.current && !isDragging && storageKey) {
      try {
        localStorage.setItem(storageKey, String(width))
      } catch { /* ignore */ }
    }
    prevDragging.current = isDragging
  }, [isDragging, width, storageKey])

  // ── handleProps.onPointerDown ──

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault()
      const vertical = isVertical(direction)
      startPosRef.current = vertical ? e.clientY : e.clientX
      startWidthRef.current = width
      setIsDragging(true)

      const cursor = cursorForDirection(direction)

      // Global cursor lock
      document.body.style.cursor = cursor
      document.body.style.userSelect = 'none'

      // Full-screen transparent overlay to prevent iframe/Monaco from capturing events
      if (!document.getElementById('resize-drag-overlay')) {
        const overlay = document.createElement('div')
        overlay.id = 'resize-drag-overlay'
        overlay.style.cssText =
          `position:fixed;inset:0;z-index:9999;cursor:${cursor};`
        document.body.appendChild(overlay)
      }

      window.addEventListener('pointermove', onPointerMove)
      window.addEventListener('pointerup', onPointerUp)
    },
    [width, direction, onPointerMove, onPointerUp],
  )

  return {
    width,
    isDragging,
    handleProps: { onPointerDown },
  }
}