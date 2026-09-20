import { create } from 'zustand'

const STORAGE_KEY = 'qa-ui:sidebar-collapsed'

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function saveCollapsed(value: boolean) {
  try {
    localStorage.setItem(STORAGE_KEY, String(value))
  } catch { /* ignore */ }
}

interface SidebarState {
  collapsed: boolean
  toggle: () => void
}

export const useSidebarStore = create<SidebarState>((set) => ({
  collapsed: loadCollapsed(),
  toggle: () =>
    set((state) => {
      const next = !state.collapsed
      saveCollapsed(next)
      return { collapsed: next }
    }),
}))