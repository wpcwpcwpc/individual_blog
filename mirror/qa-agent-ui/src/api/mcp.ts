import apiClient from './client'
import type {
  MCPServerInfo,
  AddMCPServerRequest,
  UpdateMCPServerRequest,
  MCPConfigResponse,
} from '@/types/api'

// ---------------------------------------------------------------------------
// Server CRUD
// ---------------------------------------------------------------------------

export const listMCPServers = () =>
  apiClient.get<{ servers: MCPServerInfo[] }>('/mcp/servers').then(r => r.data.servers)

export const getMCPServer = (name: string) =>
  apiClient.get<MCPServerInfo>(`/mcp/servers/${name}`).then(r => r.data)

export const addMCPServer = (data: AddMCPServerRequest) =>
  apiClient.post<MCPServerInfo>('/mcp/servers', data).then(r => r.data)

export const updateMCPServer = (name: string, data: UpdateMCPServerRequest) =>
  apiClient.put<MCPServerInfo>(`/mcp/servers/${name}`, data).then(r => r.data)

export const deleteMCPServer = (name: string) =>
  apiClient.delete(`/mcp/servers/${name}`).then(() => undefined)

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

export const enableMCPServer = (name: string) =>
  apiClient.post<MCPServerInfo>(`/mcp/servers/${name}/enable`).then(r => r.data)

export const disableMCPServer = (name: string) =>
  apiClient.post<MCPServerInfo>(`/mcp/servers/${name}/disable`).then(r => r.data)

export const reconnectMCPServer = (name: string) =>
  apiClient.post<MCPServerInfo>(`/mcp/servers/${name}/reconnect`).then(r => r.data)

// ---------------------------------------------------------------------------
// Global config
// ---------------------------------------------------------------------------

export const getMCPConfig = () =>
  apiClient.get<MCPConfigResponse>('/mcp/config').then(r => r.data)

export const updateMCPConfig = (config: MCPConfigResponse) =>
  apiClient.put<MCPConfigResponse>('/mcp/config', config).then(r => r.data)
