import { Tooltip } from "@/components/ui/Tooltip";
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, Copy, Download, Trash2 } from 'lucide-react';
import type { AgentMessageItem } from '@/types/events';
import { useSessionsStore } from '@/store/sessions';
import { getSessionMessagesTail } from '@/api/sessions';
import { getAgentColor, getAgentShortName } from '@/utils/agentColors';
import { buildMessageFileName, copyMarkdown, downloadMarkdownFile, formatSessionTimestamp } from '@/utils/exportMarkdown';
import { cn } from '@/utils/cn';
import { ConfirmModal } from '@/components/common/ConfirmModal';
import { InfoModal, type InfoModalVariant } from '@/components/common/InfoModal';

// 稳定空数组引用，避免 selector `?? []` 每次新建引用触发 useSyncExternalStore 无限循环
const EMPTY_SKILLS: string[] = [];
const EMPTY_TIMELINE: never[] = [];
interface Props {
  item: AgentMessageItem;
  sessionId: string;
}
export function AgentMessageBubble({
  item,
  sessionId
}: Props) {
  const color = getAgentColor(item.agent_name);
  const shortName = getAgentShortName(item.agent_name);
  const [loading, setLoading] = useState(false);
  const [confirmState, setConfirmState] = useState<{
    message: string;
    onConfirm: () => void;
  } | null>(null);
  const [infoState, setInfoState] = useState<{
    title: string;
    message?: string;
    detail?: string;
    variant: InfoModalVariant;
  } | null>(null);
  const activeSessionId = useSessionsStore(s => s.activeSessionId);
  const lastActivatedSkills = useSessionsStore(s => activeSessionId ? s.sessions[activeSessionId]?.lastActivatedSkills ?? EMPTY_SKILLS : EMPTY_SKILLS);
  const sessionStatus = useSessionsStore(s => s.sessions[sessionId]?.meta.status);
  const truncateTimeline = useSessionsStore(s => s.truncateTimeline);
  const timeline = useSessionsStore(s => s.sessions[sessionId]?.timeline ?? EMPTY_TIMELINE);
  const agentName = useSessionsStore(s => s.sessions[sessionId]?.meta.agent_name ?? 'Agent');
  // For download filename (D8): derive sessionLabel from agent_name + created_at
  const sessionCreatedAt = useSessionsStore(s => s.sessions[sessionId]?.meta.created_at ?? 0);
  const isRunning = sessionStatus === 'running' || sessionStatus === 'interrupt_pending';
  const showActions = !isRunning && !item.isStreaming && !loading;

  // Local "just copied" state for icon swap feedback
  const [copied, setCopied] = useState(false);

  // D12.5：modal 开启时收到外部截断 → store truncateState 被置 idle →
  // 关闭 modal + 提示（目标消息可能已被其它标签删除，确认已无意义）
  const truncateState = useSessionsStore(s => s.sessions[sessionId]?.truncateState);
  useEffect(() => {
    if (confirmState && truncateState === 'idle') {
      setConfirmState(null);
      alert('消息已被其它窗口修改，视图已同步');
    }
  }, [truncateState, confirmState]);

  /**
   * Resolve backendMessageId — D9 消费方修复：hydrated 卡片必带 id（hydrate 全路径），
   * live 卡缺 id（后端事件发射点缺陷的残留兜底）时仅拉尾页按内容匹配
   * （live 卡必在尾页范围），不再全量重灌 store（分页窗口不可砸）。
   */
  const resolveBackendMessageId = async (): Promise<string | null> => {
    if (item.backendMessageId) return item.backendMessageId;
    try {
      const resp = await getSessionMessagesTail(sessionId);
      const match = resp.messages.find(m => m.role === 'assistant' && m.content === item.content);
      return match?.id ?? null;
    } catch {
      return null;
    }
  };

  const handleDelete = async () => {
    useSessionsStore.getState().setTruncateState(sessionId, 'confirming')
    setConfirmState({
      message: '将删除此回复及之后的所有消息。确认？',
      onConfirm: async () => {
        setConfirmState(null);
        setLoading(true);
        try {
          const msgId = await resolveBackendMessageId();
          if (!msgId) {
            alert('无法定位消息，请刷新页面后重试');
            return;
          }
          await truncateTimeline(sessionId, msgId, item.id);
        } catch (e) {
          alert('操作失败: ' + (e instanceof Error ? e.message : '未知错误'));
        } finally {
          setLoading(false);
        }
      }
    });
  };

  /** Copy this message's markdown source to the clipboard */
  const handleCopy = async () => {
    try {
      await copyMarkdown(item.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      setInfoState({
        title: '复制失败',
        message: '剪贴板不可用，请检查浏览器权限或改用 HTTPS 环境。',
        detail: e instanceof Error ? e.message : String(e),
        variant: 'error'
      });
    }
  };

  /** Download this message's markdown source as a .md file */
  const handleDownload = async () => {
    // Compute message index: count only user_message + agent_message in order
    const messageOnly = timeline.filter(t => t.type === 'user_message' || t.type === 'agent_message');
    const idx = messageOnly.findIndex(t => t.id === item.id);
    const index = idx >= 0 ? idx + 1 : messageOnly.length + 1;
    const sessionLabel = `${agentName}-${formatSessionTimestamp(sessionCreatedAt)}`;
    const fileName = buildMessageFileName(sessionLabel, sessionId, index);
    const result = await downloadMarkdownFile(item.content, fileName);
    if (result.ok) {
      setInfoState({
        title: '下载已开始',
        message: '文件已生成，请查看浏览器下载文件夹。如未弹出下载，请检查浏览器是否拦截了下载请求。',
        detail: fileName,
        variant: 'success'
      });
    } else {
      setInfoState({
        title: '下载失败',
        message: '生成下载文件时出错。',
        detail: result.error ?? '未知错误',
        variant: 'error'
      });
    }
  };
  return <div className="py-1">
      {/* Confirm modal — replaces window.confirm() */}
      {confirmState && <ConfirmModal message={confirmState.message} onConfirm={confirmState.onConfirm} onCancel={() => { setConfirmState(null); useSessionsStore.getState().setTruncateState(sessionId, 'idle') }} />}
      {/* Info modal — replaces window.alert() for copy/download feedback */}
      {infoState && <InfoModal title={infoState.title} message={infoState.message} detail={infoState.detail} variant={infoState.variant} onClose={() => setInfoState(null)} />}
      {/* Card container — full-width with slight horizontal margin */}
      <div className={cn('group relative rounded-lg px-4 py-3 text-[hsl(var(--foreground))] border min-w-0 shadow-sm', 'bg-[hsl(var(--card))] border-[hsl(var(--border))]')}>
        {/* Header: agent label */}
        <div className="flex items-center gap-2 mb-2">
          <span className={cn('px-2 py-0.5 rounded text-[10px] font-medium border', color.bg, color.text, color.border)}>
            {shortName}
          </span>
        </div>

        {/* Activated skills indicator */}
        {lastActivatedSkills.length > 0 && <div className="mb-2 pb-2 border-b border-emerald-500/20">
            <span className="text-[11px] text-emerald-400">
              ⚡ 已激活: {lastActivatedSkills.join(', ')}
            </span>
          </div>}

        {/* Markdown content — uses CSS custom properties from display settings */}
        <div className="md-content max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {item.content}
          </ReactMarkdown>
        </div>
        {item.isStreaming && <span className="inline-block w-1.5 h-4 bg-slate-400 animate-pulse ml-0.5 -mb-0.5" />}

        {/* Action buttons — hover to show */}
        {showActions && <div className="mt-2 flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
            <Tooltip tip="复制为 Markdown"><button onClick={handleCopy} className={cn('p-1 rounded hover:bg-[hsl(var(--secondary))] transition-colors', copied ? 'text-emerald-500' : 'text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--primary))]')}>
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            </button></Tooltip>
            <Tooltip tip="下载为 Markdown"><button onClick={handleDownload} className="p-1 rounded hover:bg-[hsl(var(--secondary))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--primary))] transition-colors">
              <Download className="w-3.5 h-3.5" />
            </button></Tooltip>
            <Tooltip tip="删除此回复及后续"><button onClick={handleDelete} className="p-1 rounded hover:bg-red-500/10 text-[hsl(var(--muted-foreground))] hover:text-red-500 transition-colors">
              <Trash2 className="w-3.5 h-3.5" />
            </button></Tooltip>
          </div>}
      </div>
    </div>;
}
