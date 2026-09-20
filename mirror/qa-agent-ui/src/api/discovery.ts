import apiClient from './client'
import type { AgentInfo, SkillInfo, HealthResponse, ConfigResponse, ModelSlotsResponse, AvailableModelsResponse } from '@/types/api'

export const listAgents = () =>
  apiClient.get<AgentInfo[]>('/agents').then(r => r.data)

export const listSkills = () =>
  apiClient.get<SkillInfo[]>('/skills').then(r => r.data)

export const getHealth = () =>
  apiClient.get<HealthResponse>('/health').then(r => r.data)

export const getConfig = () =>
  apiClient.get<ConfigResponse>('/config').then(r => r.data)

export const getModelSlots = () =>
  apiClient.get<ModelSlotsResponse>('/config/model-slots').then(r => r.data)

export const getAvailableModels = () =>
  apiClient.get<AvailableModelsResponse>('/config/available-models').then(r => r.data)
