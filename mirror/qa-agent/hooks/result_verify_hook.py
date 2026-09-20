"""
QA Agent System — Result Verify Hook (L3 Run post_hook)

After a non-Coordinator Agent completes its run, pauses execution
and requests human pass/fail verification of the results.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def result_verify_post_hook(agent: Any, run_output: Any) -> None:
    """Run post_hook: request L3 human verification of Agent results.

    Only triggered when:
    - agent_type != "coordinator" (coordinators have their own Team post_hook)
    - The Agent run produced a meaningful response (not empty)

    Args:
        agent: The Agno Agent instance.
        run_output: The RunOutput from the completed run (Agno hook convention).
    """
    from hooks.interrupt_manager import InterruptType, interrupt_manager

    state = getattr(agent, "session_state", None)
    if state is None:
        return

    agent_type = getattr(state, "agent_type", "normal")
    session_id = getattr(state, "session_id", "")

    # Coordinators handle their own L3 verification via Team post_hook
    if agent_type == "coordinator":
        return

    if not session_id:
        logger.warning("[Hook] result_verify: no session_id, skipping L3 verification")
        return

    # Extract result summary from run_output
    result_summary = _extract_result_summary(run_output)
    if not result_summary:
        logger.debug("[Hook] result_verify: empty response, skipping L3 (session=%s)", session_id)
        return

    logger.info(
        "[Hook] L3 result verification requested (session=%s, agent_type=%s)",
        session_id, agent_type,
    )

    # Build verification payload
    payload = {
        "result_summary": result_summary,
        "agent_name": getattr(state, "agent_name", "Unknown"),
        "agent_type": agent_type,
        "game_version": getattr(state, "game_version", ""),
        "module": getattr(state, "module", ""),
        "turn_count": getattr(state, "turn_count", 0),
    }

    # Pause for human verification
    record = await interrupt_manager.request_interrupt(
        session_id=session_id,
        interrupt_type=InterruptType.RESULT_VERIFY,
        payload=payload,
    )

    action = record.action or "pass"

    logger.info(
        "[Hook] L3 verification completed (session=%s, action=%s, notes=%s)",
        session_id, action, record.notes,
    )

    # Store verdict in session state for downstream use (e.g., conclusion writer)
    if hasattr(state, "__dict__"):
        state.__dict__["_last_verdict"] = action
        state.__dict__["_last_verdict_notes"] = record.notes

    if action == "fail":
        logger.warning(
            "[Hook] Agent result marked FAIL by human (session=%s, notes=%s)",
            session_id, record.notes,
        )
    elif action == "rerun":
        logger.info("[Hook] Human requested re-run (session=%s)", session_id)
    elif action == "investigate":
        logger.info("[Hook] Human requested further investigation (session=%s)", session_id)
    else:
        logger.info("[Hook] Agent result marked PASS by human (session=%s)", session_id)


def _extract_result_summary(run_output: Any) -> Optional[str]:
    """Extract a text summary from the RunOutput."""
    if run_output is None:
        return None

    # Agno RunOutput typically has a .content attribute
    if hasattr(run_output, "content") and run_output.content:
        content = str(run_output.content)
        # Truncate for payload
        if len(content) > 2000:
            return content[:2000] + "\n\n...[已截断，请查看完整执行记录]"
        return content

    # Fallback: convert to string
    result_str = str(run_output)
    if result_str and result_str != "None":
        return result_str[:2000]

    return None
