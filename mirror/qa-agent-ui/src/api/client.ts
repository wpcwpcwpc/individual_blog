import axios from 'axios'

// baseURL: 生产构建走 '/api' 相对(同域 nginx /api/ 反代);
// dev 走 vite proxy /api → localhost:8000;
// 桌面壳/独立客户端可在 .env.local 注入绝对地址(如 http://127.0.0.1:8000/api)直连。
// 用 ?? 而非 ||:空串不再作为 falsy 兜底(注入绝对地址时生效)。
const baseURL = import.meta.env.VITE_API_BASE_URL ?? '/api'

const apiClient = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30000,
  withCredentials: true,  // required: send session cookie on cross-origin requests
})

// Guard against redirect loops when many concurrent requests 401 at once.
let _redirectingToLogin = false

/**
 * Build /auth/login_required URL with origin_url so post-login redirect returns
 * to current page. Bypasses unreliable Referer header (stripped by Referrer-Policy,
 * private mode, direct URL entry → would fall back to frontend_url '/' = AI 助手).
 */
export function getLoginRequiredUrl(originUrl: string): string {
  return `${baseURL}/auth/login_required?origin_url=${encodeURIComponent(originUrl)}`
}

/**
 * API base (absolute when injected by the shell, '/api' relative in web mode).
 * For non-axios resources (e.g. <img src>) that need the full backend URL.
 */
export function apiBase(): string {
  return baseURL
}

function redirectToLogin() {
  if (_redirectingToLogin) return
  _redirectingToLogin = true
  window.location.href = getLoginRequiredUrl(window.location.href)
}

// Global response interceptor: handle business error codes
apiClient.interceptors.response.use(
  (response) => {
    const data = response.data
    if (data && typeof data === 'object') {
      // LLM_NOT_CONFIGURED → dispatch event so the app can show a toast and navigate
      if (data.code === 409 && data.error === 'LLM_NOT_CONFIGURED') {
        window.dispatchEvent(new CustomEvent('llm-not-configured', { detail: data }))
        return Promise.reject(new Error(data.message || 'LLM Token 未配置'))
      }
    }
    return response
  },
  (error) => {
    // Session expired / invalid cookie mid-session → bounce to login flow.
    if (error?.response?.status === 401) {
      redirectToLogin()
    }
    return Promise.reject(error)
  }
)

export default apiClient
