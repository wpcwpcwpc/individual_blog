"""
QA Agent System — Memory Injector

Retrieves historical context from Milvus via KnowledgeHub at Worker startup
and formats it as additional instructions text.

Uses hub_retriever for unified multi-collection search with degradation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Number of historical records to retrieve per collection
TOP_K = 5


async def inject_historical_context(
    task_description: str,
    game_version: str = "",
    module: str = "",
) -> str:
    """Retrieve relevant historical context and format for Agent instructions.

    Searches qa_knowledge (compressed summaries), qa_execution_log (recent
    executions), and qa_conclusions via hub_retriever, then formats results
    as markdown for injection into Agent system prompt.

    Args:
        task_description: Description of the current task.
        game_version: Filter by game version (optional, for future use).
        module: Filter by module name (optional, for future use).

    Returns:
        Formatted string to append to Agent instructions, or empty string
        if Milvus is unavailable or no relevant history found.
    """
    from memory.knowledge_hub import hub_retriever

    # Use hub_retriever to search all three collections
    all_docs = hub_retriever(
        query=task_description,
        num_documents=TOP_K,
    )

    if not all_docs:
        logger.debug("No relevant historical context found for task: %s", task_description[:80])
        return ""

    # Separate results by source collection
    knowledge_hits = [d for d in all_docs if d.get("_source") == "qa_knowledge"]
    log_hits = [d for d in all_docs if d.get("_source") == "qa_execution_log"]
    conclusion_hits = [d for d in all_docs if d.get("_source") == "qa_conclusions"]

    sections: List[str] = ["## [Related Historical Knowledge]",
                            "_以下内容来自历史执行记忆，仅供参考，请结合当前任务判断是否适用_\n"]

    # Format knowledge summaries
    if knowledge_hits:
        sections.append("### 历史知识摘要")
        for i, hit in enumerate(knowledge_hits[:3], 1):
            meta = hit.get("meta_data", {}) or {}
            pending = meta.get("pending_steps", "").strip() if isinstance(meta.get("pending_steps"), str) else ""
            findings = meta.get("findings", "").strip() if isinstance(meta.get("findings"), str) else ""
            completed = meta.get("completed_steps", "").strip() if isinstance(meta.get("completed_steps"), str) else ""
            module_name = meta.get("module", "")
            version = meta.get("game_version", "")

            parts = []
            if version or module_name:
                parts.append(f"**版本/模块**: {version} / {module_name}")
            if completed:
                parts.append(f"**已完成步骤**:\n{completed[:500]}")
            if pending:
                parts.append(f"**待续步骤（可续接）**:\n{pending[:500]}")
            if findings:
                parts.append(f"**关键发现**:\n{findings[:500]}")

            if parts:
                sections.append(f"\n**[知识 {i}]**")
                sections.extend(parts)

    # Format execution log hits
    if log_hits:
        sections.append("\n### 相关执行记录")
        for i, hit in enumerate(log_hits[:3], 1):
            meta = hit.get("meta_data", {}) or {}
            tool = meta.get("tool_used", "")
            result = meta.get("result_summary", "").strip() if isinstance(meta.get("result_summary"), str) else ""
            if tool and result:
                sections.append(
                    f"- **步骤{meta.get('step_no', '?')}** `{tool}`: {result[:200]}"
                )

    # Format conclusion hits
    if conclusion_hits:
        sections.append("\n### 相关测试结论")
        for i, hit in enumerate(conclusion_hits[:2], 1):
            meta = hit.get("meta_data", {}) or {}
            verdict = meta.get("verdict", "")
            key_findings = meta.get("key_findings", "").strip() if isinstance(meta.get("key_findings"), str) else ""
            if verdict:
                sections.append(f"- **结论 {i}** [{verdict}]: {key_findings[:200]}")

    sections.append("\n---")

    injected = "\n".join(sections)
    logger.info(
        "Injected historical context (%d knowledge + %d log + %d conclusion records) for task: %s",
        len(knowledge_hits), len(log_hits), len(conclusion_hits), task_description[:60],
    )
    return injected