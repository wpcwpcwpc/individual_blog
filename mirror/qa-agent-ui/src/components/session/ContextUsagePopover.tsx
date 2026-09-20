import { useState } from 'react'
import { Package, RefreshCw, X } from 'lucide-react'
import { useSessionsStore } from '@/store/sessions'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/utils/cn'
import { formatCost, formatDuration, formatPercent, formatTokens } from '@/utils/formatTokens'
import type { RunUsageEntry, UsageMetricsPayload } from '@/types/api'

interface Props {
  sessionId: string
  onClose: () => void
  /** 徽章上方可用像素高度（badge 位于输入框上方、贴近视口底部时传入，面板向上展开不溢出） */
  maxHeight?: number
}

/** 分类条目定义：key → (label, color)。颜色复用既有语义色 tailwind class。 */
const CATEGORY_DEFS: Array<{ key: 'system_prompt' | 'tools' | 'mcp_tools' | 'messages' | 'free_space'; label: string; color: string }> = [
  { key: 'system_prompt', label: '系统提示', color: 'bg-violet-500' },
  { key: 'tools', label: '内置工具', color: 'bg-sky-500' },
  { key: 'mcp_tools', label: 'MCP 工具', color: 'bg-cyan-500' },
  { key: 'messages', label: '对话消息', color: 'bg-emerald-500' },
  { key: 'free_space', label: '剩余空间', color: 'bg-zinc-600' },
]

const SEGMENT_LABELS: Record<string, string> = {
  base: '基础指令',
  plan_first: '计划优先',
  tool_anti_patterns: '工具反模式',
  task_management: '任务管理',
  extra: '其他（定义/技能/规则）',
}

/**
 * 上下文详情面板（add-context-usage-visibility）。
 *
 * 上半：分类占比横向堆叠条形图（纯 flex 百分比宽度）+ 图例；
 * 下半：本次会话消耗 —— 每轮一行明细（缺失维度 "—"）+ agent 筛选。
 * 数据源 store.sessionUsage.breakdown（open 时 refreshContextUsage 已拉新）。
 */
