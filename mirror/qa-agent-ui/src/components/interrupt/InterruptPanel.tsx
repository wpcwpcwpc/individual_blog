import { useSessionsStore } from '@/store/sessions'
import { L2PreviewPanel } from './L2PreviewPanel'
import { L3ResultPanel } from './L3ResultPanel'
import { ReviewGatePanel } from './ReviewGatePanel'
import { cn } from '@/utils/cn'

interface Props {
  sessionId: string
  /** Dynamic panel width in px, managed by useResizable in parent */
  panelWidth: number
  /** Whether the resize handle is being dragged (disables transition) */
  isDragging?: boolean
}

export function InterruptPanel({ sessionId, panelWidth, isDragging }: Props) {
  const interrupt = useSessionsStore(s => s.sessions[sessionId]?.interrupt ?? null)
  const isOpen = interrupt !== null

  return (
    <div
      className={cn(
        'flex-shrink-0 border-l border-[hsl(217.2_32.6%_17.5%)] overflow-hidden',
        // Keep open/close transition, but disable during drag to avoid lag
        !isDragging && 'transition-all duration-300 ease-in-out',
        !isOpen && 'opacity-0'
      )}
      style={{ width: isOpen ? panelWidth : 0 }}
    >
      {isOpen && interrupt && (
        <div className="h-full flex flex-col bg-[hsl(222.2_84%_5%)]" style={{ width: panelWidth }}>
          {interrupt.interrupt_type === 'preview_confirm' && (
            <L2PreviewPanel sessionId={sessionId} payload={interrupt} />
          )}
          {interrupt.interrupt_type === 'result_verify' && (
            <L3ResultPanel sessionId={sessionId} payload={interrupt} />
          )}
          {interrupt.interrupt_type === 'plan_confirm' && (
            <L2PreviewPanel sessionId={sessionId} payload={interrupt} />
          )}
          {interrupt.interrupt_type === 'review_requested' && (
            <ReviewGatePanel sessionId={sessionId} payload={interrupt} />
          )}
        </div>
      )}
    </div>
  )
}