import { create } from 'zustand'
import type { AgentInfo, SkillInfo, HealthResponse, ModelInfo } from '@/types/api'
import { listAgents, listSkills, getHealth, getAvailableModels } from '@/api/discovery'

interface AppState {
  agents: AgentInfo[]
  skills: SkillInfo[]
  health: HealthResponse | null
  initialized: boolean
  availableModels: ModelInfo[]
  defaultModel: string

  onDemandSkills: () => SkillInfo[]
  initialize: () => Promise<void>
  refreshHealth: () => Promise<void>
  fetchAvailableModels: () => Promise<void>
}

export const useAppStore = create<AppState>((set, get) => ({
  agents: [],
  skills: [],
  health: null,
  initialized: false,
  availableModels: [],
  defaultModel: '',

  onDemandSkills: () => get().skills.filter(s => s.load_mode === 'on-demand'),

  initialize: async () => {
    try {
      const [agents, skills, health] = await Promise.allSettled([
        listAgents(),
        listSkills(),
        getHealth(),
      ])
      set({
        agents: agents.status === 'fulfilled' ? agents.value : [],
        skills: skills.status === 'fulfilled' ? skills.value : [],
        health: health.status === 'fulfilled' ? health.value : null,
        initialized: true,
      })
    } catch {
      set({ initialized: true })
    }
  },

  refreshHealth: async () => {
    try {
      const health = await getHealth()
      set({ health })
    } catch {
      // ignore
    }
  },

  fetchAvailableModels: async () => {
    try {
      const res = await getAvailableModels()
      set({ availableModels: res.models, defaultModel: res.default })
    } catch {
      // Graceful degradation: keep empty list, selector won't render
      set({ availableModels: [], defaultModel: '' })
    }
  },
}))
