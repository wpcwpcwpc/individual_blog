/**
 * 历史分页 kill-switch（add-session-history-pagination D10）。
 *
 * localStorage `qa-ui:history-pagination` = `'off'` 时回退全量模式：
 * 加载入口走全量 hydrate、哨兵不渲染、truncate 走既有全量回滚路径。
 * 每次读取实时查询（同步、开销可忽略），切换后无需刷新即在下一次
 * 会话加载/回灌时生效。
 */

export const HISTORY_PAGING_KILL_SWITCH_KEY = 'qa-ui:history-pagination'

/** 分页功能是否启用（kill-switch 未关闭）。 */
export function isHistoryPaginationEnabled(): boolean {
  try {
    return localStorage.getItem(HISTORY_PAGING_KILL_SWITCH_KEY) !== 'off'
  } catch {
    // localStorage 不可用（隐私模式等）→ 默认启用
    return true
  }
}
