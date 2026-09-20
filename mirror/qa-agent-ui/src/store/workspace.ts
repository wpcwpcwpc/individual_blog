import { create } from 'zustand'
import type { TreeEntry } from '@/types/api'

// ---------------------------------------------------------------------------
// Per-session workspace data
// ---------------------------------------------------------------------------

interface WorkspaceData {
  rootPath: string
  /** Flat map: relative dir path → direct children. '' = root level */
  childrenMap: Record<string, TreeEntry[]>
  expandedPaths: Set<string>
  loadingPaths: Set<string>
  selectedPaths: string[]
}

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

interface WorkspaceStore {
  workspaces: Record<string, WorkspaceData>

  // workspace lifecycle
  setWorkspace(sessionId: string, rootPath: string, tree: TreeEntry[]): void
  clearWorkspace(sessionId: string): void

  // tree data
  setChildren(sessionId: string, path: string, entries: TreeEntry[]): void

  // expand / collapse
  toggleExpand(sessionId: string, path: string): void

  // loading
  setLoading(sessionId: string, path: string, loading: boolean): void

  // selection
  toggleSelect(sessionId: string, path: string): void
  removeSelect(sessionId: string, path: string): void
  clearSelections(sessionId: string): void
}

// ---------------------------------------------------------------------------
// Helper: get or create workspace data
// ---------------------------------------------------------------------------

function getWs(state: WorkspaceStore, sid: string): WorkspaceData | undefined {
  return state.workspaces[sid]
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useWorkspaceStore = create<WorkspaceStore>()((set) => ({
  workspaces: {},

  setWorkspace(sessionId, rootPath, tree) {
    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [sessionId]: {
          rootPath,
          childrenMap: { '': tree },
          expandedPaths: new Set<string>(),
          loadingPaths: new Set<string>(),
          selectedPaths: [],
        },
      },
    }))
  },

  clearWorkspace(sessionId) {
    set((state) => {
      const { [sessionId]: _, ...rest } = state.workspaces
      return { workspaces: rest }
    })
  },

  setChildren(sessionId, path, entries) {
    set((state) => {
      const ws = getWs(state, sessionId)
      if (!ws) return state
      return {
        workspaces: {
          ...state.workspaces,
          [sessionId]: {
            ...ws,
            childrenMap: { ...ws.childrenMap, [path]: entries },
          },
        },
      }
    })
  },

  toggleExpand(sessionId, path) {
    set((state) => {
      const ws = getWs(state, sessionId)
      if (!ws) return state
      const next = new Set(ws.expandedPaths)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return {
        workspaces: {
          ...state.workspaces,
          [sessionId]: { ...ws, expandedPaths: next },
        },
      }
    })
  },

  setLoading(sessionId, path, loading) {
    set((state) => {
      const ws = getWs(state, sessionId)
      if (!ws) return state
      const next = new Set(ws.loadingPaths)
      if (loading) next.add(path)
      else next.delete(path)
      return {
        workspaces: {
          ...state.workspaces,
          [sessionId]: { ...ws, loadingPaths: next },
        },
      }
    })
  },

  toggleSelect(sessionId, path) {
    set((state) => {
      const ws = getWs(state, sessionId)
      if (!ws) return state
      const idx = ws.selectedPaths.indexOf(path)
      const next = idx >= 0
        ? ws.selectedPaths.filter((_, i) => i !== idx)
        : [...ws.selectedPaths, path]
      return {
        workspaces: {
          ...state.workspaces,
          [sessionId]: { ...ws, selectedPaths: next },
        },
      }
    })
  },

  removeSelect(sessionId, path) {
    set((state) => {
      const ws = getWs(state, sessionId)
      if (!ws) return state
      return {
        workspaces: {
          ...state.workspaces,
          [sessionId]: {
            ...ws,
            selectedPaths: ws.selectedPaths.filter((p) => p !== path),
          },
        },
      }
    })
  },

  clearSelections(sessionId) {
    set((state) => {
      const ws = getWs(state, sessionId)
      if (!ws) return state
      return {
        workspaces: {
          ...state.workspaces,
          [sessionId]: { ...ws, selectedPaths: [] },
        },
      }
    })
  },
}))