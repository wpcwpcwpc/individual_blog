import type { ReactNode } from 'react'

// ── Shared form primitives for MCP server add/edit forms ────────
// 供 drawer 内 AddServerForm 与 page 内 McpServerFormModal 共用，
// 避免校验逻辑与样式分叉（共享表单字段模块）。

export interface McpServerFormState {
  name: string
  transport: 'stdio' | 'sse'
  command: string
  args: string
  env: string
  url: string
  enabled: boolean
}

export const EMPTY_MCP_SERVER_FORM: McpServerFormState = {
  name: '',
  transport: 'stdio',
  command: '',
  args: '',
  env: '',
  url: '',
  enabled: true,
}

/** 统一输入框样式（与原 AddServerForm 内联一致） */
export const inputClass =
  'w-full bg-[hsl(217.2_32.6%_12%)] border border-[hsl(217.2_32.6%_20%)] rounded px-2.5 py-1.5 text-xs text-white placeholder:text-slate-600 focus:outline-none focus:border-violet-500'

interface FieldProps {
  label: string
  error?: string
  children: ReactNode
}

/** 字段容器（label + child + 错误文案） */
export function Field({ label, error, children }: FieldProps) {
  return (
    <div>
      <label className="block text-[11px] text-slate-400 mb-1">{label}</label>
      {children}
      {error && <p className="text-[10px] text-red-400 mt-0.5">{error}</p>}
    </div>
  )
}

/**
 * 校验 MCP server 表单状态。
 * @returns 字段名 → 错误文案 映射；空对象表示通过。
 */
export function validateMcpServerForm(form: McpServerFormState): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!form.name.trim()) errors.name = '名称不能为空'
  if (form.transport === 'stdio' && !form.command.trim()) errors.command = '命令不能为空'
  if (form.transport === 'sse' && !form.url.trim()) errors.url = 'URL 不能为空'
  return errors
}
