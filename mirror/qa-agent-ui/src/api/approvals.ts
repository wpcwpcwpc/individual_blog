/**
 * 审批 / 澄清 API 域（agent 无关）。
 *
 * 覆盖：审批卡（list/get/resolve）+ resolve 后续跑 SSE + 会话待答澄清自愈源。
 * 后端路由见 api/approvals_routes.py —— 任何挂阻塞审批工具的 Agent 共用同一组 URL。
 */
import apiClient from './client'

// ── Approval（审批卡） ───────────────────────────────────────────────

/** 审批记录（agno approvals 表形态，对齐后端 ApprovalResponse） */
export interface Approval {
  id: string
  run_id: string | null
  session_id: string | null
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled'
  approval_type: string | null
  pause_type: string | null
  tool_name: string | null
  /** 审批载荷：request_approval → {kind,title,content_md}；写盘/命令 → 工具参数 */
  tool_args: Record<string, unknown> | null
  resolved_by: string | null
  resolved_at: number | null
  resolution_data: { note?: string } | null
  created_at: number | null
  updated_at: number | null
}

export async function listApprovals(params: {
  status?: string
  session_id?: string
  run_id?: string
  limit?: number
  page?: number
}): Promise<Approval[]> {
  const { data } = await apiClient.get<Approval[]>('/approvals', { params })
  return data
}

export async function getApproval(approvalId: string): Promise<Approval> {
  const { data } = await apiClient.get<Approval>(`/approvals/${encodeURIComponent(approvalId)}`)
  return data
}

export async function resolveApproval(
  approvalId: string,
  status: 'approved' | 'rejected',
  note = '',
): Promise<Approval> {
  const { data } = await apiClient.post<Approval>(
    `/approvals/${encodeURIComponent(approvalId)}/resolve`,
    { status, note },
  )
  return data
}

// ── continue 续跑（POST SSE） ────────────────────────────────────────

/** 单条澄清作答载荷（对齐后端 ClarificationAnswer） */
export interface ClarificationAnswerPayload {
  tool_call_id: string
  /** form（get_user_input）：字段名 → 值（全字段必填） */
  values?: Record<string, unknown>
  /** feedback（ask_user）：question 原文 → 选中 label 列表 */
  selections?: Record<string, string[]>
}

/** 续跑 SSE 事件（agno RunOutputEvent 形态，字段按需透传） */
export interface ContinueSseEvent {
  event: string
  run_id?: string
  session_id?: string
  content?: unknown
  tool?: Record<string, unknown> & { tool_name?: string; approval_id?: string }
  [key: string]: unknown
}

/**
 * 审批 resolve / 澄清作答后续跑（POST SSE 流）。
 * 帧形态 `event: <name>\ndata: <json>\n\n`（format_sse_event）。
 * 返回 cleanup（abort）函数；流结束（done=true）后 onClose 触发。
 * clarifications 非空时随 body 携带澄清作答（后端 requirements 注入路径）。
 */
export function continueRun(
  sessionId: string,
  runId: string,
  onEvent: (ev: ContinueSseEvent) => void,
  onError?: (msg: string) => void,
  onClose?: () => void,
  clarifications?: ClarificationAnswerPayload[],
): () => void {
  const controller = new AbortController()
  const base = (apiClient.defaults.baseURL || '').replace(/\/$/, '')
  const body = clarifications && clarifications.length > 0
    ? JSON.stringify({ clarifications })
    : undefined
  void fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/continue`, {
    method: 'POST',
    credentials: 'include',
    signal: controller.signal,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body } : {}),
  })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) {
        const detail = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }))
        onError?.(String((detail as { detail?: string }).detail || `HTTP ${resp.status}`))
        onClose?.()
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        // 按 SSE 帧（\n\n）切分
        let idx: number
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          const evName = /^event:\s*(.+)$/m.exec(frame)?.[1]?.trim()
          const dataRaw = /^data:\s*([\s\S]+)$/m.exec(frame)?.[1]?.trim()
          if (!evName || !dataRaw) continue
          try {
            onEvent({ ...JSON.parse(dataRaw), event: evName })
          } catch {
            /* ignore malformed frame */
          }
        }
      }
      onClose?.()
    })
    .catch((err: unknown) => {
      if ((err as { name?: string })?.name === 'AbortError') return
      onError?.(String(err))
      onClose?.()
    })
  return () => controller.abort()
}

// ── pending clarification（澄清卡自愈源） ────────────────────────────

/** 会话当前待答澄清（GET pending-clarification；null = 无待答） */
export interface PendingClarification {
  run_id: string | null
  tool_call_id: string | null
  tool_name: string | null
  kind: 'feedback' | 'form'
  questions: Array<{
    question: string
    header?: string | null
    multi_select: boolean
    options: Array<{ label: string; description?: string | null }>
  }>
  fields: Array<{ name: string; field_type: string; description?: string | null }>
  tool_args: Record<string, unknown> | null
}

/** 澄清无落库记录，刷新回放 run_id 缺失时由此端点自愈对齐（模式同 refreshApprovals） */
export async function fetchPendingClarification(sessionId: string): Promise<PendingClarification | null> {
  const { data } = await apiClient.get<PendingClarification | null>(
    `/sessions/${encodeURIComponent(sessionId)}/pending-clarification`,
  )
  return data
}
