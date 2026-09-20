import { useEffect, useState } from 'react'
import type { InterruptPayload } from '@/types/api'
import { cn } from '@/utils/cn'

interface Props {
  payload: InterruptPayload
}

const TIMEOUT_SECONDS = 30 * 60  // 30 minutes

export function InterruptCountdown({ payload }: Props) {
  const [remaining, setRemaining] = useState(
    Math.max(0, TIMEOUT_SECONDS - Math.floor(payload.age_seconds))
  )

  useEffect(() => {
    const start = Date.now()
    const initialRemaining = Math.max(0, TIMEOUT_SECONDS - Math.floor(payload.age_seconds))

    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - start) / 1000)
      const r = Math.max(0, initialRemaining - elapsed)
      setRemaining(r)
      if (r === 0) clearInterval(interval)
    }, 1000)

    return () => clearInterval(interval)
  }, [payload.age_seconds])

  const minutes = Math.floor(remaining / 60)
  const seconds = remaining % 60
  const isUrgent = remaining < 5 * 60  // < 5 minutes

  return (
    <div className={cn(
      'flex items-center gap-1.5 text-xs font-mono font-medium',
      isUrgent ? 'text-red-400 animate-pulse' : 'text-slate-400'
    )}>
      <span>⏱</span>
      <span>{String(minutes).padStart(2, '0')}:{String(seconds).padStart(2, '0')}</span>
      {isUrgent && <span className="font-sans font-normal text-red-400">即将超时!</span>}
    </div>
  )
}
