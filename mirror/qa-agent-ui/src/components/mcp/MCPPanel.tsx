import { Tooltip } from "@/components/ui/Tooltip";
import { X, FileJson, Loader2, RefreshCw } from 'lucide-react';
import { useMCPStore } from '@/store/mcp';
import { MCPServerCard } from './MCPServerCard';
import { AddServerForm } from './AddServerForm';
import { MCPConfigEditor } from './MCPConfigEditor';
const PANEL_WIDTH = 400;
export function MCPPanel() {
  const isOpen = useMCPStore(s => s.isOpen);
  const closePanel = useMCPStore(s => s.closePanel);
  const servers = useMCPStore(s => s.servers);
  const loading = useMCPStore(s => s.loading);
  const showConfigEditor = useMCPStore(s => s.showConfigEditor);
  const setShowConfigEditor = useMCPStore(s => s.setShowConfigEditor);
  const fetchServers = useMCPStore(s => s.fetchServers);
  return <div className="flex-shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out border-l border-[hsl(217.2_32.6%_17.5%)]" style={{
    width: isOpen ? PANEL_WIDTH : 0
  }}>
      <div className="h-full flex flex-col bg-[hsl(222.2_84%_6.5%)]" style={{
      width: PANEL_WIDTH
    }}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[hsl(217.2_32.6%_17.5%)] flex-shrink-0">
          <h3 className="text-sm font-semibold text-slate-200">
            {showConfigEditor ? 'MCP JSON 配置' : 'MCP 服务管理'}
          </h3>
          <div className="flex items-center gap-1">
            {!showConfigEditor && <Tooltip tip="刷新"><button type="button" onClick={fetchServers} disabled={loading} className="p-1 text-slate-500 hover:text-slate-300 transition-colors">
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </button></Tooltip>}
            <button type="button" onClick={closePanel} className="p-1 text-slate-500 hover:text-white transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Content */}
        {showConfigEditor ? <MCPConfigEditor /> : <>
            {/* Server list (scrollable) */}
            <div className="flex-1 overflow-y-auto min-h-0">
              {loading && servers.length === 0 ? <div className="flex items-center justify-center gap-2 py-12">
                  <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
                  <span className="text-sm text-slate-400">加载中...</span>
                </div> : servers.length === 0 ? <div className="flex flex-col items-center justify-center py-12 text-center px-4">
                  <div className="text-3xl mb-3">🔌</div>
                  <p className="text-sm text-slate-400">暂无 MCP 服务</p>
                  <p className="text-xs text-slate-500 mt-1">点击下方「添加 MCP 服务」开始配置</p>
                </div> : <div className="p-3 space-y-2">
                  {servers.map(server => <MCPServerCard key={server.name} server={server} />)}
                </div>}
            </div>

            {/* Add server form */}
            <div className="flex-shrink-0 border-t border-[hsl(217.2_32.6%_17.5%)]">
              <AddServerForm />
            </div>

            {/* Footer: JSON config button */}
            <div className="flex-shrink-0 px-3 py-2 border-t border-[hsl(217.2_32.6%_17.5%)]">
              <button type="button" onClick={() => setShowConfigEditor(true)} className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded text-xs text-slate-500 hover:text-slate-300 hover:bg-[hsl(217.2_32.6%_14%)] transition-colors">
                <FileJson className="w-3.5 h-3.5" />
                编辑 JSON 配置
              </button>
            </div>
          </>}
      </div>
    </div>;
}
