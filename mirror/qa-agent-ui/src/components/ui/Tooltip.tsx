import type { ReactNode } from 'react'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { cn } from '@/utils/cn'

interface TooltipProps {
  tip?: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  sideOffset?: number
  maxWidth?: number
  collisionPadding?: number
  disabled?: boolean
  children: ReactNode
  className?: string
}

export function Tooltip({
  tip,
  side = 'top',
  sideOffset = 6,
  maxWidth = 360,
  collisionPadding = 8,
  disabled,
  children,
  className,
}: TooltipProps) {
  if (disabled || tip === undefined || tip === null || tip === '') {
    return <>{children}</>
  }

  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>
        {children}
      </TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={sideOffset}
          collisionPadding={collisionPadding}
          className={cn(
            'z-[9999] rounded border shadow pointer-events-none',
            'px-2.5 py-1 text-[11px] leading-[1.4] whitespace-normal break-words',
            className,
          )}
          style={{
            maxWidth,
            // 硬编码 hex 不走 CSS 变量 —— WebView 系统暗色模式会把
            // var(--tooltip-*) 解析为暗色导致黑底黑字（跨主题/跨页面不稳定）。
            backgroundColor: '#fff5d6',
            color: '#1a1a1a',
            borderColor: '#d4a017',
          }}
        >
          {tip}
          <TooltipPrimitive.Arrow className="fill-[#fff5d6]" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}
