"""
QA Agent System — Context Distiller

Calls the LLM to summarize the Agent's conversation history
when context thresholds are exceeded.
Produces structured summaries with completed_steps and pending_steps.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
event_logger = logging.getLogger("qa_agent.events")

_DISTILL_SYSTEM_PROMPT = """\
你是 QA 测试记录整理助手。请将以下对话历史整理成结构化摘要，用于续接后续测试工作。

输出严格遵循以下 JSON 格式（不要输出其他内容）：
{
  "completed_steps": "已完成的测试步骤列表（每步一行）",
  "pending_steps":   "尚未完成/需要续接的测试步骤（每步一行，这是最重要的字段）",
  "findings":        "关键发现、Bug 或异常（每条一行）",
  "context_coverage": "本次测试覆盖的模块/功能范围概述（50字以内）"
}

重点关注 pending_steps，确保后续 Agent 可以从此处续接工作。
"""

_HARD_COMPRESS_NOTE = "\n注意：这是硬压缩（上下文超过{pct}%），请尽可能保留所有关键信息。"


def _hard_compress_note() -> str:
    """Build the hard-compress LLM note with the actual configured threshold.

    Replaces the previous hardcoded ``"上下文超过85%"`` literal so the
    prompt reflects ``settings.context_hard_threshold``. Lazy import
    avoids a circular import at module load (distiller is imported by
    context_threshold_hook which runs early in agent setup).
    """
    from core.config import settings
    pct = int(settings.context_hard_threshold * 100)
    return _HARD_COMPRESS_NOTE.format(pct=pct)


async def distill_context(agent: Any, hard: bool = False) -> Optional[Dict[str, str]]:
    """Summarize the Agent's conversation history using LLM.

    Args:
        agent: The Agno Agent instance (for accessing model and memory).
        hard: If True, perform aggressive compression.

    Returns:
        Dict with keys: completed_steps, pending_steps, findings, context_coverage.
        Returns None if distillation fails or history is too short.
    """
    # Extract message history
    history_text = _extract_history(agent)
    if not history_text or len(history_text) < 200:
        logger.debug("History too short for distillation (%d chars)", len(history_text))
        event_logger.info(
            "📦 [压缩] L4 蒸馏跳过: history 过短 (%d chars < 200) hard=%s",
            len(history_text), hard,
        )
        return None

    system_prompt = _DISTILL_SYSTEM_PROMPT
    if hard:
        system_prompt += _hard_compress_note()

    event_logger.info(
        "📦 [压缩] L4 蒸馏 LLM 调用: hard=%s history_chars=%d (截断前)",
        hard, len(history_text),
    )

    try:
        from core.config import settings
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=settings.llm_api_key or None,
            base_url=settings.llm_base_url,
        )

        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请整理以下对话历史：\n\n{history_text[:12000]}"},
            ],
            temperature=0.1,
            max_tokens=1500,
        )

        raw = response.choices[0].message.content or ""
        summary = _parse_summary(raw)

        if summary:
            logger.info(
                "Distillation complete (hard=%s, pending_steps=%d chars)",
                hard, len(summary.get("pending_steps", "")),
            )
            event_logger.info(
                "📦 [压缩] L4 蒸馏 LLM 返回: hard=%s pending=%d chars findings=%d chars completed=%d chars",
                hard,
                len(summary.get("pending_steps", "")),
                len(summary.get("findings", "")),
                len(summary.get("completed_steps", "")),
            )
        return summary

    except Exception:
        logger.warning("Context distillation LLM call failed", exc_info=True)
        event_logger.info("📦 [压缩] L4 蒸馏 LLM 调用失败: hard=%s", hard)
        return None


def _extract_history(agent: Any) -> str:
    """Extract conversation history text from the Agent's memory."""
    lines = []

    try:
        mem = getattr(agent, "memory", None)
        if mem is None:
            return ""

        messages = getattr(mem, "messages", None) or []
        for msg in messages:
            role = ""
            content = ""
            if hasattr(msg, "role") and hasattr(msg, "content"):
                role = str(msg.role)
                content = str(msg.content or "")
            elif isinstance(msg, dict):
                role = msg.get("role", "")
                content = str(msg.get("content", ""))

            if content:
                # Truncate very long individual messages
                if len(content) > 2000:
                    content = content[:2000] + "...[截断]"
                lines.append(f"[{role.upper()}]: {content}")

    except Exception:
        logger.debug("Failed to extract history from agent memory", exc_info=True)

    return "\n\n".join(lines)


def _parse_summary(raw: str) -> Optional[Dict[str, str]]:
    """Parse the LLM's JSON response into a summary dict."""
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    try:
        data = json.loads(text)
        return {
            "completed_steps": str(data.get("completed_steps", "")),
            "pending_steps":   str(data.get("pending_steps", "")),
            "findings":        str(data.get("findings", "")),
            "context_coverage": str(data.get("context_coverage", "")),
        }
    except json.JSONDecodeError:
        logger.warning("Failed to parse distillation JSON: %s", raw[:200])
        # Fallback: return raw as pending_steps
        return {
            "completed_steps": "",
            "pending_steps": raw[:2000],
            "findings": "",
            "context_coverage": "解析失败，原始摘要已保存",
        }
