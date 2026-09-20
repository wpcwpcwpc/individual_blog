import { Tooltip } from "@/components/ui/Tooltip";
import { Zap } from 'lucide-react';
import { useMCPStore, useConnectedCount } from '@/store/mcp';
import { cn } from '@/utils/cn';
export function MCPButton() {
  const togglePanel = useMCPStore(s => s.togglePanel);
  const isOpen = useMCPStore(s => s.isOpen);
  const servers = useMCPStore(s => s.servers);
  const connectedCount = useConnectedCount();
  const hasFetched = servers.length > 0 || isOpen;
  return <Tooltip tip="MCP 服务管理"><button type="button" onClick={togglePanel} className={cn('relative flex items-center gap-1 px-2 py-1 rounded-md text-xs transition-colors', isOpen ? 'bg-violet-600/20 text-violet-400' : 'text-slate-500 hover:text-slate-300 hover:bg-[hsl(217.2_32.6%_14%)]')}>
      <Zap className="w-3.5 h-3.5" />
      <span className="select-none">MCP</span>
      {hasFetched && <span className={cn('ml-0.5 min-w-[16px] h-4 px-1 rounded-full text-[10px] font-medium flex items-center justify-center', connectedCount > 0 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-600/30 text-slate-500')}>
          {connectedCount}
        </span>}
    </button></Tooltip>;
}
