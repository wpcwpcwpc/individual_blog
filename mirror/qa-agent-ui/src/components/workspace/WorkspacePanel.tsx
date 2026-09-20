import { Tooltip } from "@/components/ui/Tooltip";
import { useState, useCallback, useEffect } from 'react';
import { FolderOpen, PanelLeftClose, PanelLeftOpen, Trash2, RefreshCw } from 'lucide-react';
import { cn } from '@/utils/cn';
import { useResizable } from '@/hooks/useResizable';
import { ResizeHandle } from '@/components/ui/ResizeHandle';
import { useWorkspaceStore } from '@/store/workspace';
import { setWorkspace as apiSetWorkspace, deleteWorkspace as apiDeleteWorkspace, browseDirectory } from '@/api/workspace';
import { FileTree } from './FileTree';

// ---------------------------------------------------------------------------
// localStorage helpers for panel open/close persistence
// ---------------------------------------------------------------------------

const PANEL_OPEN_KEY = 'qa-ui:workspace-panel-open';
function readPanelOpen(): boolean {
  try {
    const v = localStorage.getItem(PANEL_OPEN_KEY);
    if (v !== null) return v === 'true';
  } catch {/* ignore */}
  return true; // default open
}
function writePanelOpen(open: boolean) {
  try {
    localStorage.setItem(PANEL_OPEN_KEY, String(open));
  } catch {/* ignore */}
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WorkspacePanelProps {
  sessionId: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function WorkspacePanel({
  sessionId
}: WorkspacePanelProps) {
  const [isPanelOpen, setIsPanelOpen] = useState(readPanelOpen);
  const ws = useWorkspaceStore(s => s.workspaces[sessionId]);
  const storeSetWorkspace = useWorkspaceStore(s => s.setWorkspace);
  const storeClearWorkspace = useWorkspaceStore(s => s.clearWorkspace);
  const hasWorkspace = !!ws?.rootPath;

  // Path input state
  const [pathInput, setPathInput] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [isReplacing, setIsReplacing] = useState(false);
  const [browsing, setBrowsing] = useState(false);

  // Resizable panel width
  const {
    width,
    isDragging,
    handleProps
  } = useResizable({
    defaultWidth: 260,
    minWidth: 200,
    maxWidth: 450,
    direction: 'right',
    storageKey: 'qa-ui:workspace-panel-width'
  });

  // Toggle panel open/close
  const togglePanel = useCallback(() => {
    setIsPanelOpen(prev => {
      const next = !prev;
      writePanelOpen(next);
      return next;
    });
  }, []);

  // Handle set workspace
  const handleSetWorkspace = useCallback(async () => {
    const trimmed = pathInput.trim();
    if (!trimmed) return;
    setError('');
    setSubmitting(true);
    try {
      const info = await apiSetWorkspace(sessionId, trimmed);
      storeSetWorkspace(sessionId, info.root_path, info.tree);
      setPathInput('');
      setIsReplacing(false);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || '设置工作区失败';
      setError(typeof msg === 'string' ? msg : '设置工作区失败');
    } finally {
      setSubmitting(false);
    }
  }, [pathInput, sessionId, storeSetWorkspace]);

  // Handle delete workspace
  const handleDelete = useCallback(async () => {
    try {
      await apiDeleteWorkspace(sessionId);
      storeClearWorkspace(sessionId);
      setIsReplacing(false);
    } catch {/* ignore */}
  }, [sessionId, storeClearWorkspace]);

  // Handle replace workspace
  const handleReplace = useCallback(() => {
    setIsReplacing(true);
    setPathInput('');
    setError('');
  }, []);

  // Handle browse folder (native dialog)
  const handleBrowse = useCallback(async () => {
    setBrowsing(true);
    setError('');
    try {
      const result = await browseDirectory();
      if (result.selected_path) {
        setPathInput(result.selected_path);
      } else if (result.error) {
        setError(result.error);
      }
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || '无法打开文件夹选择器';
      setError(typeof msg === 'string' ? msg : '无法打开文件夹选择器');
    } finally {
      setBrowsing(false);
    }
  }, []);

  // Handle Enter key
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSetWorkspace();
    }
  }, [handleSetWorkspace]);

  // Reset replace mode when switching sessions
  useEffect(() => {
    setIsReplacing(false);
    setPathInput('');
    setError('');
  }, [sessionId]);

  // ── Collapsed state ──
  if (!isPanelOpen) {
    return <div className="flex-shrink-0 w-9 flex flex-col items-center pt-2 border-r border-[hsl(217.2_32.6%_17.5%)]">
        <Tooltip tip="展开工作区面板"><button onClick={togglePanel} className="p-1.5 rounded hover:bg-white/10 transition-colors text-gray-400 hover:text-gray-200">
          <PanelLeftOpen className="w-4 h-4" />
        </button></Tooltip>
      </div>;
  }

  // ── Expanded state ──
  const showSetupUI = !hasWorkspace || isReplacing;
  return <>
      <div className="flex-shrink-0 flex flex-col border-r border-[hsl(217.2_32.6%_17.5%)] bg-[hsl(222.2_84%_4.9%)]/50" style={{
      width: `${width}px`
    }}>
        {/* ── Header ── */}
        <div className="flex items-center h-9 px-2 border-b border-[hsl(217.2_32.6%_17.5%)] flex-shrink-0">
          <FolderOpen className="w-4 h-4 text-amber-400/80 mr-1.5 flex-shrink-0" />

          {hasWorkspace && !isReplacing ? <>
              <Tooltip tip={ws.rootPath}><span className="text-xs text-gray-300 truncate flex-1">
                {ws.rootPath}
              </span></Tooltip>
              <Tooltip tip="更换工作区"><button onClick={handleReplace} className="p-1 rounded hover:bg-white/10 text-gray-400 hover:text-gray-200 flex-shrink-0">
                <RefreshCw className="w-3.5 h-3.5" />
              </button></Tooltip>
              <Tooltip tip="删除工作区"><button onClick={handleDelete} className="p-1 rounded hover:bg-white/10 text-gray-400 hover:text-red-400 flex-shrink-0">
                <Trash2 className="w-3.5 h-3.5" />
              </button></Tooltip>
            </> : <span className="text-xs text-gray-400 flex-1">工作区</span>}

          <Tooltip tip="隐藏面板"><button onClick={togglePanel} className="p-1 rounded hover:bg-white/10 text-gray-400 hover:text-gray-200 flex-shrink-0 ml-auto">
            <PanelLeftClose className="w-3.5 h-3.5" />
          </button></Tooltip>
        </div>

        {/* ── Body ── */}
        {showSetupUI ? (/* Setup / Replace UI */
      <div className="flex-1 flex flex-col items-center justify-center p-4 gap-3">
            <FolderOpen className="w-8 h-8 text-gray-500" />
            <p className="text-xs text-gray-400 text-center">
              {isReplacing ? '输入新的工作区路径' : '设置本地工作区目录'}
            </p>
            <input type="text" value={pathInput} onChange={e => {
          setPathInput(e.target.value);
          setError('');
        }} onKeyDown={handleKeyDown} placeholder="D:\project\src" className={cn('w-full px-2 py-1.5 text-xs rounded border bg-transparent text-gray-200', 'placeholder:text-gray-500 focus:outline-none focus:ring-1', error ? 'border-red-500/60 focus:ring-red-500/40' : 'border-gray-600 focus:ring-violet-500/40')} disabled={submitting} />
            <button onClick={handleBrowse} disabled={submitting || browsing} className={cn('w-full text-xs px-3 py-1.5 rounded border border-gray-600', 'text-gray-300 hover:bg-white/5 transition-colors', 'disabled:opacity-40 disabled:cursor-not-allowed')}>
              {browsing ? '正在打开...' : '📁 浏览本地文件夹...'}
            </button>
            {error && <p className="text-xs text-red-400 w-full">{error}</p>}
            <div className="flex gap-2 w-full">
              {isReplacing && <button onClick={() => {
            setIsReplacing(false);
            setError('');
          }} className="flex-1 text-xs px-3 py-1.5 rounded border border-gray-600 text-gray-400 hover:bg-white/5">
                  取消
                </button>}
              <button onClick={handleSetWorkspace} disabled={!pathInput.trim() || submitting || browsing} className={cn('flex-1 text-xs px-3 py-1.5 rounded text-white transition-colors', 'bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed')}>
                {submitting ? '设置中...' : '设置工作区'}
              </button>
            </div>
          </div>) : (/* File tree */
      <FileTree sessionId={sessionId} />)}
      </div>

      {/* Resize handle between workspace panel and timeline */}
      <ResizeHandle handleProps={handleProps} isDragging={isDragging} />
    </>;
}
