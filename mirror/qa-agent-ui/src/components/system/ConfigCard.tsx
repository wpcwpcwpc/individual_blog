import type { ConfigResponse } from '@/types/api'

interface Props {
  config: ConfigResponse
}

const CONFIG_LABELS: Record<keyof ConfigResponse, string> = {
  llm_model: 'LLM 模型',
  llm_base_url: 'LLM Base URL',
  storage_backend: '存储后端',
  milvus_enabled: 'Milvus 启用',
  milvus_host: 'Milvus Host',
  default_max_turns: '默认最大轮次',
  default_permission_mode: '默认权限模式',
  project_skills_dir: 'Skills 目录',
  mcp_config_path: 'MCP 配置路径',
}

export function ConfigCard({ config }: Props) {
  return (
    <div className="rounded-xl border border-[hsl(217.2_32.6%_17.5%)] p-4">
      <h2 className="text-sm font-semibold text-slate-200 mb-3">运行配置</h2>
      <div className="divide-y divide-[hsl(217.2_32.6%_13%)]">
        {(Object.entries(config) as [keyof ConfigResponse, unknown][]).map(([key, value]) => (
          <div key={key} className="flex items-start py-2 gap-4">
            <span className="text-xs text-slate-500 w-36 flex-shrink-0">{CONFIG_LABELS[key] ?? key}</span>
            <span className="text-xs font-mono text-slate-300 break-all">{String(value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
