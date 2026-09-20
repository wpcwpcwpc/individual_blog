import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, Plus, Trash2 } from 'lucide-react'
import { useAppStore } from '@/store/app'
import { useSessionsStore } from '@/store/sessions'
import { createSession } from '@/api/sessions'
import type { AgentMode, CreateSessionRequest } from '@/types/api'
import { cn } from '@/utils/cn'

interface Props {
  onClose: () => void
}

export function CreateSessionModal({ onClose }: Props) {
  const agents = useAppStore(s => s.agents)
  const addPendingSession = useSessionsStore(s => s.addPendingSession)
  const resolvePendingSession = useSessionsStore(s => s.resolvePendingSession)
  const rejectPendingSession = useSessionsStore(s => s.rejectPendingSession)
  const setActiveSession = useSessionsStore(s => s.setActiveSession)
  const navigate = useNavigate()

  const [agentName, setAgentName] = useState(agents[0]?.name ?? 'QAAutomationAgent')
  const [mode, setMode] = useState<AgentMode>('normal')
  const [workerAgents, setWorkerAgents] = useState<string[]>([])

  // harden-create-session-entry D2：提交即返回——占位卡入列表 + 关弹窗 + 跳列表，
  // createSession（后端内联构建 agent，最重 RTT）在后台执行；成功替换占位并跳转，
  // 失败把占位卡转错误态。弹窗内不再有 loading/错误态（已卸载）。
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    const req: CreateSessionRequest = {
      agent_name: agentName,
      mode,
    }
    if (mode === 'coordinator' && workerAgents.length > 0) {
      req.worker_agent_names = workerAgents
    }

    const tempId = addPendingSession(agentName)
    navigate('/sessions')
    onClose()

    createSession(req).then(resp => {
      resolvePendingSession(tempId, {
        session_id: resp.session_id,
        agent_name: resp.agent_name,
        mode: resp.mode,
        status: resp.status,
        created_at: Date.now(),
      })
      setActiveSession(resp.session_id)
      navigate(`/sessions/${resp.session_id}`, { replace: true })
    }).catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : '创建会话失败，请检查后端连接'
      console.warn('[CreateSessionModal] createSession failed: %s', msg)
      rejectPendingSession(tempId, msg)
    })
  }

  const addWorkerAgent = () => setWorkerAgents(prev => [...prev, agents[0]?.name ?? ''])
  const removeWorkerAgent = (i: number) => setWorkerAgents(prev => prev.filter((_, idx) => idx !== i))
  const updateWorkerAgent = (i: number, v: string) =>
    setWorkerAgents(prev => prev.map((a, idx) => idx === i ? v : a))

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-[hsl(222.2_84%_7%)] border border-[hsl(217.2_32.6%_17.5%)] rounded-xl w-full max-w-lg shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[hsl(217.2_32.6%_17.5%)]">
          <h2 className="text-base font-semibold">新建 Agent 会话</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          {/* Agent selector */}
          <div>
            <label className="block text-sm text-slate-400 mb-1">Agent 类型</label>
            <select
              value={agentName}
              onChange={e => setAgentName(e.target.value)}
              className="w-full bg-[hsl(217.2_32.6%_12%)] border border-[hsl(217.2_32.6%_20%)] rounded-md px-3 py-2 text-sm text-white focus:outline-none focus:border-violet-500"
            >
              {agents.map(a => (
                <option key={a.name} value={a.name}>{a.name}</option>
              ))}
              {agents.length === 0 && <option value="QAAutomationAgent">QAAutomationAgent</option>}
            </select>
            {agents.find(a => a.name === agentName)?.description && (
              <p className="text-xs text-slate-500 mt-1">
                {agents.find(a => a.name === agentName)?.description}
              </p>
            )}
          </div>

          {/* Mode selector */}
          <div>
            <label className="block text-sm text-slate-400 mb-1">运行模式</label>
            <div className="flex gap-3">
              {(['normal', 'coordinator'] as AgentMode[]).map(m => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={cn(
                    'flex-1 py-2 rounded-md text-sm font-medium border transition-colors',
                    mode === m
                      ? 'bg-violet-600 border-violet-500 text-white'
                      : 'bg-transparent border-[hsl(217.2_32.6%_20%)] text-slate-400 hover:border-violet-500/50'
                  )}
                >
                  {m === 'normal' ? 'Normal' : 'Coordinator'}
                </button>
              ))}
            </div>
            {mode === 'coordinator' && (
              <p className="text-xs text-amber-400/80 mt-2">
                ⚠ Coordinator 模式尚未开发完整，暂不可用
              </p>
            )}
          </div>

          {/* Worker agents (coordinator mode only) */}
          {mode === 'coordinator' && (
            <div>
              <label className="block text-sm text-slate-400 mb-1">Worker Agents</label>
              <div className="space-y-2">
                {workerAgents.map((wa, i) => (
                  <div key={i} className="flex gap-2">
                    <select
                      value={wa}
                      onChange={e => updateWorkerAgent(i, e.target.value)}
                      className="flex-1 bg-[hsl(217.2_32.6%_12%)] border border-[hsl(217.2_32.6%_20%)] rounded-md px-3 py-1.5 text-sm text-white focus:outline-none focus:border-violet-500"
                    >
                      {agents.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
                    </select>
                    <button type="button" onClick={() => removeWorkerAgent(i)}
                      className="p-1.5 text-slate-500 hover:text-red-400 transition-colors">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
                <button type="button" onClick={addWorkerAgent}
                  className="flex items-center gap-1.5 text-xs text-violet-400 hover:text-violet-300 transition-colors">
                  <Plus className="w-3.5 h-3.5" /> 添加 Worker
                </button>
              </div>
            </div>
          )}

          {/* Game context — hidden: not needed yet */}
          {/* <div className="grid grid-cols-2 gap-3"> ... </div> */}

          {/* Advanced config — hidden: not needed yet */}
          {/* <div> ... </div> */}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 py-2 rounded-md border border-[hsl(217.2_32.6%_20%)] text-sm text-slate-400 hover:text-white transition-colors"
            >
              取消
            </button>
            <button
              type="submit"
              className="flex-1 py-2 rounded-md bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors"
            >
              创建会话
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
