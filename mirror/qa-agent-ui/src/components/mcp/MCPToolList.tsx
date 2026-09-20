import { cn } from '@/utils/cn'
import type { MCPToolInfo } from '@/types/api'

interface Props {
  tools: MCPToolInfo[]
}

export function MCPToolList({ tools }: Props) {
  if (tools.length === 0) {
    return (
      <div className="px-3 py-2 text-xs text-slate-600">
        该服务没有注册任何工具
      </div>
    )
  }

  return (
    <div className="space-y-1 px-3 py-2">
      {tools.map(tool => (
        <div
          key={tool.name}
          className="flex items-start gap-2 py-1"
        >
          {/* Permission badge */}
          <span
            className={cn(
              'flex-shrink-0 mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold leading-none',
              tool.permission_level === 'L1'
                ? 'bg-emerald-500/15 text-emerald-400'
                : 'bg-amber-500/15 text-amber-400'
            )}
          >
            {tool.permission_level}
          </span>

          {/* Name + description */}
          <div className="min-w-0 flex-1">
            <span className="text-xs font-medium text-slate-300">{tool.name}</span>
            {tool.description && (
              <p className="text-[11px] text-slate-500 leading-tight mt-0.5 break-words">
                {tool.description}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
