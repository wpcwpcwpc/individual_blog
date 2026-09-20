import { Tooltip } from "@/components/ui/Tooltip";
import { useEffect, useState } from 'react';
import { Check, Copy, Download, Folder, File, RefreshCw, Trash2 } from 'lucide-react';
import type { UserMessageItem } from '@/types/events';
import { useSessionsStore } from '@/store/sessions';
import { getSessionMessagesTail } from '@/api/sessions';
import { buildMessageFileName, copyMarkdown, downloadMarkdownFile, formatSessionTimestamp } from '@/utils/exportMarkdown';
import { cn } from '@/utils/cn';
import SkillTags from '@/components/session/SkillTags';
import { ConfirmModal } from '@/components/common/ConfirmModal';
import { InfoModal, type InfoModalVariant } from '@/components/common/InfoModal';
interface Props {
  item: UserMessageItem;
  sessionId: string;
}
function formatTime(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
}

/** Guess if a path is a directory (no extension or ends with /) */
function isLikelyDir(path: string): boolean {
  if (path.endsWith('/')) return true;
  const lastSegment = path.split('/').pop() ?? '';
  return !lastSegment.includes('.');
}

/**
 * fix-resend-from-here（Q1 代码核实）：存储侧用户消息为注入态——server.py 拼装
 * [工作区上下文]/[用户上传文件]/[易协作需求分析] 块 + [用户消息] 标记后落库，
 * 读路径不还原原文。回填取最后一个 [用户消息] 标记之后的原文；无标记
 * （live 卡 / 未注入消息）原样返回。嵌套注入（前缀块 + 工作区包裹各带一标记）
 * 时最后一道标记之后必为用户原文。
 */
function extractRawUserContent(content: string): string {
  const marker = '[用户消息]';
  const idx = content.lastIndexOf(marker);
  if (idx < 0) return content;
  return content.slice(idx + marker.length).replace(/^\n+/, '');
}
export function UserMessageBubble({
  item,
  sessionId
}: Props) {
  const timeStr = formatTime(item.timestamp);
  const paths = item.workspace_context?.selected_paths ?? [];
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

  const sessionStatus = useSessionsStore(s => s.sessions[sessionId]?.meta.status);
  const truncateTimeline = useSessionsStore(s => s.truncateTimeline);
  const setDraftContent = useSessionsStore(s => s.setDraftContent);
  // For download filename (D8): timeline + created_at
  const timeline = useSessionsStore(s => s.sessions[sessionId]?.timeline ?? []);
  const sessionCreatedAt = useSessionsStore(s => s.sessions[sessionId]?.meta.created_at ?? 0);
  const agentName = useSessionsStore(s => s.sessions[sessionId]?.meta.agent_name ?? 'Agent');
  const isRunning = sessionStatus === 'running' || sessionStatus === 'interrupt_pending';
  const showActions = !isRunning && !loading;

  /**
   * Resolve backendMessageId — D9 消费方修复：hydrated 卡片必带 id（hydrate 全路径），
   * live 卡缺 id（后端事件发射点缺陷的残留兜底）时仅拉尾页按内容匹配，
   * 不再全量重灌 store（分页窗口不可砸）。
   */
  const resolveBackendMessageId = async (): Promise<string | null> => {
    if (item.backendMessageId) return item.backendMessageId;
    try {
      const resp = await getSessionMessagesTail(sessionId);
      const match = resp.messages.find(m => m.role === 'user' && m.content === item.content);
      return match?.id ?? null;
    } catch {
      return null;
    }
  };
  const handleResend = async () => {
    useSessionsStore.getState().setTruncateState(sessionId, 'confirming')
    setConfirmState({
      message: '将删除此消息及之后的所有消息，并填回输入框。确认？',
      onConfirm: async () => {
        setConfirmState(null);
        setLoading(true);
        try {
          // fix-resend-from-here D1 时序反转：回填先行（自身卡片文本，零网络）→
          // 截断（backendMessageId 解析后置到 store 内）。失败回滚时输入框内容
          // 保留（spec：用户可复制挽救，不静默丢稿）。
          setDraftContent(sessionId, extractRawUserContent(item.content));
          await truncateTimeline(sessionId, item.backendMessageId, item.id, resolveBackendMessageId);
        } catch (e) {
          alert('操作失败: ' + (e instanceof Error ? e.message : '未知错误'));
        } finally {
          setLoading(false);
        }
      }
    });
  };
  const handleDelete = async () => {
    useSessionsStore.getState().setTruncateState(sessionId, 'confirming')
    setConfirmState({
      message: '将删除此消息及之后的所有消息。确认？',
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
      <div className="group relative rounded-lg px-4 py-3 border border-[hsl(var(--border))] bg-[hsl(var(--card))] shadow-sm">
        {/* Header: username + time */}
        <div className="flex items-center justify-between gap-4 mb-1.5">
          <span className="text-xs font-medium text-[hsl(var(--primary))]">
            {item.username}
          </span>
          {timeStr && <span className="text-[10px] text-[hsl(var(--muted-foreground))] tabular-nums">
              {timeStr}
            </span>}
        </div>

        {/* Activated skill tags */}
        {item.activate_skills && item.activate_skills.length > 0 && <div className="mb-2">
            <SkillTags skills={item.activate_skills} mode="bubble" />
          </div>}

        {/* Content */}
        <div className="text-sm whitespace-pre-wrap break-words">
          {item.content}
        </div>

        {/* Workspace context tags */}
        {paths.length > 0 && <div className="mt-2 pt-2 border-t border-[hsl(var(--border))] flex flex-wrap gap-1.5">
            {paths.map(p => <span key={p} className="inline-flex items-center gap-1 text-[10px] text-[hsl(var(--muted-foreground))] bg-[hsl(var(--secondary))] rounded px-1.5 py-0.5">
                {isLikelyDir(p) ? <Folder className="w-3 h-3 text-amber-400/60" /> : <File className="w-3 h-3 text-[hsl(var(--muted-foreground))]" />}
                {p}
              </span>)}
          </div>}

        {/* Action buttons — hover to show, trailing the content (D9) */}
        {showActions && <div className="mt-2 flex items-center justify-end gap-1 invisible group-hover:visible">
            <Tooltip tip="复制为 Markdown"><button onClick={handleCopy} className={cn('p-1 rounded hover:bg-[hsl(var(--secondary))] transition-colors', copied ? 'text-emerald-400' : 'text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--primary))]')}>
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            </button></Tooltip>
            <Tooltip tip="下载为 Markdown"><button onClick={handleDownload} className="p-1 rounded hover:bg-[hsl(var(--secondary))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--primary))] transition-colors">
              <Download className="w-3.5 h-3.5" />
            </button></Tooltip>
            <Tooltip tip="从此处重新发起对话"><button onClick={handleResend} className="p-1 rounded hover:bg-[hsl(var(--secondary))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--primary))] transition-colors">
              <RefreshCw className="w-3.5 h-3.5" />
            </button></Tooltip>
            <Tooltip tip="删除此消息及后续"><button onClick={handleDelete} className="p-1 rounded hover:bg-red-500/10 text-[hsl(var(--muted-foreground))] hover:text-red-400 transition-colors">
              <Trash2 className="w-3.5 h-3.5" />
            </button></Tooltip>
          </div>}
      </div>
    </div>;
}
