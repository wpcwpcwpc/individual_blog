/// <reference types="vitest/globals" />
/**
 * ClarifyCard — 澄清卡核心行为测试。
 *
 * 覆盖「已答定格与刷新回放」与「用户作答回传续跑」的前端侧：
 * 1. 已答回放（item.answer 非 null）→ 定格只读，无提交入口
 * 2. 未答回放（feedback 事件）→ schema 重建渲染选项，可作答
 * 3. 提交载荷拼装（feedback selections / form values 类型强转）
 * 4. 回放卡 run_id 缺失 → fetchPendingClarification 自愈后可提交
 */
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import ClarifyCard from '@/components/approvals/ClarifyCard'
import type { ClarifyCardItem } from '@/types/events'

const { continueRunMock, fetchPendingMock } = vi.hoisted(() => ({
  continueRunMock: vi.fn<(...args: unknown[]) => () => void>(() => () => {}),
  fetchPendingMock: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => null),
}))

vi.mock('@/api/approvals', () => ({
  continueRun: (...args: unknown[]) => continueRunMock(...args),
  fetchPendingClarification: (...args: unknown[]) => fetchPendingMock(...args),
}))

// ── Helpers ────────────────────────────────────────────────────────

function buildFeedbackItem(overrides: Partial<ClarifyCardItem> = {}): ClarifyCardItem {
  return {
    type: 'clarify_request',
    id: 'evt-1',
    run_id: 'run-1',
    tool_call_id: 'call_ask_1',
    tool_name: 'ask_user',
    kind: 'feedback',
    questions: [{
      question: '落盘策略是覆盖还是另存？',
      header: '落盘策略',
      multi_select: false,
      options: [
        { label: '覆盖写', description: '覆盖原文件' },
        { label: '另存新档', description: null },
      ],
    }],
    fields: [],
    tool_args: null,
    answer: null,
    ...overrides,
  }
}

function buildFormItem(overrides: Partial<ClarifyCardItem> = {}): ClarifyCardItem {
  return {
    type: 'clarify_request',
    id: 'evt-2',
    run_id: 'run-1',
    tool_call_id: 'call_form_1',
    tool_name: 'get_user_input',
    kind: 'form',
    questions: [],
    fields: [
      { name: 'case_name', field_type: 'str', description: 'Case 类名' },
      { name: 'retry_count', field_type: 'int', description: null },
    ],
    tool_args: null,
    answer: null,
    ...overrides,
  }
}

// ── 已答定格 ───────────────────────────────────────────────────────

describe('ClarifyCard — 已答回放定格', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('answer 非 null → 显示「已作答」摘要，无提交入口，且不自愈拉取', () => {
    render(<ClarifyCard
      item={buildFeedbackItem({
        answer: { selections: { '落盘策略是覆盖还是另存？': ['覆盖写'] } },
      })}
      sessionId="sid-1"
    />)

    expect(screen.getByText('已作答')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /提交并继续/ })).toBeNull()
    expect(screen.getByText('落盘策略是覆盖还是另存？：覆盖写')).toBeTruthy()
    expect(fetchPendingMock).not.toHaveBeenCalled()
  })
})

// ── 未答回放 + 提交载荷 ────────────────────────────────────────────

describe('ClarifyCard — 未答回放与提交载荷拼装', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('feedback：渲染选项 → 单选 → 提交携带 selections', async () => {
    render(<ClarifyCard item={buildFeedbackItem()} sessionId="sid-1" />)

    expect(screen.getByText('待回答')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '覆盖写' }))
    fireEvent.click(screen.getByRole('button', { name: /提交并继续/ }))

    await waitFor(() => expect(continueRunMock).toHaveBeenCalled())
    const [, , , , , clarifications] = continueRunMock.mock.calls[0] as unknown[]
    expect(clarifications).toEqual([{
      tool_call_id: 'call_ask_1',
      selections: { '落盘策略是覆盖还是另存？': ['覆盖写'] },
    }])
  })

  it('feedback 漏选 → 前端拦截提交（continueRun 不被调用）', () => {
    render(<ClarifyCard item={buildFeedbackItem()} sessionId="sid-1" />)

    fireEvent.click(screen.getByRole('button', { name: /提交并继续/ }))
    expect(screen.getByText(/所有问题\/字段都必须填写/)).toBeTruthy()
    expect(continueRunMock).not.toHaveBeenCalled()
  })

  it('form：全字段必填 + int 强转 → 提交携带强转后 values', async () => {
    render(<ClarifyCard item={buildFormItem()} sessionId="sid-1" />)

    // case_name 两个 input（str 文本框 + int 数字框）
    const inputs = screen.getAllByRole('textbox') // str 输入框（int 是 number role 为 spinbutton）
    fireEvent.change(inputs[0], { target: { value: 'CaseFishing' } })
    const spin = screen.getByRole('spinbutton')
    fireEvent.change(spin, { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: /提交并继续/ }))

    await waitFor(() => expect(continueRunMock).toHaveBeenCalled())
    const [, , , , , clarifications] = continueRunMock.mock.calls[0] as unknown[]
    expect(clarifications).toEqual([{
      tool_call_id: 'call_form_1',
      values: { case_name: 'CaseFishing', retry_count: 3 },
    }])
  })

  it('form 漏填 → 前端拦截提交', () => {
    render(<ClarifyCard item={buildFormItem()} sessionId="sid-1" />)

    fireEvent.click(screen.getByRole('button', { name: /提交并继续/ }))
    expect(screen.getByText(/所有问题\/字段都必须填写/)).toBeTruthy()
    expect(continueRunMock).not.toHaveBeenCalled()
  })
})

// ── run_id 自愈 ────────────────────────────────────────────────────

describe('ClarifyCard — 回放卡 run_id 自愈', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('run_id 缺失 + 端点命中同 tool_call_id → 自愈后可提交且携带 run_id', async () => {
    fetchPendingMock.mockResolvedValue({
      run_id: 'run-9',
      tool_call_id: 'call_ask_1',
      tool_name: 'ask_user',
      kind: 'feedback' as const,
      questions: [],
      fields: [],
      tool_args: null,
    })
    render(<ClarifyCard item={buildFeedbackItem({ run_id: '' })} sessionId="sid-1" />)

    await waitFor(() => expect(fetchPendingMock).toHaveBeenCalledWith('sid-1'))
    fireEvent.click(screen.getByRole('button', { name: '覆盖写' }))
    fireEvent.click(screen.getByRole('button', { name: /提交并继续/ }))

    await waitFor(() => expect(continueRunMock).toHaveBeenCalled())
    const [sessionId, runId] = continueRunMock.mock.calls[0] as unknown[]
    expect(sessionId).toBe('sid-1')
    expect(runId).toBe('run-9')
  })

  it('run_id 缺失 + 端点未命中 → 提交保持禁用', async () => {
    fetchPendingMock.mockResolvedValue(null)
    render(<ClarifyCard item={buildFeedbackItem({ run_id: '' })} sessionId="sid-1" />)

    await waitFor(() => expect(fetchPendingMock).toHaveBeenCalled())
    const btn = screen.getByRole('button', { name: /提交并继续/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })
})
