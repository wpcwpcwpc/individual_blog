/// <reference types="vitest/globals" />
/**
 * ThinkingBlock — 思考折叠块交互测试（add-thinking-stream-display design D4）。
 *
 * 覆盖状态机：流式展开渐进渲染 / 思考结束（isStreaming=false）自动收起 /
 * 用户手动收起后增量静默追加不强制展开 / settled 后点击标题重展开回看。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ThinkingBlock } from '@/components/timeline/ThinkingBlock'
import type { AgentThinkingItem } from '@/types/events'

// ── Helpers ────────────────────────────────────────────────────────

function buildItem(overrides: Partial<AgentThinkingItem> = {}): AgentThinkingItem {
  return {
    type: 'agent_thinking',
    id: 'think-1',
    agent_name: 'CodingAgent',
    content: '用户在问一个配置表的问题',
    isStreaming: true,
    startedAt: 1_000,
    endedAt: null,
    ...overrides,
  }
}

// ── 状态机 ─────────────────────────────────────────────────────────

describe('ThinkingBlock — 思考折叠块状态机', () => {
  afterEach(() => {
    cleanup()
  })

  it('流式中默认展开，内容渐进可见，摘要为「思考中」', () => {
    render(<ThinkingBlock item={buildItem()} />)
    expect(screen.getByText('用户在问一个配置表的问题')).toBeTruthy()
    expect(screen.getByText(/思考中/)).toBeTruthy()
  })

  it('流式内容追加（rerender）→ 新增量渐进渲染', () => {
    const { rerender } = render(<ThinkingBlock item={buildItem()} />)
    rerender(
      <ThinkingBlock
        item={buildItem({ content: '用户在问一个配置表的问题，需要先查 rag_query' })}
      />,
    )
    expect(screen.getByText(/需要先查 rag_query/)).toBeTruthy()
  })

  it('思考结束（isStreaming=false）→ 自动收起，摘要切换「已深度思考」', () => {
    const { rerender } = render(<ThinkingBlock item={buildItem()} />)
    rerender(
      <ThinkingBlock
        item={buildItem({ isStreaming: false, endedAt: 4_000 })}
      />,
    )
    expect(screen.queryByText('用户在问一个配置表的问题')).toBeNull()
    expect(screen.getByText(/已深度思考/)).toBeTruthy()
  })

  it('流式期间用户手动收起 → 后续增量静默追加，不强制展开', () => {
    const { rerender } = render(<ThinkingBlock item={buildItem()} />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.queryByText('用户在问一个配置表的问题')).toBeNull()

    rerender(
      <ThinkingBlock
        item={buildItem({ content: '用户在问一个配置表的问题，追加的思考增量' })}
      />,
    )
    expect(screen.queryByText(/追加的思考增量/)).toBeNull()
    expect(screen.getByText(/思考中/)).toBeTruthy()
  })

  it('settled 后点击标题 → 重新展开回看全文', () => {
    const { rerender } = render(<ThinkingBlock item={buildItem()} />)
    rerender(
      <ThinkingBlock
        item={buildItem({ isStreaming: false, endedAt: 4_000 })}
      />,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('用户在问一个配置表的问题')).toBeTruthy()
    expect(screen.getByText(/已深度思考/)).toBeTruthy()
  })

  it('settled 后用户展开 → 保持展开（不受后续 rerender 影响）', () => {
    const { rerender } = render(<ThinkingBlock item={buildItem()} />)
    rerender(
      <ThinkingBlock
        item={buildItem({ isStreaming: false, endedAt: 4_000 })}
      />,
    )
    fireEvent.click(screen.getByRole('button'))
    rerender(
      <ThinkingBlock
        item={buildItem({ isStreaming: false, endedAt: 4_500 })}
      />,
    )
    expect(screen.getByText('用户在问一个配置表的问题')).toBeTruthy()
  })
})

// ── 摘要行时长降级（add-thinking-history-hydrate design D4） ─────────

describe('ThinkingBlock — 摘要行时长降级', () => {
  afterEach(() => {
    cleanup()
  })

  it('零时长（历史重建，endedAt === startedAt）→ 摘要仅展示字数、无时长段', () => {
    render(
      <ThinkingBlock
        item={buildItem({ isStreaming: false, endedAt: 1_000, content: '十' })}
      />,
    )
    expect(screen.getByText('已深度思考 · 1 字')).toBeTruthy()
    expect(screen.queryByText(/秒/)).toBeNull()
  })

  it('live 时长正常展示（endedAt - startedAt > 0）', () => {
    render(
      <ThinkingBlock
        item={buildItem({ isStreaming: false, endedAt: 4_000 })}
      />,
    )
    expect(screen.getByText(/已深度思考 · 3 秒 · 12 字/)).toBeTruthy()
  })
})
