import { Tooltip } from "@/components/ui/Tooltip";
import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Loader2, Square, Play } from 'lucide-react';
import { sendMessage, abortSession } from '@/api/sessions';
import { streamPool } from '@/services/streamPool';
import { useSessionsStore, isAborting } from '@/store/sessions';
import { useAuthStore } from '@/store/auth';
import { useWorkspaceStore } from '@/store/workspace';
import { useAppStore } from '@/store/app';
import { useResizable } from '@/hooks/useResizable';
import { ResizeHandle } from '@/components/ui/ResizeHandle';
import { SelectedPathsTags } from '@/components/workspace/SelectedPathsTags';
import { MCPButton } from '@/components/mcp/MCPButton';
import SkillPicker from '@/components/session/SkillPicker';
import SkillTags from '@/components/session/SkillTags';
import { FileUploader } from '@/components/common/FileUploader';
import { resumeInterruptedSession } from '@/utils/resumeSession';
import { enqueueRestoringSend, type ResendPayload } from '@/utils/restoringSend';
import type { UploadedFileRef } from '@/types/api';
import { cn } from '@/utils/cn';
const EMPTY_PATHS: string[] = [];

interface Props {
  sessionId: string;
}
export function MessageInputBar({
  sessionId
}: Props) {
  const [content, setContent] = useState('');
  const [stream, setStream] = useState(true);
  const [loading, setLoading] = useState(false);
  // harden-resume-interrupt-chain 2.1 — interim hint while the background
  // restore→resend chain runs (input stays usable during the agent rebuild).
  const [restoreHint, setRestoreHint] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Skill Picker state
  const [pickerOpen, setPickerOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [isComposing, setIsComposing] = useState(false);
  const status = useSessionsStore(s => s.sessions[sessionId]?.meta.status ?? 'idle');
  const connectionStatus = useSessionsStore(s => s.sessions[sessionId]?.connectionStatus);
  const draftContent = useSessionsStore(s => s.draftContent[sessionId] ?? '');
  const setDraftContentStore = useSessionsStore(s => s.setDraftContent);
  const appendUserMessage = useSessionsStore(s => s.appendUserMessage);
  const updateSessionMeta = useSessionsStore(s => s.updateSessionMeta);
  const lastSeqId = useSessionsStore(s => s.sessions[sessionId]?.lastSeqId ?? 0);
  const user = useAuthStore(s => s.user);
  const onDemandSkills = useAppStore(s => s.onDemandSkills)();
  // 5.1: Model selector state — initialized from store defaultModel
  const availableModels = useAppStore(s => s.availableModels);
  const defaultModel = useAppStore(s => s.defaultModel);
  const [selectedModel, setSelectedModel] = useState('');
  // 上传文件（可选，作为 agent 上下文；session 已存在，FileUploader 直接用 sessionId）
  const [uploadedFile, setUploadedFile] = useState<UploadedFileRef | null>(null);
  // Sync selectedModel with defaultModel once it's loaded from backend
  useEffect(() => {
    if (defaultModel) {
      setSelectedModel(prev => prev || defaultModel);
    }
  }, [defaultModel]);

  // Pick up draft content from store (set by truncate → resend flow)
  useEffect(() => {
    if (draftContent) {
      setContent(draftContent);
      setDraftContentStore(sessionId, ''); // clear from store after consuming
      // Focus textarea so user can immediately edit/send
      setTimeout(() => textareaRef.current?.focus(), 0);
    }
  }, [draftContent, sessionId, setDraftContentStore]);

  // Compute a dynamic max height — cap at 50% of viewport height
  const [maxH, setMaxH] = useState(() => Math.max(120, Math.floor(window.innerHeight * 0.5)));
  useEffect(() => {
    const onResize = () => setMaxH(Math.max(120, Math.floor(window.innerHeight * 0.5)));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Resizable height: drag top edge upward to increase height
  const {
    width: barHeight,
    isDragging,
    handleProps
  } = useResizable({
    defaultWidth: 80,
    minWidth: 60,
    maxWidth: maxH,
    storageKey: 'qa-ui:input-bar-height',
    direction: 'up'
  });
  const isRunning = status === 'running';
  // fix-abort-latency D2：「中断中」过渡标记 —— 输入仍锁定（等终态），按钮转圈并置文案
  const abortingRun = useSessionsStore(s => isAborting(s.sessions[sessionId]));
  const isInterrupted = status === 'aborted';
  const isInterruptPending = status === 'interrupt_pending';
  // add-session-history-pagination D11 S2：truncate cutting 中禁用输入（互斥新 run）
  const truncateCutting = useSessionsStore(s => s.sessions[sessionId]?.truncateState === 'cutting');
  const inputLocked = isRunning || truncateCutting;
  const canSend = !isRunning && !isInterrupted && content.trim().length > 0 && !loading && !truncateCutting;
  const selectedPaths = useWorkspaceStore(s => s.workspaces[sessionId]?.selectedPaths) ?? EMPTY_PATHS;
  const clearSelections = useWorkspaceStore(s => s.clearSelections);

  // Close Skill Picker helper
  const closePickerAndClear = useCallback(() => {
    setPickerOpen(false);
    setSearchQuery('');
    // Remove the "/" prefix from content
    setContent(prev => {
      if (prev.startsWith('/')) return '';
      return prev;
    });
  }, []);

  // Handle Skill selection (toggle)
  const handleSkillSelect = useCallback((name: string) => {
    setSelectedSkills(prev => prev.includes(name) ? prev.filter(s => s !== name) : [...prev, name]);
    closePickerAndClear();
  }, [closePickerAndClear]);

  // Handle Skill tag removal
  const handleSkillRemove = useCallback((name: string) => {
    setSelectedSkills(prev => prev.filter(s => s !== name));
  }, []);
  const handleSend = async () => {
    if (!canSend) return;
    const msg = content.trim();
    const username = user?.name ?? '用户';
    const workspaceContext = selectedPaths.length > 0 ? {
      selected_paths: [...selectedPaths]
    } : undefined;
    const skillsToActivate = selectedSkills.length > 0 ? [...selectedSkills] : undefined;

    // Optimistic insert: show user message in timeline immediately
    appendUserMessage(sessionId, msg, username, workspaceContext, skillsToActivate, uploadedFile);
    setContent('');
    setSelectedSkills([]);
    setUploadedFile(null);
    setLoading(true);
    try {
      // Ensure WS connection is alive before sending (fire-and-forget)
      streamPool.ensureConnection(sessionId, lastSeqId);
      await sendMessage(sessionId, {
        content: msg,
        stream,
        workspace_context: workspaceContext,
        ...(skillsToActivate ? {
          activate_skills: skillsToActivate
        } : {}),
        // 5.5: attach selected model as override for this message's run
        ...(selectedModel ? {
          model_override: selectedModel
        } : {}),
        // 上传文件：非空时后端注入 [用户上传文件] 块到 prompt，agent file_read 读
        ...(uploadedFile ? {
          uploaded_files: [uploadedFile]
        } : {})
      });
      // Only clear selections after successful send
      if (selectedPaths.length > 0) {
        clearSelections(sessionId);
      }
    } catch (err: unknown) {
      const errResp = (err as { response?: { status?: number; data?: unknown } })?.response;
      console.error('[MessageInputBar] send failed', {
        status: errResp?.status,
        data: errResp?.data,
        isAxios: (err as { isAxiosError?: boolean })?.isAxiosError,
        message: (err as Error)?.message,
      });
      // If agent is missing (orphaned session), try restore then retry once
      const status = errResp?.status;
      const detail = (errResp?.data as { detail?: string } | undefined)?.detail;
      console.warn('[MessageInputBar] retry condition check', {
        statusIs400: status === 400,
        detail,
        detailMatches: detail === 'No agent attached to session',
        hasEmail: !!user?.email,
      });
      if (status === 400 && detail === 'No agent attached to session' && user?.email) {
        // harden-resume-interrupt-chain 2.1: the agent rebuild can take seconds —
        // don't hold the input loading for it. Release the input, show an interim
        // hint, and run restore → resend in background (deduped per session, 2.2).
        // The optimistic user message stays in the timeline; on failure the hint
        // tells the user to retry (message is not silently dropped).
        setLoading(false);
        setRestoreHint('正在恢复会话…');
        const payload: ResendPayload = {
          content: msg,
          stream,
          ...(workspaceContext ? { workspace_context: workspaceContext } : {}),
          ...(skillsToActivate ? { activate_skills: skillsToActivate } : {}),
          ...(selectedModel ? { model_override: selectedModel } : {}),
        };
        void enqueueRestoringSend(sessionId, user.email, payload)
          .then(() => {
            setRestoreHint(null);
            if (selectedPaths.length > 0) {
              clearSelections(sessionId);
            }
          })
          .catch((retryErr: unknown) => {
            console.warn('[MessageInputBar] restore-and-resend failed', retryErr);
            setRestoreHint('会话恢复失败，请重新发送');
          });
        return; // finally still runs: setLoading(false) + focus — input usable now
      }
    } finally {
      setLoading(false);
      textareaRef.current?.focus();
    }
  };
  const handleAbort = async () => {
    if (loading) return;
    setLoading(true);
    try {
      await abortSession(sessionId);
      // fix-abort-latency D3：POST 成功即乐观渲染「中断中」，不等 run_aborted
      useSessionsStore.getState().markAborting(sessionId);
    } catch (err) {
      console.error('[MessageInputBar] abort failed', err);
    } finally {
      setLoading(false);
    }
  };
  const handleResume = async () => {
    if (loading) return;
    const msg = content.trim();
    const username = user?.name ?? '用户';

    // Optimistic: show user message if non-empty
    if (msg) {
      appendUserMessage(sessionId, msg, username);
    }
    setContent('');
    setLoading(true);
    try {
      // Delegated to shared helper. Helper throws on failure;
      // caller reverts status to 'aborted' so user can retry.
      if (!user?.email) throw new Error('用户未登录');
      await resumeInterruptedSession(sessionId, user.email, lastSeqId, msg);
    } catch (err) {
      console.error('[MessageInputBar] resume failed', err);
      // Revert to aborted so user can retry
      updateSessionMeta(sessionId, {
        status: 'aborted'
      });
    } finally {
      setLoading(false);
      textareaRef.current?.focus();
    }
  };

  // Handle textarea onChange — detect "/" trigger
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    if (!isComposing) {
      // Detect "/" trigger: value is "/" (just typed "/" in an empty input)
      if (newValue === '/' && !pickerOpen) {
        setPickerOpen(true);
        setSearchQuery('');
        setContent(newValue);
        return;
      }

      // Update search query while picker is open
      if (pickerOpen && newValue.startsWith('/')) {
        setSearchQuery(newValue.slice(1));
        setContent(newValue);
        return;
      }

      // Picker open but "/" was deleted (e.g. backspace on "/")
      if (pickerOpen && !newValue.startsWith('/')) {
        setPickerOpen(false);
        setSearchQuery('');
      }
    }
    setContent(newValue);
  };
  const handleKeyDown = (e: React.KeyboardEvent) => {
    // When Picker is open, forward keyboard events
    if (pickerOpen) {
      if (['ArrowDown', 'ArrowUp', 'Escape'].includes(e.key)) {
        e.preventDefault();
        // Forward to SkillPicker via ref
        const panel = pickerRef.current as HTMLDivElement & {
          _onKeyDown?: (e: React.KeyboardEvent) => void;
        } | null;
        panel?._onKeyDown?.(e);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const panel = pickerRef.current as HTMLDivElement & {
          _onKeyDown?: (e: React.KeyboardEvent) => void;
        } | null;
        panel?._onKeyDown?.(e);
        return;
      }
      // Backspace when content is just "/" — close picker
      if (e.key === 'Backspace' && content === '/') {
        // Let onChange handle the actual close
        return;
      }
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (isInterrupted) {
        handleResume();
      } else if (!isRunning) {
        handleSend();
      }
    }
  };

  // Determine action button config based on status
  const actionButton = isRunning ? {
    icon: abortingRun ? Loader2 : Square,
    color: 'btn-danger',
    handler: handleAbort,
    title: abortingRun ? '正在中断' : '中断执行',
    disabled: loading || abortingRun
  } : isInterrupted ? {
    icon: Play,
    color: 'btn-success',
    handler: handleResume,
    title: '恢复执行 (Enter)',
    disabled: loading
  } : {
    icon: Send,
    color: canSend ? 'btn-primary' : 'btn-disabled',
    handler: handleSend,
    title: isRunning ? 'Agent 正在运行中' : '发送消息 (Enter)',
    disabled: !canSend
  };
  const ActionIcon = actionButton.icon;
  return <div className="flex-shrink-0 flex flex-col bg-[hsl(var(--background))]">
      {/* Horizontal resize handle at the very top */}
      <ResizeHandle handleProps={handleProps} isDragging={isDragging} orientation="horizontal" />

      {/* Selected paths tags (above input) */}
      <SelectedPathsTags sessionId={sessionId} />

      {/* Selected skill tags (above input) */}
      <SkillTags skills={selectedSkills} onRemove={handleSkillRemove} mode="input" />

      {/* Status hints */}
      <div className="px-4">
        {restoreHint && <div className="text-xs text-violet-400/80 mb-1 flex items-center gap-1.5">
            <Loader2 className="w-3 h-3 animate-spin" />
            {restoreHint}
          </div>}
        {isInterruptPending && <div className="text-xs text-amber-400/70 mb-1 flex items-center gap-1.5">
            ⚠️ Agent 当前已暂停，发送的消息将在审核完成后生效
          </div>}
        {isInterrupted && <div className="text-xs text-orange-400/70 mb-1 flex items-center gap-1.5">
            ⏸️ 会话已中断，输入消息并点击恢复按钮继续执行
          </div>}
      </div>

      {/* Input card — textarea + controls all in one bordered card */}
      <div className="px-4 pb-3 pt-1">
        <div className={cn('relative flex flex-col rounded-lg border bg-[hsl(var(--card))]', 'border-[hsl(var(--border))] focus-within:border-[hsl(var(--primary))]/50 transition-colors', isRunning && 'opacity-60')} style={{
        height: barHeight
      }}>
          {/* Skill Picker floating panel */}
          {pickerOpen && onDemandSkills.length > 0 && <SkillPicker ref={pickerRef} skills={onDemandSkills} selectedSkills={selectedSkills} searchQuery={searchQuery} onSelect={handleSkillSelect} onClose={closePickerAndClear} />}

          {/* Textarea area */}
          <div className="flex-1 min-h-0">
            <textarea ref={textareaRef} value={content} onChange={handleChange} onKeyDown={handleKeyDown} onCompositionStart={() => setIsComposing(true)} onCompositionEnd={() => setIsComposing(false)} placeholder={connectionStatus === 'disconnected' ? '连接已断开，输入消息自动重连...' : isRunning ? 'Agent 运行中，点击 ⏹ 可中断执行...' : isInterrupted ? '输入消息继续执行... (Enter 恢复)' : '输入任务描述，Enter 发送，Shift+Enter 换行，/ 选择 Skill'} disabled={inputLocked} className={cn('w-full h-full bg-transparent px-4 py-2.5 text-sm text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))]', 'focus:outline-none resize-none')} />
          </div>

          {/* Bottom toolbar: stream toggle + action button */}
          <div className="flex items-center justify-between px-3 py-1.5 border-t border-[hsl(var(--border))] flex-shrink-0">
            {/* Left: stream toggle + MCP button + model selector */}
            <div className="flex items-center gap-3">
              {/* Stream toggle */}
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => setStream(!stream)} className={cn('w-7 h-3.5 rounded-full transition-colors relative', stream ? 'bg-violet-600' : 'bg-slate-600')}>
                  <span className={cn('absolute top-0.5 block w-2.5 h-2.5 rounded-full bg-white transition-transform', stream ? 'left-[14px]' : 'left-0.5')} />
                </button>
                <span className="text-[10px] text-slate-500 select-none">流式</span>
              </div>

              {/* MCP services button */}
              <MCPButton />

              {/* 5.2 & 5.3: Model selector — only rendered when models are available */}
              {availableModels.length > 0 && <select value={selectedModel} onChange={e => setSelectedModel(e.target.value)}
            // 5.4: disabled when agent is running / truncate cutting (D11 S2)
            disabled={inputLocked} className={cn('h-6 px-1.5 rounded text-[11px] border border-[hsl(var(--border))]', 'bg-[hsl(var(--card))] text-[hsl(var(--foreground))]', 'focus:outline-none focus:border-[hsl(var(--primary))]/50 transition-colors', inputLocked && 'opacity-40 cursor-not-allowed')}>
                  {availableModels.map(model => <option key={model.name} value={model.name}>{model.name}</option>)}
                </select>}

              {/* 上传文件（可选，作为 agent 上下文；session 已存在，直接用 sessionId） */}
              <FileUploader sessionId={sessionId} value={uploadedFile} onChange={setUploadedFile} disabled={inputLocked} selectedModel={selectedModel} compact />
            </div>

            {/* Action button — Send / Stop / Resume */}
            <Tooltip tip={actionButton.title}><button onClick={actionButton.handler} disabled={actionButton.disabled} className={cn('h-7 px-3 rounded-md flex items-center justify-center gap-1.5 text-xs font-medium flex-shrink-0 transition-colors', actionButton.color)}>
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ActionIcon className="w-3.5 h-3.5" />}
              {isRunning ? (abortingRun ? '中断中…' : '中断') : isInterrupted ? '恢复' : '发送'}
            </button></Tooltip>
          </div>
        </div>
      </div>
    </div>;
}
