import apiClient from './client'

export interface Trace {
  trace_id: string
  name: string
  status: string
  start_time: string
  end_time: string
  duration_ms: number
  total_spans: number
  error_count: number
  run_id?: string
  session_id?: string
  user_id?: string
  agent_id?: string
  team_id?: string
  workflow_id?: string
  agent_type?: string
  token_prompt?: number
  token_completion?: number
  token_total?: number
  /** 缓存命中 token（含于 token_total 内）；历史 trace 无此字段为 undefined */
  token_cache_read?: number
  created_at: string
}

export interface Span {
  span_id: string
  trace_id: string
  parent_span_id?: string
  name: string
  span_kind: string
  status_code: string
  status_message?: string
  start_time: string
  end_time: string
  duration_ms: number
  attributes: Record<string, unknown>
  created_at: string
}

export interface TraceListResponse {
  traces: Trace[]
  total: number
  page: number
  limit: number
}

export interface TraceSpansResponse {
  spans: Span[]
  trace_id: string
}

export interface ListTracesParams {
  user_id?: string
  agent_id?: string
  workflow_id?: string
  agent_type?: string
  session_id?: string
  status?: string
  start_time?: string
  end_time?: string
  limit?: number
  page?: number
}

export async function listTraces(params: ListTracesParams = {}): Promise<TraceListResponse> {
  const res = await apiClient.get<TraceListResponse>('/traces', { params })
  return res.data
}

export async function getTraceSpans(traceId: string): Promise<TraceSpansResponse> {
  const res = await apiClient.get<TraceSpansResponse>(`/traces/${traceId}/spans`)
  return res.data
}

export interface DailyTokenEntry {
  day: string
  token_total: number
  /** 缓存命中 token；历史日期聚合为 0 */
  token_cache_read?: number
  trace_count: number
}

export interface DailyTokensResponse {
  days: DailyTokenEntry[]
  days_count: number
}

export interface DailyTokensParams {
  days?: number
  user_id?: string
  agent_type?: string
  status?: string
}

export async function listDailyTokens(params: DailyTokensParams = {}): Promise<DailyTokensResponse> {
  const res = await apiClient.get<DailyTokensResponse>('/traces/daily-tokens', { params })
  return res.data
}
