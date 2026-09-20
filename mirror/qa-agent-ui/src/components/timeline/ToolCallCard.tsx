import { Tooltip } from "@/components/ui/Tooltip";
import { useState } from 'react';
import { ChevronRight, ChevronDown, Wrench, CheckCircle, XCircle, Loader2, Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ToolCallItem } from '@/types/events';
import { cn } from '@/utils/cn';
interface Props {
  item: ToolCallItem;
}
const TOOL_ICONS: Record<string, string> = {
  file_read: '📄',
  glob_search: '🔍',
  grep: '🔎',
  bash: '⚡',
  file_edit: '✏️',
  agent_spawn: '🤖',
  task_list: '📋',
  task_update: '📝',
  send_message: '📨',
  task_stop: '🛑',
  get_worker_status: '📊',
  save_artifact: '📦',
  load_artifact: '📥',
  request_review: '📋'
};
const REVIEW_STATUS_COLORS: Record<string, string> = {
  approved: 'text-green-400 bg-green-500/10',
  pending: 'text-yellow-400 bg-yellow-500/10',
  rejected: 'text-red-400 bg-red-500/10'
};
export function ToolCallCard({
  item
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const icon = TOOL_ICONS[item.tool_name] ?? '🔧';
  const StatusIcon = item.status === 'running' ? Loader2 : item.status === 'done' ? CheckCircle : XCircle;
  return <div className={cn('rounded-lg border text-sm overflow-hidden', item.status === 'error' ? 'border-red-300 bg-red-50' : 'border-[hsl(var(--border))] bg-[hsl(var(--muted))]')}>
      {/* Header row */}
      <button onClick={() => setExpanded(!expanded)} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-[hsl(var(--secondary))] transition-colors text-left">
        {expanded ? <ChevronDown className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />}
        <span className="text-base">{icon}</span>
        <span className="font-mono font-medium text-slate-300">{item.tool_name}</span>
        {item.agent_name && <span className="text-xs text-slate-600 ml-1">({item.agent_name})</span>}
        <div className="flex-1" />
        {item.elapsed_ms !== undefined && <span className="text-xs text-slate-500 mr-2">{Math.round(item.elapsed_ms)}ms</span>}
        <StatusIcon className={cn('w-4 h-4 flex-shrink-0', item.status === 'running' ? 'text-blue-400 animate-spin' : item.status === 'done' ? 'text-green-400' : 'text-red-400')} />
      </button>

      {/* Expanded details */}
      {expanded && <div className="border-t border-[hsl(217.2_32.6%_17.5%)] divide-y divide-[hsl(217.2_32.6%_13%)]">
          {item.tool_name === 'save_artifact' ? <SaveArtifactDetail item={item} /> : item.tool_name === 'load_artifact' ? <LoadArtifactDetail item={item} /> : <DefaultToolDetail item={item} />}
        </div>}
    </div>;
}

// ── Default tool detail (original) ─────────────────────────────
function DefaultToolDetail({
  item
}: {
  item: ToolCallItem;
}) {
  return <>
      {/* Inputs */}
      <div className="px-3 py-2">
        <p className="text-xs text-slate-500 mb-1 flex items-center gap-1">
          <Wrench className="w-3 h-3" /> 输入参数
        </p>
        <pre className="text-xs text-slate-300 overflow-x-auto bg-[hsl(222.2_84%_4%)] rounded p-2 max-h-48 overflow-y-auto">
          {JSON.stringify(item.inputs, null, 2)}
        </pre>
      </div>

      {/* Outputs */}
      {item.outputs !== undefined && <div className="px-3 py-2">
          <p className="text-xs text-slate-500 mb-1">输出结果</p>
          {typeof item.outputs === 'string' ? <div className="prose prose-invert prose-xs max-w-none text-slate-300 bg-[hsl(222.2_84%_4%)] rounded p-2 max-h-64 overflow-y-auto text-xs [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {item.outputs}
              </ReactMarkdown>
            </div> : <pre className="text-xs text-slate-300 overflow-x-auto bg-[hsl(222.2_84%_4%)] rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap">
              {JSON.stringify(item.outputs, null, 2)}
            </pre>}
        </div>}

      {/* Error */}
      {item.error && <div className="px-3 py-2">
          <p className="text-xs text-red-400 mb-1">错误信息</p>
          <pre className="text-xs text-red-800 bg-red-50 rounded p-2 whitespace-pre-wrap">{item.error}</pre>
        </div>}
    </>;
}

// ── save_artifact structured detail ─────────────────────────────
function SaveArtifactDetail({
  item
}: {
  item: ToolCallItem;
}) {
  const [copied, setCopied] = useState(false);
  const artifactType = item.inputs?.artifact_type as string | undefined;
  const summary = item.inputs?.summary as string | undefined;
  const artifactId = typeof item.outputs === 'string' ? item.outputs : undefined;

  // Fallback to default if key fields missing
  if (!artifactType && !summary) {
    return <DefaultToolDetail item={item} />;
  }
  const handleCopy = () => {
    if (artifactId) {
      navigator.clipboard.writeText(artifactId);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };
  return <div className="px-3 py-2 space-y-2">
      {artifactType && <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">类型:</span>
          <span className="bg-violet-500/20 text-violet-300 px-2 py-0.5 rounded text-xs font-medium">
            {artifactType}
          </span>
        </div>}
      {summary && <div>
          <span className="text-xs text-slate-500">摘要:</span>
          <p className="text-xs text-slate-300 mt-0.5">{summary}</p>
        </div>}
      {artifactId && <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">ID:</span>
          <code className="text-xs text-slate-300 bg-[hsl(222.2_84%_4%)] px-2 py-0.5 rounded font-mono">
            {artifactId}
          </code>
          <Tooltip tip="复制 Artifact ID"><button onClick={handleCopy} className="p-0.5 text-slate-500 hover:text-slate-300 transition-colors">
            {copied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
          </button></Tooltip>
        </div>}
      {item.error && <div>
          <p className="text-xs text-red-400 mb-1">错误信息</p>
          <pre className="text-xs text-red-800 bg-red-50 rounded p-2 whitespace-pre-wrap">{item.error}</pre>
        </div>}
    </div>;
}

// ── load_artifact structured detail ─────────────────────────────
function LoadArtifactDetail({
  item
}: {
  item: ToolCallItem;
}) {
  const [contentExpanded, setContentExpanded] = useState(false);

  // outputs could be a structured object or a string
  if (typeof item.outputs === 'string' || item.outputs === undefined) {
    return <DefaultToolDetail item={item} />;
  }
  const outputs = item.outputs as Record<string, unknown>;
  const artifactType = outputs.artifact_type as string | undefined;
  const version = outputs.version as number | undefined;
  const reviewStatus = outputs.review_status as string | undefined;
  const content = outputs.content as unknown;

  // Fallback if not structured
  if (!artifactType && version === undefined) {
    return <DefaultToolDetail item={item} />;
  }
  const statusColor = reviewStatus ? REVIEW_STATUS_COLORS[reviewStatus] ?? 'text-slate-400 bg-slate-500/10' : null;
  return <div className="px-3 py-2 space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        {artifactType && <div className="flex items-center gap-1.5">
            <span className="text-xs text-slate-500">类型:</span>
            <span className="bg-violet-500/20 text-violet-300 px-2 py-0.5 rounded text-xs font-medium">
              {artifactType}
            </span>
          </div>}
        {version !== undefined && <div className="flex items-center gap-1.5">
            <span className="text-xs text-slate-500">版本:</span>
            <span className="text-xs text-slate-300">v{version}</span>
          </div>}
        {reviewStatus && statusColor && <div className="flex items-center gap-1.5">
            <span className="text-xs text-slate-500">审核:</span>
            <span className={cn('px-2 py-0.5 rounded text-xs font-medium', statusColor)}>
              {reviewStatus}
            </span>
          </div>}
      </div>

      {content !== undefined && <div>
          <button onClick={() => setContentExpanded(!contentExpanded)} className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors">
            {contentExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            内容预览
          </button>
          {contentExpanded && <pre className="text-xs text-slate-300 overflow-x-auto bg-[hsl(222.2_84%_4%)] rounded p-2 max-h-48 overflow-y-auto mt-1 whitespace-pre-wrap">
              {typeof content === 'string' ? content : JSON.stringify(content, null, 2)}
            </pre>}
        </div>}

      {item.error && <div>
          <p className="text-xs text-red-400 mb-1">错误信息</p>
          <pre className="text-xs text-red-800 bg-red-50 rounded p-2 whitespace-pre-wrap">{item.error}</pre>
        </div>}
    </div>;
}
