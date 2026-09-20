import type { AxiosError } from 'axios'

/**
 * 提取后端错误信息（FastAPI HTTPException → {detail}）。
 * 网络层失败（无响应：后端未就绪/调试器暂停/连接被断）给可读提示，
 * 避免 UI 直接展示 "AxiosError: Network Error"。
 */
export function apiErrorMessage(err: unknown): string {
  const ax = err as AxiosError<{ detail?: string }>
  if (ax?.isAxiosError) {
    if (ax.response) {
      const detail = ax.response.data?.detail
      const detailText =
        typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : ''
      return detailText || `请求失败（HTTP ${ax.response.status}）`
    }
    if (ax.code === 'ECONNABORTED') return '请求超时（后端响应过慢或被调试器暂停）'
    return '网络错误：后端无响应（后端未就绪、调试器暂停或已断开）'
  }
  return String(err)
}
