/**
 * Token 数字格式化（add-context-usage-visibility）。
 *
 * 46900 → "46.9k"；1230000 → "1.2M"；950 → "950"。
 * null/undefined 透传（调用方渲染 "—"）。
 */

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1000) return String(n)
  if (n < 1_000_000) {
    const v = n / 1000
    return `${v >= 100 ? Math.round(v) : v.toFixed(1).replace(/\.0$/, '')}k`
  }
  const m = n / 1_000_000
  return `${m >= 100 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, '')}M`
}

/** 占比百分数：0.0469 → "4.7%"（0 显示 "0%"）。 */
export function formatPercent(ratio: number | null | undefined): string {
  if (ratio == null) return '—'
  const pct = ratio * 100
  if (pct === 0) return '0%'
  return `${pct < 10 ? pct.toFixed(1).replace(/\.0$/, '') : Math.round(pct)}%`
}

/** cost 值：null → "—"；0.0123 → "$0.0123"（最多 4 位小数，尾零不裁）。 */
export function formatCost(cost: number | null | undefined): string {
  if (cost == null) return '—'
  if (cost === 0) return '$0'
  return `$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)}`
}

/** 时长秒：null → "—"；2.5 → "2.5s"；95 → "1m35s"。 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Number(seconds.toFixed(1))}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m${s}s`
}
