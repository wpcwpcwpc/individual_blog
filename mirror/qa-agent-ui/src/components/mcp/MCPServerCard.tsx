import { Tooltip } from "@/components/ui/Tooltip";
import { useState } from 'react';
import { RefreshCw, Power, PowerOff, Trash2, ChevronDown, Loader2, Pencil } from 'lucide-react';
import * as Collapsible from '@radix-ui/react-collapsible';
import { useMCPStore } from '@/store/mcp';
import { MCPToolList } from './MCPToolList';
import { cn } from '@/utils/cn';
import type { MCPServerInfo } from '@/types/api';
interface Props {
  server: MCPServerInfo;
}
const STATUS_INDICATOR: Record<MCPServerInfo['status'], {
  color: string;
  label: string;
}> = {
  connected: {
    color: 'bg-emerald-500',
    label: '已连接'
  },
  disconnected: {
    color: 'bg-red-500',
    label: '未连接'
  },
  error: {
    color: 'bg-amber-500',
    label: '出错'
  }
};
function formatConnectedDuration(connectedAt: number | null): string | null {
  if (!connectedAt) return null;
  const diff = Math.floor(Date.now() / 1000 - connectedAt);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  return `${Math.floor(diff / 3600)}h ${Math.floor(diff % 3600 / 60)}m`;
}
export function MCPServerCard({
  server
}: Props) {
  const [toolsOpen, setToolsOpen] = useState(false);
  const operatingServer = useMCPStore(s => s.operatingServer);
  const enableServer = useMCPStore(s => s.enableServer);
  const disableServer = useMCPStore(s => s.disableServer);
  const reconnectServer = useMCPStore(s => s.reconnectServer);
  const deleteServer = useMCPStore(s => s.deleteServer);
  const setEditingServer = useMCPStore(s => s.setEditingServer);
  const isOperating = operatingServer === server.name;
  const indicator = STATUS_INDICATOR[server.status];
  const duration = formatConnectedDuration(server.connected_at);
  const handleDelete = async () => {
    if (!confirm(`确认删除 MCP 服务 "${server.name}"？`)) return;
    await deleteServer(server.name);
  };
  return <Collapsible.Root open={toolsOpen} onOpenChange={setToolsOpen}>
      <div className="rounded-lg border border-[hsl(217.2_32.6%_17.5%)] bg-[hsl(217.2_32.6%_10%)] overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-2.5 px-3 py-2.5">
          {/* Status dot */}
          <span className={cn('w-2 h-2 rounded-full flex-shrink-0', indicator.color)} />

          {/* Info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-slate-200 truncate">{server.name}</span>
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono text-slate-500 bg-[hsl(217.2_32.6%_14%)]">
                {server.transport}
              </span>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-[11px] text-slate-500">{indicator.label}</span>
              {server.status === 'connected' && <>
                  <span className="text-[11px] text-slate-600">·</span>
                  <span className="text-[11px] text-slate-500">{server.tool_count} 工具</span>
                </>}
              {duration && <>
                  <span className="text-[11px] text-slate-600">·</span>
                  <span className="text-[11px] text-slate-500">{duration}</span>
                </>}
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-1 flex-shrink-0">
            {isOperating ? <Loader2 className="w-3.5 h-3.5 text-slate-400 animate-spin" /> : <>
                {/* Edit (any status) — triggers McpServerFormModal edit mode */}
                <ActionBtn icon={Pencil} title="编辑" onClick={() => setEditingServer(server)} />
                {/* Connected: Reconnect + Disable */}
                {server.status === 'connected' && <>
                    <ActionBtn icon={RefreshCw} title="重连" onClick={() => reconnectServer(server.name)} />
                    <ActionBtn icon={PowerOff} title="禁用" onClick={() => disableServer(server.name)} variant="warn" />
                  </>}
                {/* Disconnected: Enable + Delete */}
                {server.status === 'disconnected' && <>
                    <ActionBtn icon={Power} title="启用" onClick={() => enableServer(server.name)} variant="success" />
                    <ActionBtn icon={Trash2} title="删除" onClick={handleDelete} variant="danger" />
                  </>}
                {/* Error: Reconnect + Delete */}
                {server.status === 'error' && <>
                    <ActionBtn icon={RefreshCw} title="重连" onClick={() => reconnectServer(server.name)} />
                    <ActionBtn icon={Trash2} title="删除" onClick={handleDelete} variant="danger" />
                  </>}
              </>}
          </div>
        </div>

        {/* Error message */}
        {server.status === 'error' && server.error && <div className="px-3 pb-2">
            <div className="text-[11px] text-red-400/80 bg-red-500/10 rounded px-2 py-1 break-words">
              ⚠️ {server.error}
            </div>
          </div>}

        {/* Tool list trigger */}
        <Collapsible.Trigger asChild>
          <button className="w-full flex items-center gap-1.5 px-3 py-1.5 text-[11px] text-slate-500 hover:text-slate-400 border-t border-[hsl(217.2_32.6%_14%)] transition-colors">
            <ChevronDown className={cn('w-3 h-3 transition-transform', toolsOpen && 'rotate-180')} />
            {server.tool_count} 工具
          </button>
        </Collapsible.Trigger>

        {/* Collapsible tool list */}
        <Collapsible.Content className="border-t border-[hsl(217.2_32.6%_14%)] bg-[hsl(222.2_84%_5%)]">
          <MCPToolList tools={server.tools} />
        </Collapsible.Content>
      </div>
    </Collapsible.Root>;
}

// ── Tiny action button ──────────────────────────────────────────

interface ActionBtnProps {
  icon: React.FC<{
    className?: string;
  }>;
  title: string;
  onClick: () => void;
  variant?: 'default' | 'success' | 'warn' | 'danger';
}
function ActionBtn({
  icon: Icon,
  title,
  onClick,
  variant = 'default'
}: ActionBtnProps) {
  const colors = {
    default: 'text-slate-500 hover:text-slate-300',
    success: 'text-emerald-500/70 hover:text-emerald-400',
    warn: 'text-amber-500/70 hover:text-amber-400',
    danger: 'text-red-500/70 hover:text-red-400'
  };
  return <Tooltip tip={title}><button type="button" onClick={onClick} className={cn('p-1 rounded transition-colors', colors[variant])}>
      <Icon className="w-3.5 h-3.5" />
    </button></Tooltip>;
}
