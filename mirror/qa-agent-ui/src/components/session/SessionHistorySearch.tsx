import { Search, X } from 'lucide-react';
import { useSessionHistoryUiStore } from '@/store/sessionHistoryUi';

// ── SessionHistorySearch ────
// 搜索框:接 sessionHistoryUi store。搜索范围为全局(含隐藏会话),
// 由 SessionList 负责实际过滤;本组件只管输入态 + 清空。
export function SessionHistorySearch() {
  const searchQuery = useSessionHistoryUiStore(s => s.searchQuery);
  const setSearchQuery = useSessionHistoryUiStore(s => s.setSearchQuery);
  const clearSearch = useSessionHistoryUiStore(s => s.clearSearch);
  return <div className="flex items-center gap-1 px-2 py-1" onClick={e => e.stopPropagation()}>
      <Search className="w-3 h-3 flex-shrink-0" style={{
      color: 'var(--text-muted)'
    }} />
      <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="搜索会话标题" className="flex-1 min-w-0 bg-transparent text-xs outline-none" style={{
      color: 'var(--text-primary)'
    }} />
      {searchQuery && <button onClick={e => {
      e.stopPropagation();
      clearSearch();
    }} className="p-0.5 rounded hover:bg-[var(--surface-4)] flex-shrink-0" style={{
      color: 'var(--text-muted)'
    }}>
          <X className="w-3 h-3" />
        </button>}
    </div>;
}
