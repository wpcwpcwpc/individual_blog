import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { useMCPStore } from '@/store/mcp'
import { cn } from '@/utils/cn'
import type { AddMCPServerRequest, MCPServerInfo, UpdateMCPServerRequest } from '@/types/api'
import {
  EMPTY_MCP_SERVER_FORM,
  Field,
  inputClass,
  validateMcpServerForm,
  type McpServerFormState,
} from './form-fields'

interface Props {
  mode: 'add' | 'edit'
  open: boolean
  onClose: () => void
  initialServer?: MCPServerInfo
}

/** 从 MCPServerInfo.config 反解出表单状态（edit 模式预填）。 */
function formStateFromServer(server: MCPServerInfo): McpServerFormState {
  const cfg = (server.config ?? {}) as Record<string, unknown>
  const args = Array.isArray(cfg.args) ? (cfg.args as unknown[]).map(String).join(' ') : ''
  const env =
    cfg.env && typeof cfg.env === 'object'
      ? Object.entries(cfg.env as Record<string, string>)
          .map(([k, v]) => `${k}=${v}`)
          .join('\n')
      : ''
  return {
    name: server.name,
    transport: server.transport,
    command: typeof cfg.command === 'string' ? cfg.command : '',
    args,
    env,
    url: typeof cfg.url === 'string' ? cfg.url : '',
    enabled: server.enabled,
  }
}

/** 把 env textarea 文本解析回 Record<string,string>。 */
function parseEnvText(text: string): Record<string, string> {
  return Object.fromEntries(
    text
      .split('\n')
      .map(line => line.split('='))
      .filter(parts => parts.length >= 2)
      .map(([k, ...v]) => [k.trim(), v.join('=').trim()]),
  )
}

