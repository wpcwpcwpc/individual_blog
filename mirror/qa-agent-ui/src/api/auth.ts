import apiClient from './client'
import type { GetLoginUserResponse } from '@/types/api'

/**
 * GET /auth/get_login_user
 * Returns code=0 + user_info if authenticated, code=300 + redirect_url if not.
 */
export const getLoginUser = (): Promise<GetLoginUserResponse> =>
  apiClient.get<GetLoginUserResponse>('/auth/get_login_user').then(r => r.data)

/**
 * GET /auth/logout — navigate directly (full page redirect).
 * Call window.location.href = getLogoutUrl() instead of axios.
 */
export const getLogoutUrl = (): string => {
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  return `${base}/auth/logout`
}
