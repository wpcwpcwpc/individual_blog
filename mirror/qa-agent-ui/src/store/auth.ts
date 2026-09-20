import { create } from 'zustand'
import type { UserInfo } from '@/types/api'

export type AuthStatus = 'loading' | 'authed' | 'unauthenticated'

interface AuthState {
  user: UserInfo | null
  status: AuthStatus

  // Actions
  setUser: (user: UserInfo) => void
  clearUser: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'loading',

  setUser: (user) => set({ user, status: 'authed' }),

  clearUser: () => set({
    user: null,
    status: 'unauthenticated',
  }),
}))