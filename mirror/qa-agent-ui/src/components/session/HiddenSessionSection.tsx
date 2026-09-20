import { ChevronDown, ChevronRight } from 'lucide-react';
import type { SessionRecord } from '@/store/sessions';
import { useSessionHistoryUiStore } from '@/store/sessionHistoryUi';
import { cn } from '@/utils/cn';

// ── HiddenSessionSection ────
// 已隐藏会话折叠区。无隐藏会话时不渲染。
// 折叠态:只显示标题行(数量 + chevron);展开态:渲染 children。
// 折叠状态持久化到 sessionHistoryUi store(localStorage)。
export function HiddenSessionSection({
  count,
  children
}: {
  count: number;
  children: React.ReactNode;
}) {
  const collapsed = useSessionHistoryUiStore(s => s.hiddenSectionCollapsed);
  const toggle = useSessionHistoryUiStore(s => s.toggleHiddenSection);
  if (count === 0) return null;
  return <div className="pt-2">
      <button onClick={e => {
      e.stopPropagation();
      toggle();
    }} className="flex items-center gap-1 w-full px-3 py-1 text-xs hover:bg-[var(--surface-4)] rounded-md transition-colors" style={{
      color: 'var(--text-muted)'
    }}>
        {collapsed ? <ChevronRight className="w-3 h-3 flex-shrink-0" /> : <ChevronDown className="w-3 h-3 flex-shrink-0" />}
        <span>已隐藏会话</span>
        <span className="ml-auto">{count}</span>
      </button>
      {!collapsed && <div className={cn('space-y-0.5 mt-1')}>
          {children}
        </div>}
    </div>;
}

// ── 类型导出 ────
export type { SessionRecord };
