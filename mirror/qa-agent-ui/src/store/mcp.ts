import { create } from 'zustand'
import type { MCPServerInfo, AddMCPServerRequest, UpdateMCPServerRequest } from '@/types/api'
import {
  listMCPServers,
  enableMCPServer,
  disableMCPServer,
  reconnectMCPServer,
  addMCPServer,
  updateMCPServer,
  deleteMCPServer,
} from '@/api/mcp'

interface MCPState {
  servers: MCPServerInfo[]
  isOpen: boolean
  loading: boolean
  operatingServer: string | null
  showAddForm: boolean
  showConfigEditor: boolean
  /** 当前正在编辑的 server（非 null 时 McpServerFormModal 显示 edit 模式）。See add-mcp-management-page spec — "store 新增 editingServer 状态" */
  editingServer: MCPServerInfo | null

  openPanel: () => Promise<void>
  closePanel: () => void
  togglePanel: () => void
  fetchServers: () => Promise<void>
  enableServer: (name: string) => Promise<void>
  disableServer: (name: string) => Promise<void>
  reconnectServer: (name: string) => Promise<void>
  addServer: (req: AddMCPServerRequest) => Promise<void>
  updateServer: (name: string, req: UpdateMCPServerRequest) => Promise<void>
  deleteServer: (name: string) => Promise<void>
  setShowAddForm: (v: boolean) => void
  setShowConfigEditor: (v: boolean) => void
  setEditingServer: (server: MCPServerInfo) => void
  clearEditingServer: () => void
}

/** Replace a single server entry in the array by name. */
const replaceServer = (servers: MCPServerInfo[], updated: MCPServerInfo) =>
  servers.map(s => (s.name === updated.name ? updated : s))

export const useMCPStore = create<MCPState>((set, get) => ({
  servers: [],
  isOpen: false,
  loading: false,
  operatingServer: null,
  showAddForm: false,
  showConfigEditor: false,
  editingServer: null,

  // ── Panel controls ──────────────────────────────────────────────

  openPanel: async () => {
    await get().fetchServers()
    set({ isOpen: true })
  },

  closePanel: () => set({ isOpen: false, showAddForm: false, showConfigEditor: false }),

  togglePanel: () => {
    if (get().isOpen) {
      get().closePanel()
    } else {
      get().openPanel()
    }
  },

  // ── Fetch ───────────────────────────────────────────────────────

  fetchServers: async () => {
    set({ loading: true })
    try {
      const servers = await listMCPServers()
      set({ servers, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  // ── Single-server operations (in-place update) ──────────────────

  enableServer: async (name) => {
    set({ operatingServer: name })
    try {
      const updated = await enableMCPServer(name)
      set(s => ({ servers: replaceServer(s.servers, updated), operatingServer: null }))
    } catch {
      set({ operatingServer: null })
    }
  },

  disableServer: async (name) => {
    set({ operatingServer: name })
    try {
      const updated = await disableMCPServer(name)
      set(s => ({ servers: replaceServer(s.servers, updated), operatingServer: null }))
    } catch {
      set({ operatingServer: null })
    }
  },

  reconnectServer: async (name) => {
    set({ operatingServer: name })
    try {
      const updated = await reconnectMCPServer(name)
      set(s => ({ servers: replaceServer(s.servers, updated), operatingServer: null }))
    } catch {
      set({ operatingServer: null })
    }
  },

  updateServer: async (name, req) => {
    set({ operatingServer: name })
    try {
      const updated = await updateMCPServer(name, req)
      set(s => ({ servers: replaceServer(s.servers, updated), operatingServer: null }))
    } catch {
      set({ operatingServer: null })
    }
  },

  // ── Add / Delete (refetch list) ─────────────────────────────────

  addServer: async (req) => {
    await addMCPServer(req)
    await get().fetchServers()
  },

  deleteServer: async (name) => {
    set({ operatingServer: name })
    try {
      await deleteMCPServer(name)
      set({ operatingServer: null })
      await get().fetchServers()
    } catch {
      set({ operatingServer: null })
    }
  },

  // ── UI toggles ──────────────────────────────────────────────────

  setShowAddForm: (v) => set({ showAddForm: v }),
  setShowConfigEditor: (v) => set({ showConfigEditor: v }),

  // ── Edit modal (page-level) ──────────────────────────────────────
  // editingServer 非 null 时 McpServerFormModal 渲染 edit 模式。

  setEditingServer: (server) => set({ editingServer: server }),
  clearEditingServer: () => set({ editingServer: null }),
}))

/** Derived: count of connected servers. */
export const useConnectedCount = () =>
  useMCPStore(s => s.servers.filter(sv => sv.status === 'connected').length)
