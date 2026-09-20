import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import * as Collapsible from '@radix-ui/react-collapsible'
import { useMCPStore } from '@/store/mcp'
import { cn } from '@/utils/cn'
import type { AddMCPServerRequest } from '@/types/api'
import { inputClass, Field, validateMcpServerForm } from './form-fields'

export function AddServerForm() {
  const showAddForm = useMCPStore(s => s.showAddForm)
  const setShowAddForm = useMCPStore(s => s.setShowAddForm)
  const addServer = useMCPStore(s => s.addServer)

  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'stdio' | 'sse'>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [env, setEnv] = useState('')
  const [url, setUrl] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({})

  const resetForm = () => {
    setName('')
    setTransport('stdio')
    setCommand('')
    setArgs('')
    setEnv('')
    setUrl('')
    setEnabled(true)
    setError(null)
    setValidationErrors({})
  }

  const handleCancel = () => {
    resetForm()
    setShowAddForm(false)
  }

  const validate = (): boolean => {
    const errors = validateMcpServerForm({ name, transport, command, args, env, url, enabled })
    setValidationErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return

    setSubmitting(true)
    setError(null)
    try {
      const req: AddMCPServerRequest = {
        name: name.trim(),
        transport,
        enabled,
      }
      if (transport === 'stdio') {
        req.command = command.trim()
        if (args.trim()) req.args = args.trim().split(/\s+/)
        if (env.trim()) {
          req.env = Object.fromEntries(
            env.trim().split('\n')
              .map(line => line.split('='))
              .filter(parts => parts.length >= 2)
              .map(([k, ...v]) => [k.trim(), v.join('=').trim()])
          )
        }
      } else {
        req.url = url.trim()
      }

      await addServer(req)
      resetForm()
      setShowAddForm(false)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '添加服务失败'
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Collapsible.Root open={showAddForm} onOpenChange={setShowAddForm}>
      <Collapsible.Trigger asChild>
        <button
          type="button"
          className="w-full flex items-center justify-center gap-1.5 py-2 text-xs text-violet-400 hover:text-violet-300 transition-colors"
        >
          {showAddForm ? (
            <>
              <X className="w-3.5 h-3.5" />
              收起
            </>
          ) : (
            <>
              <Plus className="w-3.5 h-3.5" />
              添加 MCP 服务
            </>
          )}
        </button>
      </Collapsible.Trigger>

      <Collapsible.Content>
        <div className="px-3 pb-3 space-y-3">
          {/* Name */}
          <Field label="服务名称" error={validationErrors.name}>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="如: game_debug_mcp"
              className={inputClass}
            />
          </Field>

          {/* Transport type */}
          <div>
            <label className="block text-[11px] text-slate-400 mb-1">传输类型</label>
            <div className="flex gap-2">
              {(['stdio', 'sse'] as const).map(t => (
                <button
                  key={t}
                  type="button"
                  onClick={() => { setTransport(t); setValidationErrors({}) }}
                  className={cn(
                    'flex-1 py-1.5 rounded text-xs font-medium border transition-colors',
                    transport === t
                      ? 'bg-violet-600 border-violet-500 text-white'
                      : 'bg-transparent border-[hsl(217.2_32.6%_20%)] text-slate-400 hover:border-violet-500/50'
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* stdio fields */}
          {transport === 'stdio' && (
            <>
              <Field label="命令 (command)" error={validationErrors.command}>
                <input
                  type="text"
                  value={command}
                  onChange={e => setCommand(e.target.value)}
                  placeholder="如: python"
                  className={inputClass}
                />
              </Field>
              <Field label="参数 (args，空格分隔)">
                <input
                  type="text"
                  value={args}
                  onChange={e => setArgs(e.target.value)}
                  placeholder="如: -m mcp_server --port 8080"
                  className={inputClass}
                />
              </Field>
              <Field label="环境变量 (每行 KEY=VALUE)">
                <textarea
                  value={env}
                  onChange={e => setEnv(e.target.value)}
                  placeholder={"PATH=/usr/bin\nDEBUG=1"}
                  rows={2}
                  className={cn(inputClass, 'resize-none')}
                />
              </Field>
            </>
          )}

          {/* sse fields */}
          {transport === 'sse' && (
            <Field label="服务器 URL" error={validationErrors.url}>
              <input
                type="text"
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder="如: http://localhost:3001/sse"
                className={inputClass}
              />
            </Field>
          )}

          {/* Enable checkbox */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              onChange={e => setEnabled(e.target.checked)}
              className="rounded border-slate-600 bg-transparent"
            />
            <span className="text-xs text-slate-400">立即启用</span>
          </label>

          {/* Error */}
          {error && (
            <div className="bg-red-500/10 border border-red-500/30 rounded px-2.5 py-1.5 text-[11px] text-red-400">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleCancel}
              className="flex-1 py-1.5 rounded text-xs text-slate-400 border border-[hsl(217.2_32.6%_20%)] hover:text-white transition-colors"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting}
              className="flex-1 py-1.5 rounded text-xs font-medium bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white transition-colors"
            >
              {submitting ? '添加中...' : '添加'}
            </button>
          </div>
        </div>
      </Collapsible.Content>
    </Collapsible.Root>
  )
}

// ── Helpers ─────────────────────────────────────────────────────
// inputClass / Field / validateMcpServerForm 已抽到 ./form-fields，此处不再内联。
