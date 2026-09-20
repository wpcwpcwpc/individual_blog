import { Tooltip } from "@/components/ui/Tooltip";
import { useState, useRef, useEffect } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { AlertTriangle, Bell, Trash2, Check, X, Pencil, EyeOff, Eye, Loader2 } from 'lucide-react';
import { useSessionsStore } from '@/store/sessions';
import type { SessionRecord } from '@/store/sessions';
import { useAuthStore } from '@/store/auth';
import { useSessionHistoryUiStore } from '@/store/sessionHistoryUi';
import { CODING_AGENT_ID } from '@/config/agentIds';
import { STATUS_COLORS, STATUS_LABELS } from '@/utils/sessionStatus';
import { ConfirmDeleteModal } from '@/components/common/ConfirmDeleteModal';
import { deleteSession, updateSessionTitle, updateSessionVisibility } from '@/api/sessions';
import { streamPool } from '@/services/streamPool';
import { cn } from '@/utils/cn';
import { HiddenSessionSection } from './HiddenSessionSection';
import type { SessionStatus } from '@/types/api';

// ── EditableTitle ────
function EditableTitle({
  sessionId,
  title,
  editing,
  onCancel
}: {
  sessionId: string;
  title: string;
  editing: boolean;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);
  const setSessionTitle = useSessionsStore(s => s.setSessionTitle);
  const email = useAuthStore(s => s.user?.email);
  useEffect(() => {
    if (editing) {
      setDraft(title);
      // defer focus so React renders input first
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [editing, title]);
  const handleSave = async () => {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== title && email) {
      setSessionTitle(sessionId, trimmed);
      updateSessionTitle(email, sessionId, trimmed).catch(() => {
        setSessionTitle(sessionId, title);
      });
    }
    onCancel();
  };
  const handleCancel = () => {
    setDraft(title);
    onCancel();
  };
  if (editing) {
    return <div className="flex items-center gap-1 flex-1 min-w-0" onClick={e => e.stopPropagation()}>
        <input ref={inputRef} value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => {
        if (e.key === 'Enter') handleSave();
        if (e.key === 'Escape') handleCancel();
      }} onBlur={handleSave} className="flex-1 min-w-0 bg-transparent border-b border-slate-500 text-sm outline-none text-[hsl(var(--foreground))]" />
        <button onClick={handleSave} className="p-0.5 rounded hover:bg-green-500/20 text-green-400 flex-shrink-0">
          <Check className="w-3 h-3" />
        </button>
        <button onClick={handleCancel} className="p-0.5 rounded hover:bg-red-500/20 text-red-400 flex-shrink-0">
          <X className="w-3 h-3" />
        </button>
      </div>;
  }
  return <span className="text-sm font-medium truncate flex-1">
      {title || metaTitleFallback()}
    </span>;
}
function metaTitleFallback() {
  return '未命名会话';
}

