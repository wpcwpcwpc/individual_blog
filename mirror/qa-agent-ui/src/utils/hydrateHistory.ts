/**
 * hydrateHistory.ts
 *
 * Converts Agno SQLite message history into HistoryEvent[] for the
 * ExecutionTimeline to render — visually identical to live SSE events.
 *
 * History events are a distinct shape from live NormalizedEvent: they carry
 * `id`/`timestamp`, a `user_message` variant, and pre-merged `tool_args`/
 * `tool_result` on tool events (live events split tool_start/tool_end).
 */

import type { HistoryMessage } from '@/types/api'

let _idCounter = 0
const uid = () => `hist_${Date.now()}_${++_idCounter}`

// ── History event types ──────────────────────────────────────────

export interface HistoryBaseEvent {
  id: string
  timestamp: number
  backend_message_id?: string
  agent_name?: string
}

export interface HistoryUserMessageEvent extends HistoryBaseEvent {
  event_type: 'user_message'
  content: string
  username?: string
  workspace_context?: { selected_paths: string[] }
}

export interface HistoryTokenEvent extends HistoryBaseEvent {
  event_type: 'token'
  content: string
}

/** 历史 assistant 消息携带的整段思考快照（区别于 live 的增量 reasoning_delta） */
export interface HistoryReasoningEvent extends HistoryBaseEvent {
  event_type: 'reasoning'
  content: string
}

export interface HistoryToolEndEvent extends HistoryBaseEvent {
  event_type: 'tool_end'
  tool_name: string
  tool_call_id: string
  tool_args: Record<string, unknown>
  tool_result: unknown
  status: string
}

export type HistoryEvent =
  | HistoryUserMessageEvent
  | HistoryTokenEvent
  | HistoryReasoningEvent
  | HistoryToolEndEvent

// ── Hydration ────────────────────────────────────────────────────

export function hydrateMessages(
  messages: HistoryMessage[],
  agentName: string,
): HistoryEvent[] {
  const events: HistoryEvent[] = []

  // Build a map: tool_call_id → index in events array (for tool result merging)
  const toolCallIndexMap = new Map<string, number>()

  for (const msg of messages) {
    switch (msg.role) {
      // ── User message ────────────────────────────────────────────────────
      case 'user': {
        const ev: HistoryUserMessageEvent = {
          id: uid(),
          event_type: 'user_message',
          agent_name: 'user',
          content: msg.content ?? '',
          timestamp: msg.created_at ? msg.created_at * 1000 : Date.now(),
          backend_message_id: msg.id,
        }
        events.push(ev)
        break
      }

      // ── Assistant message ────────────────────────────────────────────────
      case 'assistant': {
        const hasToolCalls = msg.tool_calls && msg.tool_calls.length > 0

        // 思考事件先于正文/工具（时序镜像 live：思考 → 输出/工具）
        if (msg.reasoning_content) {
          const ev: HistoryReasoningEvent = {
            id: uid(),
            event_type: 'reasoning',
            agent_name: msg.name ?? agentName,
            content: msg.reasoning_content,
            timestamp: msg.created_at ? msg.created_at * 1000 : Date.now(),
            backend_message_id: msg.id,
          }
          events.push(ev)
        }

        if (!hasToolCalls) {
          // Pure text reply → single merged token bubble
          if (msg.content) {
            const ev: HistoryTokenEvent = {
              id: uid(),
              event_type: 'token',
              agent_name: msg.name ?? agentName,
              content: msg.content,
              timestamp: msg.created_at ? msg.created_at * 1000 : Date.now(),
              backend_message_id: msg.id,
            }
            events.push(ev)
          }
        } else {
          // Has tool_calls → emit tool_end events (result filled below)
          for (const tc of msg.tool_calls!) {
            // Skip incomplete entries (e.g. Agno internal task-tracking stubs that have only an id)
            if (!tc.function?.name) continue

            let toolArgs: Record<string, unknown> = {}
            try { toolArgs = JSON.parse(tc.function.arguments) } catch { /* ignore */ }

            const toolEvent: HistoryToolEndEvent = {
              id: uid(),
              event_type: 'tool_end',
              agent_name: msg.name ?? agentName,
              tool_name: tc.function.name,
              tool_call_id: tc.id,
              tool_args: toolArgs,
              tool_result: null,
              status: 'done',
              timestamp: msg.created_at ? msg.created_at * 1000 : Date.now(),
              backend_message_id: msg.id,
            }

            toolCallIndexMap.set(tc.id, events.length)
            events.push(toolEvent)
          }

          // Also emit text content if present alongside tool calls
          if (msg.content) {
            const ev: HistoryTokenEvent = {
              id: uid(),
              event_type: 'token',
              agent_name: msg.name ?? agentName,
              content: msg.content,
              timestamp: msg.created_at ? msg.created_at * 1000 : Date.now(),
              backend_message_id: msg.id,
            }
            events.push(ev)
          }
        }
        break
      }

      // ── Tool result ──────────────────────────────────────────────────────
      case 'tool': {
        if (msg.tool_call_id && toolCallIndexMap.has(msg.tool_call_id)) {
          const idx = toolCallIndexMap.get(msg.tool_call_id)!
          // Merge result into existing tool_end event
          const existing = events[idx] as HistoryToolEndEvent
          existing.tool_result = msg.content
        }
        break
      }

      // system messages are ignored in timeline
      default:
        break
    }
  }

  return events
}
