import { CheckCircle, AlertTriangle, XCircle, MinusCircle } from 'lucide-react'
import type { HealthResponse } from '@/types/api'

interface Props {
  health: HealthResponse | null
}

export function HealthCard({ health }: Props) {
  if (!health) {
    return (
      <div className="rounded-xl border border-[hsl(217.2_32.6%_17.5%)] p-4">
        <h2 className="text-sm font-semibold text-slate-400 mb-3">系统健康</h2>
        <p className="text-xs text-slate-600">加载中...</p>
      </div>
    )
  }

  // 主动禁用 milvus (milvus === 'disabled') 属运维降级，非故障 → 视为健康
  const isOk = health.status === 'ok' || health.milvus === 'disabled'

  return (
    <div className="rounded-xl border border-[hsl(217.2_32.6%_17.5%)] p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">系统健康</h2>
        <div className={`flex items-center gap-1.5 text-sm font-medium ${isOk ? 'text-green-400' : 'text-yellow-400'}`}>
          {isOk
            ? <><CheckCircle className="w-4 h-4" /> 系统正常</>
            : <><AlertTriangle className="w-4 h-4" /> 降级运行</>
          }
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Milvus', value: health.milvus },
          { label: 'Storage', value: health.storage },
          { label: '版本', value: health.version },
        ].map(({ label, value }) => (
          <div key={label} className="bg-[hsl(217.2_32.6%_10%)] rounded-lg p-3">
            <p className="text-xs text-slate-500 mb-1">{label}</p>
            <div className="flex items-center gap-1.5">
              {(value === 'connected' || value === 'ok')
                ? <CheckCircle className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />
                : (value === 'disconnected' || value === 'error')
                  ? <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
                  : (value === 'disabled')
                    ? <MinusCircle className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
                    : null
              }
              <span className={`text-xs font-mono ${
                value === 'connected' || value === 'ok' ? 'text-green-300' :
                value === 'disconnected' || value === 'error' ? 'text-red-300' :
                value === 'disabled' ? 'text-slate-300' : 'text-slate-300'
              }`}>{value}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
