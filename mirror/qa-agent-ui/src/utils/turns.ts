import type { TimelineItem } from '@/types/events'

/**
 * 轮次切分（add-turn-regenerate D1）：按 user_message 卡片切分，不按 run 标记
 * ——hydrate 路径不产 run_started/run_complete，刷新/翻页后 timeline 无任何轮
 * 标记；user 卡是唯一稳定的轮起点（纯前端可算、跨刷新/翻页成立）。
 *
 * 一轮 = 第 N 张用户卡片到第 N+1 张用户卡片之前（含 thinking / tool_call /
 * agent_message / run 标记 / 终态行）。窗口是 storage 后缀：头部截断的段是
 * 残缺轮（U 在未加载页），不渲染任何重答入口。
 */

export interface TurnSegment {
  /** 该轮 U 卡的 timeline item id；头部残缺轮为 null（U 不在窗口内） */
  userItemId: string | null
  /** U 卡下标；残缺轮为 -1 */
  userIdx: number
  /** 轮尾下标（含）；随遍历推进，最终值为该轮最后一张卡 */
  endIdxInclusive: number
  /** 窗口内完整（有 U 卡）；残缺轮 MUST NOT 渲染重答入口 */
  complete: boolean
}

export function segmentTurns(timeline: TimelineItem[]): TurnSegment[] {
  const segments: TurnSegment[] = []
  let current: TurnSegment | null = null
  for (let i = 0; i < timeline.length; i++) {
    if (timeline[i].type === 'user_message') {
      if (current) current.endIdxInclusive = i - 1
      current = { userItemId: timeline[i].id, userIdx: i, endIdxInclusive: i, complete: true }
      segments.push(current)
    } else if (!current) {
      current = { userItemId: null, userIdx: -1, endIdxInclusive: i, complete: false }
      segments.push(current)
    } else {
      current.endIdxInclusive = i
    }
  }
  return segments
}

export interface TurnRowPlan {
  /** user 卡下标集合：每张 U 卡正下方渲染一个重答入口 */
  userRowAfterIdx: Set<number>
  /** 轮尾下标 → 该轮 U 卡 item id：每轮末卡下方渲染一个重答入口（与 U 卡同位时去重） */
  tailRowAfterIdx: Map<number, string>
}

/**
 * 由切分结果导出渲染位：U 卡下方 + 轮尾各一个入口（D2），同一动作（D2
 * 「不做语义分叉」）。轮体为空（U 即轮尾）时只渲染 U 下方一个，避免同位双行。
 * 残缺轮（窗口头截断）不产出任何位（D5 Risks：U 不在窗口、无 id 可锚）。
 */
export function planTurnRows(timeline: TimelineItem[]): TurnRowPlan {
  const userRowAfterIdx = new Set<number>()
  const tailRowAfterIdx = new Map<number, string>()
  for (const seg of segmentTurns(timeline)) {
    if (!seg.complete || seg.userIdx < 0 || !seg.userItemId) continue
    userRowAfterIdx.add(seg.userIdx)
    if (seg.endIdxInclusive > seg.userIdx) {
      tailRowAfterIdx.set(seg.endIdxInclusive, seg.userItemId)
    }
  }
  return { userRowAfterIdx, tailRowAfterIdx }
}
