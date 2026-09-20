import { Tooltip } from "@/components/ui/Tooltip";
import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Cpu, LogOut, ChevronLeft, ChevronRight, ChevronDown, MoreHorizontal } from 'lucide-react';
import { SessionList } from '@/components/session/SessionList';
import { SessionHistorySearch } from '@/components/session/SessionHistorySearch';
import { CreateSessionModal } from '@/components/session/CreateSessionModal';
import { useAppStore } from '@/store/app';
import { useAuthStore } from '@/store/auth';
import { useSidebarStore } from '@/store/sidebar';
import { getLogoutUrl } from '@/api/auth';
import { navItems, type NavItem } from '@/config/navRegistry';
import { cn } from '@/utils/cn';

// 打开外部链接。部分桌面 WebView 默认拦截 <a target="_blank">,
// 需显式 window.open 兜底;再失败则 location.href 兜底(同窗跳转,最后手段)。
const openExternal = (url: string): void => {
  if (!url) return;
  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (!opened) {
    window.location.href = url;
  }
};

export function Sidebar() {
  const [showCreate, setShowCreate] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const health = useAppStore(s => s.health);
  const user = useAuthStore(s => s.user);
  const location = useLocation();
  const {
    collapsed,
    toggle
  } = useSidebarStore();

  // group:'more' 项(MCP/会话追踪/系统状态)收进「更多」折叠组
  const mainNavItems = navItems.filter(item => item.group !== 'more');
  const moreNavItems = navItems.filter(item => item.group === 'more');
  const moreRouteActive = moreNavItems.some(item => item.type === 'navigate' && location.pathname === item.route);
  const moreExpanded = moreOpen || moreRouteActive;
  // 主动禁用 milvus (milvus === 'disabled') 属运维降级，非故障 → 视为健康（绿）
  const isHealthy = !!health && (health.status === 'ok' || health.milvus === 'disabled');
  const healthColor = !health ? 'bg-slate-500' : isHealthy ? 'bg-green-400' : 'bg-yellow-400';
  const renderNavIcon = (item: NavItem) => {
    const Icon = item.icon;
    const isActive = item.type === 'navigate' && (location.pathname === item.route || location.pathname.startsWith(`${item.route}/`));
    const baseClasses = cn('flex items-center gap-3 px-2 py-2 rounded-md text-sm transition-colors w-full', 'hover:bg-[var(--surface-4)]', isActive && 'bg-[var(--surface-4)] font-medium', item.type === 'action' && 'text-[var(--accent-color)]');
    if (item.type === 'navigate' && item.route) {
      return <Link to={item.route} className={baseClasses} style={!isActive ? {
        color: 'var(--text-secondary)'
      } : undefined}>
          <Icon className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span>{item.label}</span>}
        </Link>;
    }

    // link 型 — 外部链接（新开页签）
    if (item.type === 'link' && item.url) {
      const url = item.url;
      return <button onClick={() => openExternal(url)} className={baseClasses} style={{
        color: 'var(--text-secondary)'
      }}>
          <Icon className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span>{item.label}</span>}
        </button>;
    }

    // link 型 — 外部链接（如大世界工具箱），新开页签
    if (item.type === 'link' && item.url) {
      const url = item.url;
      return <button onClick={() => openExternal(url)} className={baseClasses} style={{
        color: 'var(--text-secondary)'
      }}>
          <Icon className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span>{item.label}</span>}
        </button>;
    }

    // action 型 — new-chat: 打开 CreateSessionModal
    return <button onClick={() => setShowCreate(true)} className={baseClasses} style={{
      color: 'var(--text-secondary)'
    }}>
        <Icon className="w-4 h-4 flex-shrink-0" />
        {!collapsed && <span>{item.label}</span>}
      </button>;
  };
  return <>
      {/* Header */}
      <div className="flex-shrink-0 flex items-center gap-2 px-3 py-3" style={{
      borderBottom: '1px solid var(--border-color)',
      backgroundColor: 'var(--surface-deep)'
    }}>
        <Cpu className="w-5 h-5 flex-shrink-0" style={{
        color: 'var(--accent-color)'
      }} />
        {!collapsed && <span className="font-semibold text-sm tracking-wide" style={{
        color: 'var(--text-primary)'
      }}>
            QA Agent Console
          </span>}
        {!collapsed && <Tooltip tip={health?.status ?? 'unknown'}><span className={cn('ml-auto w-2 h-2 rounded-full flex-shrink-0', healthColor)} /></Tooltip>}
      </div>

      {/* Nav 功能区 — 「更多」组默认折叠,当前路由命中组内项时自动展开;内容超高时可独立滚动 */}
      <div className="min-h-0 overflow-y-auto px-2 py-2 space-y-0.5">
        {mainNavItems.map(item => <div key={item.id}>{renderNavIcon(item)}</div>)}
        {moreNavItems.length > 0 && <>
            <button onClick={() => setMoreOpen(v => !v)} className={cn('flex items-center gap-3 px-2 py-2 rounded-md text-sm transition-colors w-full', 'hover:bg-[var(--surface-4)]', moreRouteActive && 'bg-[var(--surface-4)] font-medium')} style={{
          color: 'var(--text-secondary)'
        }}>
              <MoreHorizontal className="w-4 h-4 flex-shrink-0" />
              {!collapsed && <>
                  <span>更多</span>
                  <ChevronDown className={cn('ml-auto w-4 h-4 flex-shrink-0 transition-transform', moreExpanded && 'rotate-180')} />
                </>}
            </button>
            {moreExpanded && <div className="space-y-0.5 pt-0.5">
                {moreNavItems.map(item => <div key={item.id} className={collapsed ? 'pl-2' : 'pl-4'}>{renderNavIcon(item)}</div>)}
              </div>}
          </>}
      </div>

      {/* Session list area — 折叠时完全隐藏 */}
      {!collapsed && <>
          <div className="flex-shrink-0 px-2 pt-2 pb-1">
            <p className="px-2 py-1 text-xs uppercase tracking-wider" style={{
          color: 'var(--text-muted)'
        }}>会话历史</p>
            <SessionHistorySearch />
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto px-2 py-1 space-y-1">
            <SessionList />
          </div>
        </>}
      {/* Spacer when collapsed — pushes user/fold to bottom */}
      {collapsed && <div className="flex-1" />}

      {/* Bottom: user + collapse button */}
      <div className="flex-shrink-0 px-2 py-2 space-y-1" style={{
      borderTop: '1px solid var(--border-color)'
    }}>
        {user && <div className="flex items-center gap-2 px-2 py-1.5 rounded-md">
            {collapsed ? <Tooltip tip={user.email}><div className="w-6 h-6 rounded-full flex items-center justify-center text-xs flex-shrink-0" style={{
            backgroundColor: 'var(--surface-4)',
            color: 'var(--text-secondary)'
          }}>
                {(user.name || user.email).charAt(0).toUpperCase()}
              </div></Tooltip> : <>
                <div className="flex-1 min-w-0">
                  <p className="text-xs truncate" style={{
              color: 'var(--text-primary)'
            }}>{user.name || user.email}</p>
                  <p className="text-[10px] truncate" style={{
              color: 'var(--text-muted)'
            }}>{user.email}</p>
                </div>
                <Tooltip tip="退出登录"><button onClick={() => {
              window.location.href = getLogoutUrl();
            }} className="transition-colors flex-shrink-0 hover:text-red-400" style={{
              color: 'var(--text-muted)'
            }}>
                  <LogOut className="w-3.5 h-3.5" />
                </button></Tooltip>
              </>}
          </div>}

        {/* Collapse toggle */}
        <Tooltip tip={collapsed ? '展开侧边栏（显示会话历史）' : '折叠侧边栏'}><button onClick={toggle} className="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors w-full hover:bg-[var(--surface-4)]" style={{
          color: 'var(--text-muted)'
        }}>
          {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
          {!collapsed && <span>折叠</span>}
        </button></Tooltip>
      </div>

      {/* CreateSessionModal */}
      {showCreate && <CreateSessionModal onClose={() => setShowCreate(false)} />}
    </>;
}
