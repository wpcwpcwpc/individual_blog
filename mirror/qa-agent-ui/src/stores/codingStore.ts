/**
 * 编码工作台全局状态（agent 无关的审批待办）。
 *
 * Zustand store：pending 审批 + 已决议历史 + 拉取态。selector 订阅（U3），
 * actions 与数据同 store。审批卡的决议定格由卡片自身维护，这里只管待办清单
 * ——「断连期间挂起」恢复时按 approval_id 去重回灌 timeline。
 */
import { create } from 'zustand'
import { type Approval, listApprovals } from '@/api/approvals'
import { apiErrorMessage } from '@/utils/apiError'

interface CodingState {
  pendingApprovals: Approval[]
  approvalHistory: Approval[]
  approvalsLoading: boolean
  approvalsError: string | null

  refreshApprovals: (sessionId?: string) => Promise<void>
  markResolved: (approvalId: string, status: Approval['status']) => void
}

export const useCodingStore = create<CodingState>((set, get) => ({
  pendingApprovals: [],
  approvalHistory: [],
  approvalsLoading: false,
  approvalsError: null,

  refreshApprovals: async (sessionId?: string) => {
    set({ approvalsLoading: true, approvalsError: null })
    try {
      const all = await listApprovals({ session_id: sessionId })
      set({
        pendingApprovals: all.filter((a) => a.status === 'pending'),
        approvalHistory: all.filter((a) => a.status !== 'pending'),
        approvalsLoading: false,
      })
    } catch (err) {
      set({ approvalsLoading: false, approvalsError: apiErrorMessage(err) })
    }
  },

  markResolved: (approvalId, status) => {
    const { pendingApprovals, approvalHistory } = get()
    const target = pendingApprovals.find((a) => a.id === approvalId)
    if (!target) return
    set({
      pendingApprovals: pendingApprovals.filter((a) => a.id !== approvalId),
      approvalHistory: [{ ...target, status }, ...approvalHistory],
    })
  },
}))

// ── 常用 selector（U3：禁止整 store 订阅） ──────────────────────────

export const selectPendingCount = (s: CodingState) => s.pendingApprovals.length