export function McpServerFormModal({ mode, open, onClose, initialServer }: Props) {
  const addServer = useMCPStore(s => s.addServer)
  const updateServer = useMCPStore(s => s.updateServer)
  const clearEditingServer = useMCPStore(s => s.clearEditingServer)

  const [form, setForm] = useState<McpServerFormState>(EMPTY_MCP_SERVER_FORM)
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // 开/关或 initialServer 变化时重置表单
  useEffect(() => {
    if (!open) return
    setForm(
      mode === 'edit' && initialServer
        ? formStateFromServer(initialServer)
        : EMPTY_MCP_SERVER_FORM,
    )
    setValidationErrors({})
    setSubmitError(null)
  }, [open, mode, initialServer])

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, submitting])

  if (!open) return null

  const isEdit = mode === 'edit'
  const transportDisabled = isEdit // 后端 PUT 不接 transport 改动

  const handleClose = () => {
    if (submitting) return
    onClose()
    if (isEdit) clearEditingServer()
  }

  const handleSubmit = async () => {
    const errors = validateMcpServerForm(form)
    setValidationErrors(errors)
    if (Object.keys(errors).length > 0) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      if (isEdit && initialServer) {
        // PUT /mcp/servers/{name} 只接 command/args/env/url；空值省略（跳过更新）。
        // 已知取舍：v1 无法用 modal 清空字段（清空请走 JSON 编辑器）。
        const req: UpdateMCPServerRequest = {}
        if (form.transport === 'stdio') {
          if (form.command.trim()) req.command = form.command.trim()
          if (form.args.trim()) req.args = form.args.trim().split(/\s+/)
          if (form.env.trim()) req.env = parseEnvText(form.env.trim())
        } else {
          if (form.url.trim()) req.url = form.url.trim()
        }
        await updateServer(initialServer.name, req)
      } else {
        const req: AddMCPServerRequest = {
          name: form.name.trim(),
          transport: form.transport,
          enabled: form.enabled,
        }
        if (form.transport === 'stdio') {
          if (form.command.trim()) req.command = form.command.trim()
          if (form.args.trim()) req.args = form.args.trim().split(/\s+/)
          if (form.env.trim()) req.env = parseEnvText(form.env.trim())
        } else {
          if (form.url.trim()) req.url = form.url.trim()
        }
        await addServer(req)
      }
      handleClose()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : isEdit ? '保存失败' : '添加失败'
      setSubmitError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={handleClose}
    >
      <div
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto p-5 rounded-lg bg-[hsl(222.2_84%_6.5%)] border border-[hsl(217.2_32.6%_17.5%)]"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-slate-100">
            {isEdit ? '编辑 MCP 服务' : '新建 MCP 服务'}
          </h2>
          <button
            type="button"
            onClick={handleClose}
            className="p-1 rounded text-slate-500 hover:text-slate-300 hover:bg-[hsl(217.2_32.6%_14%)] transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-col gap-3">
          <Field label="服务名称" error={validationErrors.name}>
            <input
              type="text"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="如: game_debug_mcp"
              disabled={isEdit}
              className={cn(inputClass, isEdit && 'opacity-60 cursor-not-allowed')}
            />
          </Field>

          <div>
            <label className="block text-[11px] text-slate-400 mb-1">传输类型</label>
            <div className="flex gap-2">
              {(['stdio', 'sse'] as const).map(t => (
                <button
                  key={t}
                  type="button"
                  onClick={() => !transportDisabled && setForm(f => ({ ...f, transport: t }))}
                  disabled={transportDisabled}
                  className={cn(
                    'flex-1 py-1.5 rounded text-xs font-medium border transition-colors',
                    transportDisabled && 'opacity-60 cursor-not-allowed',
                    form.transport === t
                      ? 'bg-violet-600 border-violet-500 text-white'
                      : 'bg-transparent border-[hsl(217.2_32.6%_20%)] text-slate-400 hover:border-violet-500/50',
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
            {transportDisabled && (
              <p className="text-[10px] text-slate-500 mt-1">传输类型创建后不可更改</p>
            )}
          </div>

          {form.transport === 'stdio' && (
            <>
              <Field label="命令 (command)" error={validationErrors.command}>
                <input
                  type="text"
                  value={form.command}
                  onChange={e => setForm(f => ({ ...f, command: e.target.value }))}
                  placeholder="如: python"
                  className={inputClass}
                />
              </Field>
              <Field label="参数 (args，空格分隔)">
                <input
                  type="text"
                  value={form.args}
                  onChange={e => setForm(f => ({ ...f, args: e.target.value }))}
                  placeholder="如: -m mcp_server --port 8080"
                  className={inputClass}
                />
              </Field>
              <Field label="环境变量 (每行 KEY=VALUE)">
                <textarea
                  value={form.env}
                  onChange={e => setForm(f => ({ ...f, env: e.target.value }))}
                  placeholder={"PATH=/usr/bin\nDEBUG=1"}
                  rows={2}
                  className={cn(inputClass, 'resize-none')}
                />
              </Field>
            </>
          )}

          {form.transport === 'sse' && (
            <Field label="服务器 URL" error={validationErrors.url}>
              <input
                type="text"
                value={form.url}
                onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
                placeholder="如: http://localhost:3001/sse"
                className={inputClass}
              />
            </Field>
          )}

          {/* enabled 仅 add 模式显；edit 模式由卡片上的启用/禁用按钮控制（后端 PUT 不接 enabled） */}
          {!isEdit && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
                className="rounded border-slate-600 bg-transparent"
              />
              <span className="text-xs text-slate-400">立即启用</span>
            </label>
          )}
          {isEdit && (
            <p className="text-[10px] text-slate-500">
              启用状态请用卡片上的「启用 / 禁用」按钮切换
            </p>
          )}

          {submitError && (
            <div className="bg-red-500/10 border border-red-500/30 rounded px-2.5 py-1.5 text-[11px] text-red-400">
              {submitError}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 mt-4">
          <button
            type="button"
            onClick={handleClose}
            disabled={submitting}
            className="flex-1 py-1.5 rounded text-xs text-slate-400 border border-[hsl(217.2_32.6%_20%)] hover:text-white disabled:opacity-50 transition-colors"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="flex-1 py-1.5 rounded text-xs font-medium bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white transition-colors"
          >
            {submitting ? (isEdit ? '保存中...' : '添加中...') : isEdit ? '保存' : '添加'}
          </button>
        </div>
      </div>
    </div>
  )
}