// ── SessionItem ────
// 单条会话渲染,主列表与隐藏区共用。hidden 决定显示「隐藏/显现」按钮。
function SessionItem({
  record,
  hidden
}: {
  record: SessionRecord;
  hidden: boolean;
}) {
  const { meta, interrupt, hasUnreadInterrupt, connectionStatus, createError } = record;
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams();
  const setActiveSession = useSessionsStore(s => s.setActiveSession);
  const activeSessionId = useSessionsStore(s => s.activeSessionId);
  const setSessionHidden = useSessionsStore(s => s.setSessionHidden);
  const removeSession = useSessionsStore(s => s.removeSession);
  const email = useAuthStore(s => s.user?.email);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const isActive = routeSessionId === meta.session_id || activeSessionId === meta.session_id;
  const hasInterrupt = meta.status === 'interrupt_pending' && interrupt !== null;
  const isDisconnected = connectionStatus === 'disconnected';
  const showTitle = meta.title || meta.agent_name;
  // harden-create-session-entry D3：pending 占位卡——创建中转 spinner / 失败转错误态
  // （含原因 + 移除按钮）。无真实 session_id，故不挂点击导航（占位卡不可进入）。
  if (meta.status === 'creating') {
    return <div className="w-full text-left px-3 py-2 rounded-md relative cursor-default">
        <div className="flex items-center gap-2 min-w-0">
          <span className={cn('w-2 h-2 rounded-full flex-shrink-0', createError ? 'bg-red-400' : 'bg-violet-400 animate-pulse')} />
          <span className="text-sm font-medium truncate flex-1">
            {createError ? '创建失败' : showTitle}
          </span>
          {!createError && <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-400 flex-shrink-0" />}
          <span className="text-[10px] text-slate-500 flex-shrink-0">
            {createError ? '未创建' : '创建中'}
          </span>
          {createError && <Tooltip tip="移除"><button onClick={e => {
            e.stopPropagation();
            removeSession(meta.session_id);
          }} className="p-1 rounded hover:bg-red-500/20 hover:text-red-400 text-slate-500 transition-colors">
              <X className="w-3 h-3" />
            </button></Tooltip>}
        </div>
        {createError && <p className="mt-1 text-[10px] text-red-400/80 break-words line-clamp-2">{createError}</p>}
      </div>;
  }
  const handleToggleHidden = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const nextHidden = !hidden;
    setSessionHidden(meta.session_id, nextHidden);
    if (!email) return;
    try {
      await updateSessionVisibility(email, meta.session_id, nextHidden);
    } catch {
      // rollback on failure
      setSessionHidden(meta.session_id, hidden);
    }
  };
  return <div key={meta.session_id} role="button" tabIndex={0} onClick={() => {
      setActiveSession(meta.session_id);
      if (hasUnreadInterrupt) {
        useSessionsStore.setState(state => {
          const rec = state.sessions[meta.session_id];
          if (!rec) return state;
          return {
            sessions: {
              ...state.sessions,
              [meta.session_id]: {
                ...rec,
                hasUnreadInterrupt: false
              }
            }
          };
        });
      }
      navigate(`/sessions/${meta.session_id}`);
    }} onKeyDown={e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        setActiveSession(meta.session_id);
        navigate(`/sessions/${meta.session_id}`);
      }
    }} className={cn('sidebar-hover w-full text-left px-3 py-2 rounded-md transition-colors group relative cursor-pointer', isActive ? 'sidebar-active' : '')}>
        <div className="flex items-center gap-2 min-w-0">
          {/* Status dot */}
          <span className={cn('w-2 h-2 rounded-full flex-shrink-0', STATUS_COLORS[meta.status])} />

          {/* Disconnected indicator */}
          {isDisconnected && <Tooltip tip="连接已断开，发送消息将自动重连"><span className="w-2 h-2 rounded-full flex-shrink-0 bg-red-500 animate-pulse" /></Tooltip>}

          {/* Editable title */}
          <EditableTitle sessionId={meta.session_id} title={showTitle} editing={editingSessionId === meta.session_id} onCancel={() => setEditingSessionId(null)} />

          {/* Interrupt icons */}
          {hasInterrupt && <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />}
          {!hasInterrupt && hasUnreadInterrupt && <Bell className="w-3.5 h-3.5 text-amber-400 animate-pulse flex-shrink-0" />}

          {/* Status label */}
          <span className="text-[10px] text-slate-500 flex-shrink-0">
            {STATUS_LABELS[meta.status]}
          </span>

          {/* Hover action buttons */}
          <div className="hidden group-hover:flex items-center gap-0.5 flex-shrink-0">
            <Tooltip tip={hidden ? '取消隐藏' : '隐藏会话'}><button onClick={handleToggleHidden} className="p-1 rounded hover:bg-slate-500/20 hover:text-slate-300 text-slate-500">
              {hidden ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
            </button></Tooltip>
            <Tooltip tip="编辑标题"><button onClick={e => {
            e.stopPropagation();
            setEditingSessionId(meta.session_id);
          }} className="p-1 rounded hover:bg-slate-500/20 hover:text-slate-300 text-slate-500">
              <Pencil className="w-3 h-3" />
            </button></Tooltip>
            <DeleteButton sessionId={meta.session_id} status={meta.status} />
          </div>
        </div>
      </div>;
}
function DeleteButton({
  sessionId,
  status
}: {
  sessionId: string;
  status: SessionStatus;
}) {
  const [showModal, setShowModal] = useState(false);
  const removeSession = useSessionsStore(s => s.removeSession);
  const email = useAuthStore(s => s.user?.email);
  const navigate = useNavigate();
  const location = useLocation();
  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowModal(true);
  };
  const handleConfirm = () => {
    if (!email) throw new Error('用户未登录');
    streamPool.closeConnection(sessionId);
    setShowModal(false);
    // fix-session-io-blocking：乐观删除——UI 先行（导航 + store 移除），HTTP
    // 请求后台发出；失败时告警并从服务端对账（resyncDeletedSession）。
    //
    // NOTE: useParams() returns {} here — SessionList is rendered by the outer
    // `path="*"` route in App.tsx, outside the nested <Routes> that owns the
    // /sessions/:sessionId param. Use location.pathname instead.
    if (location.pathname === `/sessions/${sessionId}`) {
      navigate('/sessions', {
        replace: true
      });
    }
    removeSession(sessionId);
    deleteSession(email, sessionId).catch(() => {
      console.warn(`[SessionList] deleteSession failed for ${sessionId} — reconciling from server`);
      void useSessionsStore.getState().resyncDeletedSession(email, sessionId);
    });
  };
  return <>
      <Tooltip tip="删除会话"><button onClick={handleDeleteClick} className="p-1 rounded hover:bg-red-500/20 hover:text-red-400 text-slate-500 transition-all">
        <Trash2 className="w-3 h-3" />
      </button></Tooltip>
      {showModal && <ConfirmDeleteModal sessionTitle={sessionId.slice(0, 8)} isRunning={status === 'running'} onConfirm={handleConfirm} onCancel={() => setShowModal(false)} />}
    </>;
}
export function SessionList() {
  const sessions = useSessionsStore(s => s.sessions);
  const searchQuery = useSessionHistoryUiStore(s => s.searchQuery);
  // 编码工作台会话不在通用「会话历史」展示（工作台有专属 UI，由工作台首页入口管理）
  const sessionList = Object.values(sessions)
    .filter(r => r.meta.agent_name !== CODING_AGENT_ID)
    .sort((a, b) => b.meta.created_at - a.meta.created_at);
  if (sessionList.length === 0) {
    return <div className="px-3 py-4 text-xs text-slate-500 text-center">
        暂无会话<br />点击「新建会话」开始
      </div>;
  }
  // 搜索非空:扁平渲染所有命中会话(含隐藏),临时忽略 hidden 分组
  const trimmedQuery = searchQuery.trim().toLowerCase();
  if (trimmedQuery) {
    const matched = sessionList.filter(({
      meta
    }) => (meta.title || meta.agent_name || '').toLowerCase().includes(trimmedQuery));
    return <div className="space-y-0.5">
        {matched.length === 0 ? <div className="px-3 py-2 text-xs text-slate-500 text-center">无匹配会话</div> : matched.map(record => <SessionItem key={record.meta.session_id} record={record} hidden={record.meta.hidden === true} />)}
      </div>;
  }
  // 无搜索:主列表 + 隐藏折叠区
  const visibleSessions = sessionList.filter(r => r.meta.hidden !== true);
  const hiddenSessions = sessionList.filter(r => r.meta.hidden === true);
  return <>
      <div className="space-y-0.5">
        {visibleSessions.length === 0 ? <div className="px-3 py-2 text-xs text-slate-500 text-center">主列表为空</div> : visibleSessions.map(record => <SessionItem key={record.meta.session_id} record={record} hidden={false} />)}
      </div>
      <HiddenSessionSection count={hiddenSessions.length}>
        {hiddenSessions.map(record => <SessionItem key={record.meta.session_id} record={record} hidden={true} />)}
      </HiddenSessionSection>
    </>;
}
