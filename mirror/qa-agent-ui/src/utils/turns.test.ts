/// <reference types="vitest/globals" />
import { planTurnRows, segmentTurns } from '@/utils/turns'
import type { TimelineItem, UserMessageItem, AgentMessageItem } from '@/types/events'

function u(i: number): UserMessageItem {
  return { type: 'user_message', id: `u${i}`, content: `q${i}`, username: 'me', timestamp: i }
}

function a(i: number): AgentMessageItem {
  return { type: 'agent_message', id: `a${i}`, agent_name: 'QAAgent', content: `r${i}`, isStreaming: false }
}

function row(i: number): TimelineItem {
  return { type: 'run_started', id: `rs${i}`, agent_name: 'QAAgent' }
}

describe('segmentTurns — 按 user 卡切轮（D1）', () => {
  it('两轮完整切分：U 到下一张 U 之前', () => {
    const tl = [u(0), a(0), u(1), a(1)]
    const segs = segmentTurns(tl)
    expect(segs).toHaveLength(2)
    expect(segs[0]).toMatchObject({ userItemId: 'u0', userIdx: 0, endIdxInclusive: 1, complete: true })
    expect(segs[1]).toMatchObject({ userItemId: 'u1', userIdx: 2, endIdxInclusive: 3, complete: true })
  })

  it('头部残缺轮（窗口起点截断 U）complete=false', () => {
    const tl = [a(0), row(0), u(1), a(1)]
    const segs = segmentTurns(tl)
    expect(segs).toHaveLength(2)
    expect(segs[0]).toMatchObject({ userItemId: null, userIdx: -1, complete: false })
    expect(segs[0].endIdxInclusive).toBe(1)
    expect(segs[1]).toMatchObject({ userItemId: 'u1', complete: true })
  })

  it('空轮体（U 即轮尾）endIdxInclusive === userIdx', () => {
    const tl = [u(0), u(1)]
    const segs = segmentTurns(tl)
    expect(segs).toHaveLength(2)
    expect(segs[0]).toMatchObject({ userIdx: 0, endIdxInclusive: 0 })
  })

  it('空 timeline → 无轮', () => {
    expect(segmentTurns([])).toEqual([])
  })
})

describe('planTurnRows — 渲染位导出（D2/D5 残缺轮无入口）', () => {
  it('每轮 U 下方 + 轮尾各一位；空轮体去重', () => {
    const tl = [u(0), a(0), u(1), a(1), u(2)]
    const plan = planTurnRows(tl)
    // U 下方：三张 U 卡
    expect([...plan.userRowAfterIdx]).toEqual([0, 2, 4])
    // 轮尾：turn0 尾=a0(1)→u0，turn1 尾=a1(3)→u1；turn2 空（U 即尾，与 U 位去重）
    expect([...plan.tailRowAfterIdx.entries()]).toEqual([[1, 'u0'], [3, 'u1']])
  })

  it('残缺轮（窗口头）不产出任何渲染位', () => {
    const tl = [a(0), row(0), u(1), a(1)]
    const plan = planTurnRows(tl)
    expect([...plan.userRowAfterIdx]).toEqual([2])
    expect([...plan.tailRowAfterIdx.keys()]).toEqual([3])
  })
})
