import { Tooltip } from "@/components/ui/Tooltip";
import { useEffect, useState } from 'react';
import { Plug, RefreshCw, FileJson, Plus, Loader2 } from 'lucide-react';
import { useMCPStore } from '@/store/mcp';
import { MCPServerCard } from '@/components/mcp/MCPServerCard';
import { MCPConfigEditor } from '@/components/mcp/MCPConfigEditor';
import { McpServerFormModal } from '@/components/mcp/McpServerFormModal';
import { cn } from '@/utils/cn';

/** 派生：按 status 分组计数 */
function useStatusCounts() {
  const servers = useMCPStore(s => s.servers);
  return {
    connected: servers.filter(s => s.status === 'connected').length,
    disconnected: servers.filter(s => s.status === 'disconnected').length,
    error: servers.filter(s => s.status === 'error').length,
    total: servers.length
  };
}
export function McpPage() {
  const servers = useMCPStore(s => s.servers);
  const loading = useMCPStore(s => s.loading);
  const fetchServers = useMCPStore(s => s.fetchServers);
  const showConfigEditor = useMCPStore(s => s.showConfigEditor);
  const setShowConfigEditor = useMCPStore(s => s.setShowConfigEditor);
  const editingServer = useMCPStore(s => s.editingServer);
  const clearEditingServer = useMCPStore(s => s.clearEditingServer);
  const [showAddModal, setShowAddModal] = useState(false);
  const counts = useStatusCounts();

  // mount 初始拉取
  useEffect(() => {
    fetchServers();
  }, [fetchServers]);
  return <div className="flex flex-col h-full min-h-0">
      {/* Page header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-[hsl(217.2_32.6%_17.5%)]">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-violet-500/15 flex items-center justify-center">
              <Plug className="w-4 h-4 text-violet-400" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-[hsl(var(--foreground))] flex items-center gap-2">
                MCP 管理
                <span className="text-sm font-normal text-[hsl(var(--muted-foreground))]">
                  ({counts.total})
                </span>
              </h1>
              <p className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">
                管理 MCP 外部工具服务：增删改查、启停、重连、JSON 全量编辑
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Tooltip tip="刷新"><button type="button" onClick={() => fetchServers()} disabled={loading} className="p-2 rounded text-slate-400 hover:text-slate-200 hover:bg-[hsl(217.2_32.6%_14%)] transition-colors disabled:opacity-50">
              <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
            </button></Tooltip>
            <Tooltip tip="编辑 JSON 配置"><button type="button" onClick={() => setShowConfigEditor(true)} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs text-slate-400 hover:text-slate-200 hover:bg-[hsl(217.2_32.6%_14%)] transition-colors">
              <FileJson className="w-3.5 h-3.5" />
              编辑 JSON
            </button></Tooltip>
            <button type="button" onClick={() => setShowAddModal(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-violet-600 hover:bg-violet-500 text-white transition-colors">
              <Plus className="w-3.5 h-3.5" />
              新建
            </button>
          </div>
        </div>

        {/* Status stats strip */}
        {counts.total > 0 && <div className="flex items-center gap-4 mt-3 text-xs">
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              已连接 {counts.connected}
            </span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="w-2 h-2 rounded-full bg-red-500" />
              未连接 {counts.disconnected}
            </span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="w-2 h-2 rounded-full bg-amber-500" />
              出错 {counts.error}
            </span>
          </div>}
      </div>

      {/* Content area */}
      <div className="flex-1 min-h-0 flex flex-col">
        {showConfigEditor ? <MCPConfigEditor /> : <div className="flex-1 min-h-0 overflow-y-auto">
            {loading && servers.length === 0 ? <div className="flex items-center justify-center gap-2 py-16">
                <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
                <span className="text-sm text-slate-400">加载中...</span>
              </div> : servers.length === 0 ? <div className="flex flex-col items-center justify-center py-20 text-center px-4">
                <div className="text-5xl mb-4">🔌</div>
                <p className="text-sm text-slate-300">暂无 MCP 服务</p>
                <p className="text-xs text-slate-500 mt-1.5 mb-4">
                  点击右上「新建」开始配置第一个 MCP server
                </p>
                <button type="button" onClick={() => setShowAddModal(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-violet-600 hover:bg-violet-500 text-white transition-colors">
                  <Plus className="w-3.5 h-3.5" />
                  新建 MCP 服务
                </button>
              </div> : <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-4">
                {servers.map(server => <MCPServerCard key={server.name} server={server} />)}
              </div>}
          </div>}
      </div>

      {/* Add modal */}
      <McpServerFormModal mode="add" open={showAddModal} onClose={() => setShowAddModal(false)} />

      {/* Edit modal — driven by store.editingServer */}
      <McpServerFormModal mode="edit" open={editingServer !== null} initialServer={editingServer ?? undefined} onClose={clearEditingServer} />
    </div>;
}
