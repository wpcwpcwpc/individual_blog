import { create } from 'zustand'

// ── Session history UI state (折叠区 + 搜索词) ────────────────────
// hidden 状态是会话数据,归后端;折叠/搜索是 UI 临时态,归前端 localStorage。

const STORAGE_KEY = 'qa-agent:session-history-ui'

interface PersistedState {
  hiddenSectionCollapsed: boolean
  searchQuery: string
}

function loadPersisted(): PersistedState {
  const fallback: PersistedState = { hiddenSectionCollapsed: true, searchQuery: '' }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<PersistedState>
    return {
      hiddenSectionCollapsed: parsed.hiddenSectionCollapsed ?? fallback.hiddenSectionCollapsed,
      searchQuery: parsed.searchQuery ?? fallback.searchQuery,
    }
  } catch {
    return fallback
  }
}

function savePersisted(state: PersistedState) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // ignore: localStorage unavailable (private mode / quota)
  }
}

interface SessionHistoryUiState extends PersistedState {
  toggleHiddenSection: () => void
  setSearchQuery: (q: string) => void
  clearSearch: () => void
}

export const useSessionHistoryUiStore = create<SessionHistoryUiState>((set, get) => {
  const initial = loadPersisted()

  const persist = (patch: Partial<PersistedState>) => {
    const next: PersistedState = {
      hiddenSectionCollapsed: get().hiddenSectionCollapsed,
      searchQuery: get().searchQuery,
      ...patch,
    }
    savePersisted(next)
  }

  return {
    ...initial,

    toggleHiddenSection: () => {
      const next = !get().hiddenSectionCollapsed
      persist({ hiddenSectionCollapsed: next })
      set({ hiddenSectionCollapsed: next })
    },

    setSearchQuery: (q) => {
      persist({ searchQuery: q })
      set({ searchQuery: q })
    },

    clearSearch: () => {
      persist({ searchQuery: '' })
      set({ searchQuery: '' })
    },
  }
})
