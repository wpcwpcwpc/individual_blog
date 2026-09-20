/// <reference types="vitest/globals" />
/**
 * ApprovalCard — 刷新回放对账测试（2026-08-28 真机 bug 回归）。
 *
 * 症状：决议状态只存前端内存，刷新后回放的 approval_pending 事件无决议
 * 字段 → 已批准卡片回退「待审批」，点击触发后端 409。修复：挂载时按
 * approval_id 调 getApproval 对账，非 pending 即定格。
 */
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { ApprovalCard } from '@/components/approvals/ApprovalCard'
import type { Approval } from '@/api/approvals'
import type { ApprovalPendingItem } from '@/types/events'

const { getApprovalMock } = vi.hoisted(() => ({ getApprovalMock: vi.fn() }))

vi.mock('@/api/approvals', () => ({
  continueRun: vi.fn(() => () => {}),
  resolveApproval: vi.fn(async () => {
    throw new Error('resolve 不应被本测试触发')
  }),
  getApproval: (id: string) => getApprovalMock(id),
}))

// ── Helpers ────────────────────────────────────────────────────────

function buildApproval(status: Approval['status']): Approval {
  return {
    id: 'ap-1',
    run_id: 'run-1',
    session_id: 'sid-1',
    status,
    approval_type: 'required',
    pause_type: null,
    tool_name: 'request_approval',
    tool_args: null,
    resolved_by: status === 'pending' ? null : 'qa@local',
    resolved_at: status === 'pending' ? null : 1,
    resolution_data: status === 'approved' ? { note: '同意' } : null,
    created_at: 1,
    updated_at: 2,
  }
}

function buildItem(overrides: Partial<ApprovalPendingItem> = {}): ApprovalPendingItem {
  return {
    type: 'approval_pending',
    id: 'evt-1',
    run_id: 'run-1',
    approval_id: 'ap-1',
    tool_name: 'request_approval',
    tool_args: { kind: 'plan', title: '实现方案', content_md: '## 方案' },
    approval_type: 'required',
    resolution: null,
    ...overrides,
  }
}

function renderCard(item: ApprovalPendingItem) {
  return render(<ApprovalCard item={item} sessionId="sid-1" />)
}

// ── 回放对账 ───────────────────────────────────────────────────────

describe('ApprovalCard — 刷新回放对账（后端权威状态定格）', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('回放卡片后端已 approved → 定格「已批准」，不再显示批准按钮', async () => {
    getApprovalMock.mockResolvedValue(buildApproval('approved'))
    renderCard(buildItem())

    await waitFor(() => expect(screen.getByText('已批准')).toBeTruthy())
    expect(screen.queryByRole('button', { name: '批准' })).toBeNull()
    expect(screen.getByText(/同意/)).toBeTruthy()
  })

  it('回放卡片后端已 rejected → 定格「已拒绝」', async () => {
    getApprovalMock.mockResolvedValue(buildApproval('rejected'))
    renderCard(buildItem())

    await waitFor(() => expect(screen.getByText('已拒绝')).toBeTruthy())
    expect(screen.queryByRole('button', { name: '拒绝' })).toBeNull()
  })

  it('对账结果 pending → 保持「待审批」，批准按钮可用', async () => {
    getApprovalMock.mockResolvedValue(buildApproval('pending'))
    renderCard(buildItem())

    await waitFor(() => expect(getApprovalMock).toHaveBeenCalledWith('ap-1'))
    expect(screen.getByText('待审批')).toBeTruthy()
    expect(screen.getByRole('button', { name: '批准' })).toBeTruthy()
  })

  it('对账 expired → 定格不可决议（拒绝态 + 失效注明）', async () => {
    getApprovalMock.mockResolvedValue(buildApproval('expired'))
    renderCard(buildItem())

    await waitFor(() => expect(screen.getByText('已拒绝')).toBeTruthy())
    expect(screen.getByText(/已失效/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '批准' })).toBeNull()
  })

  it('内存已决议（item.resolution 非 null）→ 不再对账', () => {
    renderCard(buildItem({ resolution: { status: 'approved' } }))

    expect(getApprovalMock).not.toHaveBeenCalled()
    expect(screen.getByText('已批准')).toBeTruthy()
  })
})

// ── 替换预览行级配色（inline style） ───────────────────────────────

describe('ApprovalCard — 替换预览行级配色（inline style）', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('替换预览：删除行红 / 新增行绿 inline 配色 + +N/-M 统计徽标', () => {
    renderCard(buildItem({
      tool_name: 'edit_file',
      tool_args: {
        file_path: 'src/mod.py',
        old_string: 'a = 1\nb = 2\n',
        new_string: 'a = 1\nc = 3\n',
        purpose: '修改变量',
      },
    }))

    // 统计徽标：新增 1 行 / 删除 1 行
    expect(screen.getByText('+1 行')).toBeTruthy()
    expect(screen.getByText('-1 行')).toBeTruthy()
    // 删除行（b = 2）：red-50 底 + red-700 字（inline style 防全局覆盖规则改写）
    const delRow = screen.getByText(/b = 2/)
    expect(delRow.style.background).toBe('rgb(254, 242, 242)')
    expect(delRow.style.color).toBe('rgb(185, 28, 28)')
    // 新增行（c = 3）：green-50 底 + green-700 字
    const addRow = screen.getByText(/c = 3/)
    expect(addRow.style.background).toBe('rgb(240, 253, 244)')
    expect(addRow.style.color).toBe('rgb(21, 128, 61)')
    // purpose 上卡（审批信息充分性）
    expect(screen.getByText(/修改变量/)).toBeTruthy()
  })
})
