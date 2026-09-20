import { cn } from '@/utils/cn'

interface ResizeHandleProps {
  /** Props from useResizable – spread onto the handle element */
  handleProps: {
    onPointerDown: (e: React.PointerEvent) => void
  }
  /** Whether the user is currently dragging this handle */
  isDragging: boolean
  /** Orientation of the handle: 'vertical' for width resize, 'horizontal' for height resize */
  orientation?: 'vertical' | 'horizontal'
  /** Additional className */
  className?: string
}

/**
 * A thin resize handle rendered between two panels.
 *
 * Supports both vertical (left/right panels) and horizontal (top/bottom panels) orientations.
 *
 * Three visual states:
 * - **idle**: subtle border-colored line
 * - **hover**: highlighted with resize cursor
 * - **dragging**: stays highlighted; global cursor is managed by useResizable
 */
export function ResizeHandle({ handleProps, isDragging, orientation = 'vertical', className }: ResizeHandleProps) {
  const isHorizontal = orientation === 'horizontal'

  return (
    <div
      {...handleProps}
      className={cn(
        // Common
        'flex-shrink-0 relative select-none',
        // Orientation-specific layout & cursor
        isHorizontal
          ? 'w-full h-[5px] cursor-row-resize'
          : 'w-[5px] cursor-col-resize',
        // Idle state: subtle center line (pseudo-element)
        'before:absolute before:transition-colors before:duration-150',
        isHorizontal
          ? 'before:inset-x-0 before:top-1/2 before:-translate-y-1/2 before:h-px before:bg-[hsl(217.2_32.6%_17.5%)]'
          : 'before:inset-y-0 before:left-1/2 before:-translate-x-1/2 before:w-px before:bg-[hsl(217.2_32.6%_17.5%)]',
        // Hover state
        isHorizontal
          ? 'hover:before:h-[3px] hover:before:bg-violet-500/40 hover:before:rounded-full'
          : 'hover:before:w-[3px] hover:before:bg-violet-500/40 hover:before:rounded-full',
        // Dragging state
        isDragging && (isHorizontal
          ? 'before:h-[3px] before:bg-violet-500/60 before:rounded-full'
          : 'before:w-[3px] before:bg-violet-500/60 before:rounded-full'),
        className,
      )}
      role="separator"
      aria-orientation={orientation}
    />
  )
}