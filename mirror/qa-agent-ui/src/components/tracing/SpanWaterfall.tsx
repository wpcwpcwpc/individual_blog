import { Tooltip } from "@/components/ui/Tooltip";
import { useEffect, useState } from 'react';
import { ChevronRight, ChevronDown, ChevronUp } from 'lucide-react';
import { getTraceSpans, type Span } from '@/api/tracing';
interface Props {
  traceId: string;
  traceDurationMs: number;
  traceStartTime: string;
}
interface FlatSpanRow {
  span: Span;
  depth: number;
  hasChildren: boolean;
  childrenIds: string[];
}
const kindIcons: Record<string, string> = {
  INTERNAL: '⚙️',
  SERVER: '🖥️',
  CLIENT: '📡',
  PRODUCER: '📤',
  CONSUMER: '📥'
};
const statusBarColors: Record<string, string> = {
  OK: '#22c55e',
  ERROR: '#ef4444',
  UNSET: '#8b5cf6'
};
export function SpanWaterfall({
  traceId,
  traceDurationMs,
  traceStartTime
}: Props) {
  const [spans, setSpans] = useState<Span[]>([]);
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<FlatSpanRow[]>([]);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);
  // 'duration' = left-aligned bars (compare lengths, tidy); 'timeline' = waterfall (when)
  const [viewMode, setViewMode] = useState<'duration' | 'timeline'>('duration');
  useEffect(() => {
    setLoading(true);
    getTraceSpans(traceId).then(res => {
      setSpans(res.spans);
      setRows(buildSpanTree(res.spans));
    }).finally(() => setLoading(false));
  }, [traceId]);
  if (loading) {
    return <div className="flex justify-center py-4" style={{
      backgroundColor: 'var(--surface-deep)'
    }}>
        <div className="w-4 h-4 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>;
  }
  if (spans.length === 0) {
    return <div className="text-center py-4 text-xs" style={{
      backgroundColor: 'var(--surface-deep)',
      color: 'var(--text-muted)'
    }}>
        无 Span 数据
      </div>;
  }
  const traceStart = new Date(traceStartTime).getTime();
  const totalMs = traceDurationMs || 1;
  const visibleRows = getVisibleRows(rows, collapsedIds);
  const toggleCollapse = (spanId: string) => {
    setCollapsedIds(prev => {
      const next = new Set(prev);
      if (next.has(spanId)) next.delete(spanId);else next.add(spanId);
      return next;
    });
  };
  const selectedSpan = selectedSpanId ? spans.find(s => s.span_id === selectedSpanId) : null;
  return <div style={{
    backgroundColor: 'var(--surface-deep)'
  }} className="border-t">
      {/* View mode toggle: duration (left-aligned) vs timeline (waterfall) */}
      <div className="flex justify-end items-center px-2 py-1" style={{
      borderBottom: '1px solid var(--border-color)'
    }}>
        <Tooltip tip={viewMode === 'duration' ? '当前：时长（左对齐）。点击切到时间线（waterfall）' : '当前：时间线（waterfall）。点击切到时长（左对齐）'}><button onClick={() => setViewMode(prev => prev === 'duration' ? 'timeline' : 'duration')} className="text-[10px] px-2 py-0.5 rounded flex items-center gap-1" style={{
          color: 'var(--text-muted)',
          border: '1px solid var(--border-color)'
        }}>
          {viewMode === 'duration' ? '时长（左对齐）' : '时间线（waterfall）'}
        </button></Tooltip>
      </div>
      <div className="flex">
        {/* Span list */}
        <div className="flex-1 min-w-0">
          {visibleRows.map(row => {
          const spanStart = new Date(row.span.start_time).getTime();
          const offsetPct = Math.max(0, (spanStart - traceStart) / totalMs * 100);
          const widthPct = Math.max(0.5, row.span.duration_ms / totalMs * 100);
          const barColor = statusBarColors[row.span.status_code] || '#8b5cf6';
          return <div key={row.span.span_id} className="flex items-center text-xs h-7 hover:bg-white/5 cursor-pointer" style={{
            borderBottom: '1px solid var(--border-color)'
          }} onClick={() => setSelectedSpanId(prev => prev === row.span.span_id ? null : row.span.span_id)}>
                {/* Name column — wide, span names need room */}
                <div className="w-[70%] flex items-center gap-1 px-2 truncate" style={{
              paddingLeft: `${row.depth * 16 + 8}px`
            }}>
                  {row.hasChildren ? <button onClick={e => {
                e.stopPropagation();
                toggleCollapse(row.span.span_id);
              }} className="flex-shrink-0" style={{
                color: 'var(--text-muted)'
              }}>
                      {collapsedIds.has(row.span.span_id) ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                    </button> : <span className="w-3" />}
                  <span className="flex-shrink-0">{kindIcons[row.span.span_kind] || '⚙️'}</span>
                  <span className="truncate" style={{
                color: 'var(--text-primary)'
              }}>{row.span.name}</span>
                </div>
                {/* Waterfall bar column — slim; detail panel needs the space */}
                <div className="w-[30%] relative h-full flex items-center px-2">
                  <div className="h-3 rounded-sm" style={{
                marginLeft: viewMode === 'timeline' ? `${offsetPct}%` : 0,
                width: `${widthPct}%`,
                backgroundColor: barColor,
                opacity: 0.8,
                minWidth: '2px'
              }} />
                  <span className="absolute right-2 text-[10px] tabular-nums" style={{
                color: 'var(--text-muted)'
              }}>
                    {row.span.duration_ms}ms
                  </span>
                </div>
              </div>;
        })}
        </div>

        {/* Detail panel — flexible width, takes the bulk of the space */}
        {selectedSpan && <div className="flex-1 min-w-[460px] border-l overflow-y-auto max-h-[600px] p-3 text-xs space-y-2" style={{
        borderColor: 'var(--border-color)'
      }}>
            <div className="flex items-center justify-between">
              <span className="font-medium" style={{
            color: 'var(--text-primary)'
          }}>{selectedSpan.name}</span>
              <button onClick={() => setSelectedSpanId(null)} className="text-xs" style={{
            color: 'var(--text-muted)'
          }}>
                ✕
              </button>
            </div>
            <div className="space-y-1" style={{
          color: 'var(--text-secondary)'
        }}>
              <p>Kind: {selectedSpan.span_kind}</p>
              <p>Status: {selectedSpan.status_code}</p>
              <p>Duration: {selectedSpan.duration_ms}ms</p>
              {selectedSpan.status_message && <p>Message: {selectedSpan.status_message}</p>}
            </div>
            {Object.keys(selectedSpan.attributes).length > 0 && <div>
                <p className="font-medium mb-1" style={{
            color: 'var(--text-primary)'
          }}>Attributes</p>
                <div className="space-y-1" style={{
            color: 'var(--text-secondary)'
          }}>
                  {Object.entries(selectedSpan.attributes).map(([k, v]) => {
              const raw = formatAttrValue(v);
              const isLong = raw.length > 160;
              return <ExpandableAttr key={k} attrKey={k} value={raw} isLong={isLong} />;
            })}
                </div>
              </div>}
          </div>}
      </div>
    </div>;
}
function ExpandableAttr({
  attrKey,
  value,
  isLong
}: {
  attrKey: string;
  value: string;
  isLong: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  return <div className="flex flex-col">
      <span className="font-mono flex-shrink-0" style={{
      color: 'var(--text-muted)'
    }}>{attrKey}:</span>
      {isLong && !expanded ? <div className="flex items-start gap-1">
          <span className="break-all whitespace-pre-wrap">{value.slice(0, 160)}…</span>
          <button onClick={() => setExpanded(true)} className="flex-shrink-0 ml-1 text-[10px] hover:underline flex items-center gap-0.5" style={{
        color: 'var(--text-accent)'
      }}>
            更多 <ChevronDown className="w-3 h-3" />
          </button>
        </div> : <div>
          <span className="break-all whitespace-pre-wrap">{value}</span>
          {isLong && <button onClick={() => setExpanded(false)} className="ml-1 text-[10px] hover:underline flex items-center gap-0.5" style={{
        color: 'var(--text-accent)'
      }}>
              收起 <ChevronUp className="w-3 h-3" />
            </button>}
        </div>}
    </div>;
}
function buildSpanTree(spans: Span[]): FlatSpanRow[] {
  const map = new Map<string, Span>();
  const childrenMap = new Map<string, string[]>();
  for (const span of spans) {
    map.set(span.span_id, span);
    if (!childrenMap.has(span.span_id)) childrenMap.set(span.span_id, []);
  }
  const roots: string[] = [];
  for (const span of spans) {
    if (span.parent_span_id && map.has(span.parent_span_id)) {
      childrenMap.get(span.parent_span_id)!.push(span.span_id);
    } else {
      roots.push(span.span_id);
    }
  }
  const result: FlatSpanRow[] = [];
  function dfs(spanId: string, depth: number) {
    const span = map.get(spanId);
    if (!span) return;
    const children = childrenMap.get(spanId) || [];
    result.push({
      span,
      depth,
      hasChildren: children.length > 0,
      childrenIds: children
    });
    for (const childId of children) {
      dfs(childId, depth + 1);
    }
  }
  for (const rootId of roots) {
    dfs(rootId, 0);
  }
  return result;
}
function getVisibleRows(rows: FlatSpanRow[], collapsedIds: Set<string>): FlatSpanRow[] {
  const visible: FlatSpanRow[] = [];
  const hiddenParents = new Set<string>();
  for (const row of rows) {
    if (hiddenParents.has(row.span.parent_span_id || '')) {
      hiddenParents.add(row.span.span_id);
      continue;
    }
    visible.push(row);
    if (collapsedIds.has(row.span.span_id)) {
      hiddenParents.add(row.span.span_id);
    }
  }
  return visible;
}
function formatAttrValue(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}