export function ContextUsagePopover({ sessionId, onClose, maxHeight }: Props) {
  const breakdown = useSessionsStore(s => s.sessionUsage[sessionId]?.breakdown ?? null)
  const compressed = useSessionsStore(s => s.sessionUsage[sessionId]?.compressed ?? false)
  const refreshContextUsage = useSessionsStore(s => s.refreshContextUsage)
  const [refreshing, setRefreshing] = useState(false)
  const [agentFilter, setAgentFilter] = useState<string>('all')

  // 手动刷新：force 旁路后端缓存强制重算（add-context-usage-visibility）
  const handleRefresh = async () => {
    if (refreshing) return
    setRefreshing(true)
    try {
      await refreshContextUsage(sessionId, true)
    } finally {
      setRefreshing(false)
    }
  }

  const max = breakdown?.max_context_tokens ?? 0
  const cats = breakdown?.categories
  const usedCategories = CATEGORY_DEFS.filter(d => d.key !== 'free_space')
  const used = cats == null ? null : usedCategories.reduce((sum, d) => sum + (cats[d.key] ?? 0), 0)
  const totalForRatio = max > 0 ? max : (used ?? 0)

  const agentNames = Array.from(
    new Set([
      ...(breakdown?.recent_runs.map(r => r.agent_name) ?? []),
      ...Object.keys(breakdown?.per_agent ?? {}),
    ]),
  )
  const showFilter = agentNames.length > 1

  const allRuns = breakdown?.recent_runs ?? []
  const filteredRuns = agentFilter === 'all' ? allRuns : allRuns.filter(r => r.agent_name === agentFilter)

  return (
    <div
      className={cn(
        // 徽章现位于输入框左上方（贴近视口底部）：向上展开 + 左对齐，
        // 避免底部越界（top-full 时代向下溢出）与左侧被面板边界裁切（right-0 时代向左溢出）
        'absolute left-0 bottom-full mb-2 z-50 w-[420px] max-w-[90vw]',
        'flex flex-col overflow-hidden rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] shadow-xl',
        'text-[hsl(var(--foreground))]',
      )}
      style={maxHeight != null ? { maxHeight } : undefined}
      role="dialog"
      aria-label="上下文占用详情"
    >
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[hsl(var(--border))]">
        <span className="text-sm font-semibold">上下文占用</span>
        <div className="flex items-center gap-1">
          <Tooltip tip="刷新（强制重算，拉取最新上下文与 token 消耗）">
            <button
              onClick={() => void handleRefresh()}
              disabled={refreshing}
              className="p-1 rounded text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))] transition-colors disabled:opacity-50"
            >
              <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
            </button>
          </Tooltip>
          <button onClick={onClose} className="p-1 rounded text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))] transition-colors">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {breakdown == null ? (
        // skeleton / 无数据占位
        <div className="px-4 py-6 text-xs text-[hsl(var(--muted-foreground))] text-center">
          正在加载上下文占用…
        </div>
      ) : (
        <div className="px-4 py-3 space-y-3 min-h-0 flex-1 overflow-y-auto">
          {/* 压缩提示行 */}
          {compressed && (
            <div className="flex items-center gap-1.5 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-500">
              <Package className="w-3 h-3 flex-shrink-0" />
              L2 上下文压缩已触发 —— 占用骤降源于压缩，并非数据丢失
            </div>
          )}

          {/* 堆叠条形图 */}
          <div className="flex h-3 w-full rounded-full overflow-hidden bg-[hsl(var(--muted))]">
            {totalForRatio > 0 && cats && CATEGORY_DEFS.map(d => {
              const v = cats[d.key]
              if (!v) return null
              return (
                <div
                  key={d.key}
                  className={cn('h-full', d.color)}
                  style={{ width: `${(v / totalForRatio) * 100}%` }}
                  title={`${d.label}: ${formatTokens(v)}`}
                />
              )
            })}
          </div>

          {/* 分类图例 */}
          <div className="space-y-1">
            {CATEGORY_DEFS.map(d => {
              const v = cats ? cats[d.key] : null
              return (
                <div key={d.key} className="flex items-center gap-2 text-xs">
                  <span className={cn('w-2 h-2 rounded-full flex-shrink-0', d.color)} />
                  <span className="text-[hsl(var(--muted-foreground))] flex-1">{d.label}</span>
                  <span className="tabular-nums font-medium">
                    {v == null ? '—' : formatTokens(v)}
                    {totalForRatio > 0 && v != null && (
                      <span className="ml-1.5 text-[hsl(var(--muted-foreground))]">{formatPercent(v / totalForRatio)}</span>
                    )}
                  </span>
                </div>
              )
            })}
          </div>

          {/* system prompt 段级拆解 */}
          {breakdown.segments != null && Object.keys(breakdown.segments).length > 0 && (
            <div className="rounded border border-[hsl(var(--border))] p-2 space-y-1">
              <div className="text-[11px] font-medium text-[hsl(var(--muted-foreground))]">系统提示构成（估算）</div>
              {Object.entries(breakdown.segments).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between text-[11px]">
                  <span className="text-[hsl(var(--muted-foreground))]">{SEGMENT_LABELS[k] ?? k}</span>
                  <span className="tabular-nums">{formatTokens(v)}</span>
                </div>
              ))}
            </div>
          )}

          {/* 会话累计消耗 */}
          <div className="pt-1 border-t border-[hsl(var(--border))]">
            <div className="flex items-center justify-between pt-2">
              <span className="text-sm font-semibold">本次会话消耗</span>
              <span className="text-xs tabular-nums text-[hsl(var(--muted-foreground))]">
                共 {formatTokens(breakdown.usage_totals.total_tokens)} tokens
              </span>
            </div>
            <div className="grid grid-cols-4 gap-x-3 gap-y-1 pt-1.5 text-[11px] tabular-nums">
              <UsageCell label="输入" value={formatTokens(breakdown.usage_totals.input_tokens)} />
              <UsageCell label="输出" value={formatTokens(breakdown.usage_totals.output_tokens)} />
              <UsageCell label="缓存读" value={formatTokens(breakdown.usage_totals.cache_read_tokens)} />
              <UsageCell label="推理" value={formatTokens(breakdown.usage_totals.reasoning_tokens)} />
              <UsageCell label="成本" value={formatCost(breakdown.usage_totals.cost)} />
              <UsageCell label="累计时长" value={formatDuration(breakdown.usage_totals.duration_s)} />
            </div>
          </div>

          {/* 每轮明细 + agent 筛选 */}
          <div className="pt-1 border-t border-[hsl(var(--border))]">
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs font-medium text-[hsl(var(--muted-foreground))]">每轮消耗</span>
              {showFilter && (
                <div className="flex items-center gap-1">
                  <FilterTab active={agentFilter === 'all'} onClick={() => setAgentFilter('all')}>全部</FilterTab>
                  {agentNames.map(n => (
                    <FilterTab key={n} active={agentFilter === n} onClick={() => setAgentFilter(n)}>{n}</FilterTab>
                  ))}
                </div>
              )}
            </div>
            {filteredRuns.length === 0 ? (
              <div className="py-3 text-[11px] text-center text-[hsl(var(--muted-foreground))]">
                暂无消耗数据{showFilter && agentFilter !== 'all' ? '（该 agent 无 run）' : ''}
              </div>
            ) : (
              <div className="pt-1.5 max-h-40 overflow-y-auto">
                <table className="w-full text-[11px] tabular-nums">
                  <thead>
                    <tr className="text-[hsl(var(--muted-foreground))]">
                      <th className="text-left font-normal py-0.5">轮</th>
                      <th className="text-right font-normal py-0.5">输入</th>
                      <th className="text-right font-normal py-0.5">输出</th>
                      <th className="text-right font-normal py-0.5">缓存读</th>
                      <th className="text-right font-normal py-0.5">耗时</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRuns.map((r, i) => (
                      <RunRow key={i} run={r} index={allRuns.indexOf(r) + 1} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="text-[10px] text-[hsl(var(--muted-foreground))] text-right">
            数字为 tokenizer 估算值 · 更新于 {new Date(breakdown.updated_at * 1000).toLocaleTimeString()}
          </div>
        </div>
      )}
    </div>
  )
}

function UsageCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-1">
      <span className="text-[hsl(var(--muted-foreground))]">{label}</span>
      <span>{value}</span>
    </div>
  )
}

function FilterTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors max-w-24 truncate',
        active
          ? 'bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]'
          : 'bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))]',
      )}
    >
      {children}
    </button>
  )
}

function RunRow({ run, index }: { run: RunUsageEntry; index: number }) {
  const u: UsageMetricsPayload = run.usage
  return (
    <tr className="border-t border-[hsl(var(--border))]/50">
      <td className="py-0.5 text-[hsl(var(--muted-foreground))]">#{index}</td>
      <td className="text-right py-0.5">{formatTokens(u.input_tokens)}</td>
      <td className="text-right py-0.5">{formatTokens(u.output_tokens)}</td>
      <td className="text-right py-0.5">{formatTokens(u.cache_read_tokens)}</td>
      <td className="text-right py-0.5">{formatDuration(u.duration_s)}</td>
    </tr>
  )
}
