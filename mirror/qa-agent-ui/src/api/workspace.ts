import apiClient from './client'
import type { WorkspaceInfo, TreeEntry } from '@/types/api'

/** PUT /sessions/{sid}/workspace — set or update workspace root */
export const setWorkspace = (sessionId: string, rootPath: string) =>
  apiClient
    .put<WorkspaceInfo>(`/sessions/${sessionId}/workspace`, { root_path: rootPath })
    .then(r => r.data)

/** GET /sessions/{sid}/workspace — get workspace info; returns null if not set */
export const getWorkspace = async (sessionId: string): Promise<WorkspaceInfo | null> => {
  try {
    const res = await apiClient.get<WorkspaceInfo>(`/sessions/${sessionId}/workspace`)
    return res.data
  } catch (err: any) {
    if (err?.response?.status === 404) return null
    throw err
  }
}

/** DELETE /sessions/{sid}/workspace — remove workspace binding (idempotent) */
export const deleteWorkspace = (sessionId: string) =>
  apiClient.delete(`/sessions/${sessionId}/workspace`).then(() => undefined)

/** GET /sessions/{sid}/workspace/tree?path=... — lazy-load directory entries */
export const getWorkspaceTree = (sessionId: string, path?: string) =>
  apiClient
    .get<{ entries: TreeEntry[] }>(`/sessions/${sessionId}/workspace/tree`, {
      params: path ? { path } : undefined,
    })
    .then(r => r.data.entries)

/** POST /system/browse-directory — open native folder picker dialog */
export const browseDirectory = () =>
  apiClient
    .post<{ selected_path: string | null; error: string | null }>('/system/browse-directory')
    .then(r => r.data)