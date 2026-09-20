"""
QA Agent System — Memory Configuration

Configures Agno MemoryManager for an Agent.

Agno v2 uses `memory_manager` parameter on Agent, not `memory`.
Memory features are also configurable via Agent constructor flags:
  - enable_user_memories
  - enable_session_summaries
  - add_memories_to_context
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from agno.memory import MemoryManager

from core.config import QAAgentSettings, settings

if TYPE_CHECKING:
    from agents.base import AgentDefinition
    from agno.db.base import Db

logger = logging.getLogger(__name__)
event_logger = logging.getLogger("qa_agent.events")


def get_memory_manager(
    db: "Db | Any | None" = None,
    config: QAAgentSettings | None = None,
) -> MemoryManager:
    """Create an Agno MemoryManager instance.

    MemoryManager handles:
    - User memories: cross-session structured memory
    - Session summaries: compression of long conversations

    Args:
        db: Storage backend for persisting memories.
        config: Settings override. Uses global ``settings`` if None.

    Returns:
        Configured Agno MemoryManager instance.
    """
    cfg = config or settings

    manager = MemoryManager(
        db=db,
        # Model for memory operations (summarization, extraction)
        # Uses default model if not specified
    )

    logger.info("Agno MemoryManager configured")
    return manager


def get_memory_agent_kwargs(
    definition: "Optional[AgentDefinition]" = None,
    *,
    db: "Db | Any | None" = None,
    config: QAAgentSettings | None = None,
) -> dict:
    """Return Agent constructor kwargs for memory configuration.

    Instead of passing a Memory object, Agno v2 uses multiple kwargs
    on the Agent itself.

    Args:
        definition: AgentDefinition whose ``add_session_history`` /
            ``add_session_summary`` / ``add_user_memories`` fields control
            which Agno context-injection kwargs are emitted. ``None``
            preserves pre-change behavior (all three treated as True).
        db: Storage backend for persisting memories.
        config: Settings override.

    Returns:
        Dict of kwargs to pass to Agent(...).
    """
    cfg = config or settings

    # Resolve the three context-control flags. When definition is None
    # (e.g. coordinator_agent.py caller), default to True to preserve
    # pre-change behavior.
    if definition is None:
        add_history = True
        add_summary = True
        add_memories = True
        per_agent_history_runs: Optional[int] = None
    else:
        add_history = definition.add_session_history
        add_summary = definition.add_session_summary
        add_memories = definition.add_user_memories
        per_agent_history_runs = definition.num_history_runs

    kwargs: dict = {
        "memory_manager": get_memory_manager(db=db, config=cfg),
    }
    if add_memories:
        # Enable user memory extraction
        kwargs["enable_user_memories"] = True
        # Add memories to context for retrieval
        kwargs["add_memories_to_context"] = True
    if add_summary:
        # Enable session summaries for long conversations
        kwargs["enable_session_summaries"] = True
        kwargs["add_session_summary_to_context"] = True
    if add_history:
        # History management. Per-agent override (if set) takes precedence
        # over global settings.num_history_runs; otherwise fall back to
        # global default. NOTE: not reusing cfg.default_max_turns — that
        # field caps agentic loop iterations within a single arun(), which
        # is semantically unrelated to how many prior runs are injected.
        kwargs["add_history_to_context"] = True
        kwargs["num_history_runs"] = (
            per_agent_history_runs
            if per_agent_history_runs is not None
            else cfg.num_history_runs
        )
    return kwargs


def get_knowledge_agent_kwargs() -> dict:
    """Return Agent constructor kwargs for Knowledge (Milvus) integration.

    Configures the Agent to:
    - Register the ``search_knowledge_base`` tool (new capability)
    - Route all knowledge searches through ``hub_retriever`` (multi-collection)
    - NOT auto-inject knowledge into context (handled by inject_historical_context)
    - NOT allow LLM to write to knowledge (handled by writer.py with metadata)
    - Enable agentic knowledge filters for dynamic search refinement

    Returns:
        Dict of kwargs to merge into Agent(...), or empty dict if
        KnowledgeHub initialization fails.
    """
    try:
        from memory.knowledge_hub import get_knowledge_hub, hub_retriever

        hub = get_knowledge_hub()
        if hub is None:
            logger.warning(
                "KnowledgeHub not available — Agent will not have knowledge search capability"
            )
            return {}

        return {
            "knowledge": hub.facade_knowledge,
            "search_knowledge": True,
            "knowledge_retriever": hub_retriever,
            "add_knowledge_to_context": False,
            "update_knowledge": False,
            # Disabled: agentic filters require contents_db on Knowledge,
            # which we don't use. hub_retriever handles filtering directly.
            "enable_agentic_knowledge_filters": False,
        }
    except Exception as e:
        logger.error("Failed to configure knowledge kwargs: %s", e, exc_info=True)
        return {}


def get_compression_agent_kwargs(
    definition: "Optional[AgentDefinition]" = None,
    *,
    config: QAAgentSettings | None = None,
) -> dict:
    """Return Agent kwargs for Agno native in-run context compression.

    Configures the Agent to compress verbose tool call results DURING
    arun() (between tool calls), preventing single-run context explosion.
    Distinct from cross-session Milvus distiller
    (``hooks/context_threshold_hook``), which fires post-run and writes
    summaries for future sessions — CompressionManager compresses the
    CURRENT run's tool outputs in-flight.

    Args:
        definition: AgentDefinition whose ``compress_tool_results`` field
            controls per-agent opt-out. ``None`` preserves global default
            (uses ``settings.compress_tool_results``).
        config: Settings override. Uses global ``settings`` if None.

    Returns:
        Dict of kwargs to pass to Agent(...). Contains:
        - ``compression_manager``: CompressionManager instance, OR
        - empty dict if compression disabled or setup fails (graceful
          degradation — agent runs without compression).
    """
    cfg = config or settings

    # Per-agent opt-out takes precedence over global default.
    if definition is not None and definition.compress_tool_results is False:
        logger.debug(
            "Agent '%s' opted out of in-run compression",
            getattr(definition, "name", "?"),
        )
        event_logger.info(
            "📦 [压缩] L2 关闭: agent=%s (per-agent opt-out)",
            getattr(definition, "name", "?"),
        )
        return {}

    if not cfg.compress_tool_results:
        event_logger.info("📦 [压缩] L2 关闭: 全局 compress_tool_results=False")
        return {}

    try:
        from core.compression import ToolResultTokenCompressionManager
        from core.model_slots import get_model_slot_registry, parse_model_id

        slot = parse_model_id(cfg.compression_model_slot)
        compression_model = get_model_slot_registry().resolve(slot, None)

        # Use the tool-result-token-based subclass (operator-requested:
        # "不想以 tool 次数来做压缩，而是根据 tool 的返回字节数据量
        # （换算 token）去做压缩"). If subclass init fails we honestly
        # return {} (compression disabled, L4 post-hook provides
        # session-level safety) — no fallback to base CompressionManager,
        # because base lacks tool-result-token triggering and would
        # silently downgrade the operator's intended semantics.
        manager = ToolResultTokenCompressionManager(
            model=compression_model,
            compress_token_limit=cfg.compress_token_limit,
            compress_tool_results_token_limit=cfg.compress_tool_results_token_limit,
        )

        logger.info(
            "CompressionManager configured: kind=ToolResultToken slot=%s "
            "token_limit=%d tool_result_token_limit=%d",
            slot.value, cfg.compress_token_limit,
            cfg.compress_tool_results_token_limit,
        )
        event_logger.info(
            "📦 [压缩] L2 配置: CompressionManager token_limit=%d "
            "tool_result_token_limit=%d slot=%s model=%s",
            cfg.compress_token_limit,
            cfg.compress_tool_results_token_limit,
            slot.value,
            getattr(compression_model, "id", "?"),
        )
        return {
            "compression_manager": manager,
            "compress_tool_results": True,
        }
    except ImportError:
        logger.warning(
            "agno.compression.manager not importable (agno < 2.2.14?) — "
            "in-run compression disabled, agent runs uncompressed",
        )
        event_logger.info("📦 [压缩] L2 不可用: agno.compression.manager 导入失败（版本过低）")
        return {}
    except Exception:
        logger.warning(
            "CompressionManager setup failed — in-run compression disabled",
            exc_info=True,
        )
        event_logger.info("📦 [压缩] L2 不可用: CompressionManager 初始化异常")
        return {}
