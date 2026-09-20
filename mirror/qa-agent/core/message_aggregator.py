"""
QA Agent System — Message Aggregator

Aggregates persisted event-stream data (from ``session_events``) back into
a standard message list compatible with ``storage_reader._normalise_message``
output format.

This module is the **read-time complement** to PersistentEventStore's
write-time persistence.  It is called by ``storage_reader.read_session_messages``
when a session is in RUNNING / ABORTED / FAILED state to recover the
in-progress run's messages that Agno has not yet persisted.

The aggregation is a pure function: events in → messages out, no side effects.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def aggregate_events_to_messages(events: List[dict]) -> List[Dict[str, Any]]:
    """Aggregate a list of event dicts into a normalised message list.

    The output format matches ``storage_reader._normalise_message``:
    ``{role, content, created_at, tool_calls, tool_call_id, name}``

    Event types handled:
      - ``user_message`` → ``{role: "user", content: ...}``
      - ``token``        → accumulated into assistant message content
      - ``tool_start``   → flushes accumulated tokens as an assistant message
                           with ``tool_calls`` metadata
      - ``tool_end``     → ``{role: "tool", content: ..., tool_call_id: ...}``
      - ``tool_error``   → ``{role: "tool", content: "Error: ...", tool_call_id: ...}``
      - ``run_complete`` → flushes remaining accumulated tokens
      - ``run_error``    → flushes remaining tokens (may be incomplete)
      - ``run_aborted``  → flushes remaining tokens (may be incomplete)
      - ``run_started``  → ignored (no message produced)

    Args:
        events: List of event dicts, sorted by seq_id ascending.

    Returns:
        List of normalised message dicts, suitable for ``MessageRecord(**msg)``.
    """
    messages: List[Dict[str, Any]] = []

    # Accumulator for in-progress assistant text
    _token_buf: List[str] = []
    _token_agent_name: str = ""
    _token_first_ts: float = 0.0

    # Pending tool_calls to attach to the next flushed assistant message
    _pending_tool_calls: List[Dict[str, Any]] = []

    def _flush_tokens(force_tool_calls: Optional[List[Dict[str, Any]]] = None) -> None:
        """Flush accumulated tokens into an assistant message."""
        nonlocal _token_buf, _token_agent_name, _token_first_ts, _pending_tool_calls

        content = "".join(_token_buf) if _token_buf else None
        tool_calls = force_tool_calls or (_pending_tool_calls if _pending_tool_calls else None)

        # Only emit a message if there's content or tool_calls
        if content or tool_calls:
            msg: Dict[str, Any] = {
                "role": "assistant",
                "content": content,
                "created_at": _token_first_ts,
                "tool_calls": tool_calls,
                "tool_call_id": None,
                "name": _token_agent_name or None,
            }
            messages.append(msg)

        _token_buf = []
        _token_first_ts = 0.0
        _pending_tool_calls = []

    for event in events:
        event_type = event.get("event_type", "")
        agent_name = event.get("agent_name", "")
        created_at = event.get("created_at", 0.0)
        if isinstance(created_at, str):
            try:
                created_at = float(created_at)
            except (ValueError, TypeError):
                created_at = 0.0

        # ── user_message ──────────────────────────────────────────
        if event_type == "user_message":
            # Flush any pending tokens first (shouldn't happen, but defensive)
            _flush_tokens()
            messages.append({
                "role": "user",
                "content": event.get("content", ""),
                "created_at": created_at or time.time(),
                "tool_calls": None,
                "tool_call_id": None,
                "name": None,
            })

        # ── token ─────────────────────────────────────────────────
        elif event_type == "token":
            content = event.get("content", "")
            if content:
                if not _token_buf:
                    _token_first_ts = created_at or time.time()
                    _token_agent_name = agent_name
                _token_buf.append(content)

        # ── tool_start ────────────────────────────────────────────
        elif event_type == "tool_start":
            # Record the tool call metadata; flush tokens when we see tool_end
            tool_call_info = {
                "id": event.get("tool_call_id", ""),
                "type": "function",
                "function": {
                    "name": event.get("tool_name", ""),
                    "arguments": str(event.get("inputs", {})),
                },
            }
            _pending_tool_calls.append(tool_call_info)

            # Flush accumulated tokens as an assistant message with tool_calls
            # The assistant message that precedes a tool call should have
            # the tool_calls attached to it.
            _flush_tokens(force_tool_calls=[tool_call_info])

        # ── tool_end ──────────────────────────────────────────────
        elif event_type == "tool_end":
            tool_result = event.get("outputs", "")
            if not isinstance(tool_result, str):
                try:
                    import json
                    tool_result = json.dumps(tool_result, ensure_ascii=False, default=str)
                except Exception:
                    tool_result = str(tool_result)

            messages.append({
                "role": "tool",
                "content": tool_result,
                "created_at": created_at or time.time(),
                "tool_calls": None,
                "tool_call_id": event.get("tool_call_id", ""),
                "name": event.get("tool_name", ""),
            })

        # ── tool_error ────────────────────────────────────────────
        elif event_type == "tool_error":
            error_msg = event.get("error", "unknown error")
            messages.append({
                "role": "tool",
                "content": f"Error: {error_msg}",
                "created_at": created_at or time.time(),
                "tool_calls": None,
                "tool_call_id": event.get("tool_call_id", ""),
                "name": event.get("tool_name", ""),
            })

        # ── run_complete / run_error / run_aborted ────────────────
        elif event_type in ("run_complete", "run_error", "run_aborted"):
            _flush_tokens()

        # ── run_started and others ────────────────────────────────
        # Ignored — no message produced

    # If there are remaining tokens (incomplete run, no terminal event),
    # flush them as a potentially incomplete assistant message.
    _flush_tokens()

    return messages
