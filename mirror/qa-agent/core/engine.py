"""
QA Agent System — Agent Engine

Factory function for creating Agno Agent instances with the full
QA Agent configuration (model, tools, storage, memory, instructions, hooks).

Compatible with Agno 2.5.14+
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, Callable, List, Optional

from agno.agent import Agent

from core.config import QAAgentSettings, settings
from core.instructions import NORMAL_AGENT_INSTRUCTIONS, WORKSPACE_INSTRUCTIONS_TEMPLATE
from core.memory_setup import (
    get_compression_agent_kwargs,
    get_knowledge_agent_kwargs,
    get_memory_agent_kwargs,
)
from core.models import get_model
from core.prompts import PLAN_FIRST_INSTRUCTIONS, TOOL_ANTI_PATTERNS, get_task_management_section
from core.task_plan import TaskPlan
from core.session_state import QASessionState
from core.storage import get_storage

if TYPE_CHECKING:
    from agents.base import AgentDefinition

logger = logging.getLogger(__name__)


def create_normal_agent(
    config: QAAgentSettings | None = None,
    *,
    tools: Optional[List[Any]] = None,
    extra_instructions: str = "",
    session_id: Optional[str] = None,
    pre_hook: Optional[Callable] = None,
    extra_pre_hooks: Optional[List[Callable]] = None,
    post_hook: Optional[Callable] = None,
    agent_name: str = "QAAgent",
    permission_mode: Optional[str] = None,
    game_version: str = "",
    module: str = "",
    skills: Optional[Any] = None,
    workspace_root: Optional[str] = None,
    include_knowledge: bool = True,
    definition: Optional["AgentDefinition"] = None,
    user_id: Optional[str] = None,
) -> Agent:
    """Create a fully configured Normal-mode Agno Agent.

    Args:
        config: Settings override. Uses global ``settings`` if None.
        tools: List of Agno tool functions. If None, uses default tool set.
        extra_instructions: Additional instructions appended to the system prompt.
        session_id: Unique session identifier. Auto-generated if None.
        pre_hook: Run-level pre_hook callable (Agno 2.5.14 format).
        extra_pre_hooks: Additional run-level pre_hooks appended after the
            global ``abort_pre_hook`` / ``reminder_pre_hook`` (used for
            agent-specific pre-run setup such as dynamic tool injection).
        post_hook: Run-level post_hook callable (Agno 2.5.14 format).
        agent_name: Human-readable agent name.
        permission_mode: Override for permission mode ("default"/"plan"/"bypass").
        game_version: Game version context.
        module: Game module being tested.

    Returns:
        Configured Agno Agent ready for ``agent.arun()``.
    """
    cfg = config or settings
    sid = session_id or str(uuid.uuid4())

    # Build instructions — layer Plan-First philosophy + anti-patterns + task management
    instructions = NORMAL_AGENT_INSTRUCTIONS
    # Workspace context injection (before Plan-First, after base role)
    if workspace_root:
        instructions += WORKSPACE_INSTRUCTIONS_TEMPLATE.format(workspace_root=workspace_root)
    instructions += f"\n\n{PLAN_FIRST_INSTRUCTIONS}"
    instructions += f"\n\n{TOOL_ANTI_PATTERNS}"
    instructions += f"\n\n{get_task_management_section()}"
    if extra_instructions:
        instructions += f"\n\n## Additional Context\n{extra_instructions}"

    # Build session state
    session_state = QASessionState(
        max_turns=cfg.default_max_turns,
        max_context_tokens=cfg.max_context_tokens,
        context_soft_threshold=cfg.context_soft_threshold,
        context_hard_threshold=cfg.context_hard_threshold,
        permission_mode=permission_mode or cfg.default_permission_mode.value,
        agent_type="normal",
        agent_name=agent_name,
        session_id=sid,
        game_version=game_version,
        module=module,
        workspace_root=workspace_root,
    )

    # Get storage for both agent and memory
    db = get_storage(cfg)

    # Get memory configuration kwargs (Agno 2.5.14: uses 'db' param)
    memory_kwargs = get_memory_agent_kwargs(definition, db=db, config=cfg)

    # Get compression configuration kwargs (Agno CompressionManager — in-run
    # tool result compression). Graceful degradation: returns {} on failure
    # or per-agent opt-out, agent runs without compression.
    compression_kwargs = get_compression_agent_kwargs(definition, config=cfg)

    # Get knowledge configuration kwargs (KnowledgeHub + Milvus integration)
    knowledge_kwargs = get_knowledge_agent_kwargs() if include_knowledge else {}

    # Build Agent kwargs (Agno 2.5.14 format)
    agent_kwargs = dict(
        id=agent_name,
        name=agent_name,
        model=get_model(cfg),
        tools=tools or [],
        db=db,  # Agno 2.5.14: 'db' parameter
        instructions=instructions,
        session_state=session_state,
        session_id=sid,
        user_id=user_id,
        # Memory configuration (Agno 2.5.14 style)
        **memory_kwargs,
        # Compression configuration (Agno CompressionManager — in-run)
        **compression_kwargs,
        # Knowledge configuration (KnowledgeHub + search_knowledge_base tool)
        **knowledge_kwargs,
        # Markdown output for structured responses
        markdown=True,
        # Enable session caching so agent._cached_session is available during arun()
        # This is required for _save_agent_session_on_abort to persist conversation
        # history when the user interrupts execution.
        cache_session=True,
    )

    # Hooks (Agno 2.5.14: list format - pre_hooks/post_hooks)
    # abort_pre_hook is FIRST so aborted sessions never enter L2 flow (task 7.2)
    from hooks.permission_hook import abort_pre_hook
    from hooks.reminder_hook import reminder_pre_hook
    pre_hooks = [abort_pre_hook, reminder_pre_hook]
    if extra_pre_hooks:
        pre_hooks.extend(extra_pre_hooks)
    if pre_hook is not None:
        pre_hooks.append(pre_hook)
    agent_kwargs["pre_hooks"] = pre_hooks

    if post_hook is not None:
        agent_kwargs["post_hooks"] = [post_hook]

    # Attach Agno Skills if provided (e.g., excel-diff-analyzer)
    if skills is not None:
        agent_kwargs["skills"] = skills

    # Create Agent
    agent = Agent(**agent_kwargs)

    skills_info = f", skills={skills.get_skill_names()}" if skills else ""
    logger.info(
        "Created Normal Agent '%s' (session=%s, permission=%s, tools=%d%s)",
        agent_name, sid, session_state.permission_mode, len(tools or []), skills_info,
    )
    logger.info("[DIAG] create_normal_agent DONE: name=%s agent_type=%s module=%s",
                 agent_name, type(agent).__name__, type(agent).__module__)

    return agent