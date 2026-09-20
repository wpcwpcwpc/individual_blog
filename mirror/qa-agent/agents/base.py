"""
QA Agent System — Agent Definition Base

Defines AgentDefinition dataclass and the create_agent_from_definition()
factory function that converts a definition into a fully configured Agno Agent.
"""

from __future__ import annotations

import logging
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def resolve_skill_runner() -> str:
    """Return shell-ready runner prefix for invoking skill scripts via bash.

    frozen:     ``"<exe>" --run-script``   (in-process exec; bundled packages
                are importable because the internal dir is on sys.path[0])
    dev/docker: ``"<sys.executable>"``     (venv/container python)

    Called at agent-definition build time; result injected into prompt templates
    as the ``{runner}`` placeholder so agents invoke skill scripts with the
    mode-correct prefix instead of a hardcoded ``python`` literal.
    """
    exe = sys.executable.replace("\\", "/")
    if getattr(sys, "frozen", False):
        return f'"{exe}" --run-script'
    return f'"{exe}"'


@dataclass
class AgentDefinition:
    """Declarative definition of an Agent's capabilities and behavior.

    Used by both builtin agents and hot-loaded AGENT.md custom agents.

    ``agent_id`` is the URL-safe kebab-case identifier used in API routes
    (e.g. ``/agents/qa-automation/runs``).  If omitted, ``name`` is used as
    the fallback id.
    """
    name: str
    description: str
    instructions: str

    agent_id: Optional[str] = None  # URL-safe kebab-case; defaults to name if None

    # Tool configuration
    tool_names: List[str] = field(default_factory=list)   # from tool registry
    extra_tools: List[Any] = field(default_factory=list)  # direct callables

    # Behavior
    permission_mode: str = "default"   # "default" | "plan" | "bypass"
    agent_type: str = "normal"         # "normal" | "worker"
    max_turns: int = 25

    # Model configuration
    # None → DEFAULT slot; "slot:<name>" → named slot; "inherit" → deprecated
    model_id: Optional[str] = None

    # Metadata for API listing
    when_to_use: str = ""
    tags: List[str] = field(default_factory=list)

    # Memory injection
    inject_history: bool = True  # whether to inject Milvus historical context

    # Agno session-context control (paired with Agno Agent kwargs).
    # ``inject_history`` above only controls Milvus retrieval injection;
    # these three fields control Agno's own context-injection knobs.
    # Each field pairs two Agno kwargs:
    #   add_session_history → add_history_to_context + num_history_runs
    #   add_session_summary → enable_session_summaries + add_session_summary_to_context
    #   add_user_memories   → enable_user_memories + add_memories_to_context
    # Defaults are True to preserve pre-change behavior (backward compatible).
    add_session_history: bool = True
    add_session_summary: bool = True
    add_user_memories: bool = True

    # Per-agent override for Agno ``num_history_runs`` (how many prior runs'
    # messages are injected into context each arun()). None → fall back to
    # global ``settings.num_history_runs``. Set lower for agents prone to
    # context bloat (large tool outputs), higher for agents that need
    # deeper conversation recall. Only effective when add_session_history=True.
    num_history_runs: Optional[int] = None

    # Per-agent opt-out for Agno CompressionManager (in-run tool result
    # compression). None → fall back to global
    # ``settings.compress_tool_results``. Set False only for agents whose
    # tool outputs are already small/structured (e.g. single-shot config
    # validators) — avoids compression LLM cost for negligible savings.
    compress_tool_results: Optional[bool] = None

    # Agno Skills (native skill system — provides get_skill_instructions etc.)
    skills: Optional[Any] = None  # agno.skills.Skills instance

    # MCP tool injection
    include_mcp_tools: bool = True  # whether to inject enabled MCP tools

    # Skill discovery tool injection
    include_skill_tools: bool = True  # whether to inject find_skill/load_skill

    # Knowledge (search_knowledge_base) injection
    include_knowledge: bool = True  # whether to inject knowledge search capability

    # Agent-specific tool_hooks (appended after global hooks in create_agent_from_definition)
    agent_tool_hooks: List[Any] = field(default_factory=list)

    # Agent-specific pre_hooks (appended after global abort_pre_hook /
    # reminder_pre_hook in create_normal_agent). Used for per-agent run-time
    # setup such as dynamic MCPTools injection from session_state connectors.
    agent_pre_hooks: List[Any] = field(default_factory=list)

    # Agent-specific post_hooks (appended after the global combined_post_hook
    # in create_agent_from_definition). Agno's execute_post_hooks passes
    # ``agent`` / ``run_output`` / ``run_context`` / ``session`` / ``user_id``
    # / ``metadata`` — hooks may accept any subset (filter_hook_args picks
    # matching kwargs). Errors inside a hook are logged by agno but never
    # propagate to the agent run's return value.
    agent_post_hooks: List[Any] = field(default_factory=list)

    # Force serial execution of tool calls: wraps every tool entrypoint in a
    # global asyncio.Lock so that multiple tool_calls the model emits in one
    # turn (dispatched by agno's asyncio.gather) run strictly one at a time.
    # Prevents concurrent bash/file_edit calls and the streaming delta-merge
    # bug ("Unable to decode function arguments"). Default serial for all
    # agents — set False only for agents that genuinely benefit from parallel
    # read-only calls.
    force_serial_tool_calls: bool = True


async def create_agent_from_definition(
    definition: AgentDefinition,
    *,
    session_id: Optional[str] = None,
    task_description: str = "",
    game_version: str = "",
    module: str = "",
    config=None,
    session_overrides: Optional[dict] = None,
    workspace_root: Optional[str] = None,
    context_ids: Optional[list] = None,  # task 10.4: artifact IDs to inject
    user_id: Optional[str] = None,
) -> Any:
    """Create a fully configured Agno Agent from an AgentDefinition.

    Handles:
    - Tool assembly from registry + extra_tools
    - Model resolution via ModelSlotRegistry (based on definition.model_id)
    - Milvus historical context injection (if inject_history=True)
    - Hook assembly (L2 permission, execution log, context threshold, result verify)
    - Session state initialization

    Args:
        definition: The agent definition to instantiate.
        session_id: Override session ID (auto-generated if None).
        task_description: Used for Milvus context injection search.
        game_version: Game version context.
        module: Game module context.
        config: Settings override.
        session_overrides: Session-level model slot overrides (Dict[str, SlotConfig]).
        workspace_root: Optional workspace directory root for tool path resolution.

    Returns:
        Configured Agno Agent instance.
    """
    from core.engine import create_normal_agent
    from tools.registry import tool_registry

    sid = session_id or str(uuid.uuid4())

    # Assemble tools from registry
    tools = tool_registry.get_tools_by_names(definition.tool_names)
    tools.extend(definition.extra_tools)

    # Inject MCP tools if enabled
    if definition.include_mcp_tools:
        try:
            from mcp_service.manager import mcp_manager
            mcp_tools = mcp_manager.get_enabled_tools()
            if mcp_tools:
                tools.extend(mcp_tools)
                logger.info(
                    "Agent '%s': injected %d MCP tools",
                    definition.name, len(mcp_tools),
                )
        except Exception:
            logger.warning("Failed to inject MCP tools for agent '%s'", definition.name)

    # Inject Skill discovery tools if enabled
    if definition.include_skill_tools:
        try:
            skill_tool_fns = tool_registry.get_tools_by_names(["find_skill", "load_skill"])
            if skill_tool_fns:
                tools.extend(skill_tool_fns)
                logger.info(
                    "Agent '%s': injected %d skill discovery tools",
                    definition.name, len(skill_tool_fns),
                )
        except Exception:
            logger.warning("Failed to inject skill discovery tools for agent '%s'", definition.name)

    # Build extra instructions — cache-aware assembly order:
    # LLM 前缀缓存按「首个字节差异点」
    # 命中，跨 session 复用要求分叉点尽量靠后。因此半静态段（skill 索引，
    # 仅 skill 上传时变化）MUST 前置，动态段（Milvus 历史注入，随
    # task_description 每次变化）后置。
    extra_instructions = ""

    # Inject on-demand Skill index (Level 0) into agent instructions.
    # 仅对启用 skill 发现工具的 agent 注入:无 find_skill/load_skill 的 agent
    # (如评测 agent)拿不到激活入口,塞索引纯属上下文污染。
    if definition.include_skill_tools:
        try:
            from skills.loader import build_skill_index_prompt
            # Access skill_loader from FastAPI app.state (set in lifespan)
            from api.server import app
            skill_loader = getattr(app.state, "skill_loader", None)
            if skill_loader is not None:
                on_demand_skills = skill_loader.get_on_demand_skills()
                skill_index = build_skill_index_prompt(on_demand_skills)
                if skill_index:
                    extra_instructions = skill_index
        except Exception:
            logger.warning("Failed to inject Skill index for agent '%s'", definition.name)

    if definition.inject_history and task_description:
        try:
            from memory.injector import inject_historical_context
            historical_ctx = await inject_historical_context(
                task_description=task_description,
                game_version=game_version,
                module=module,
            )
            if historical_ctx:
                extra_instructions = (
                    extra_instructions + "\n" + historical_ctx
                    if extra_instructions else historical_ctx
                )
        except Exception:
            logger.warning("Failed to inject historical context for agent '%s'", definition.name)

    # Assemble run-level post_hooks
    from hooks.context_threshold_hook import context_threshold_post_hook
    # L3 result verification disabled — every conversation ending triggered
    # an interrupt which was redundant for most flows.  The DB persistence
    # bug in _persist_interrupt has been fixed (see interrupt_manager.py)
    # so this can be re-enabled later if needed.
    # from hooks.result_verify_hook import result_verify_post_hook

    async def combined_post_hook(agent: Any, run_output: Any) -> None:
        await context_threshold_post_hook(agent, run_output)
        # await result_verify_post_hook(agent, run_output)  # L3 disabled

    # Resolve model via ModelSlotRegistry
    from core.model_slots import get_model_slot_registry, parse_model_id
    slot = parse_model_id(definition.model_id)
    resolved_model = get_model_slot_registry().resolve(slot, session_overrides)
    logger.info(
        "Agent '%s' model resolved: slot=%s, model_id=%s",
        definition.name, slot.value, definition.model_id,
    )

    # Build the Agent via engine factory
    agent = create_normal_agent(
        config=config,
        tools=tools,
        extra_instructions=definition.instructions + (
            f"\n\n{extra_instructions}" if extra_instructions else ""
        ),
        session_id=sid,
        post_hook=combined_post_hook,
        extra_pre_hooks=definition.agent_pre_hooks,
        agent_name=definition.name,
        permission_mode=definition.permission_mode,
        game_version=game_version,
        module=module,
        skills=definition.skills,
        workspace_root=workspace_root,
        include_knowledge=definition.include_knowledge,
        definition=definition,
        user_id=user_id,
    )
    logger.info("[DIAG] create_agent_from_definition DONE: name=%s agent_type=%s module=%s arun=%s",
                 definition.name, type(agent).__name__, type(agent).__module__, type(agent.arun).__name__)

    # Apply slot-resolved model
    agent.model = resolved_model

    # Override agent_type in session state
    if hasattr(agent, "session_state") and agent.session_state:
        agent.session_state.agent_type = definition.agent_type
        agent.session_state.max_turns = definition.max_turns
        # task 10.4: forward context_ids for artifact injection in reminder_pre_hook
        if context_ids:
            agent.session_state.context_ids = list(context_ids)

    # --- Register tool_hooks (Agno middleware pattern) ---
    # Agno inspects the hook's signature and injects `agent`, `function_name`,
    # `func`, `arguments` by name — no closure capture is needed. See the
    # module docstring in hooks/execution_log_hook.py for the contract.
    #
    # Order matters: execution_log_hook runs FIRST to capture original
    # (uncorrected) arguments, then mcp_safety_hook applies its guards.
    from hooks.execution_log_hook import execution_log_tool_hook
    from hooks.mcp_safety_hook import mcp_safety_tool_hook

    existing_tool_hooks = list(getattr(agent, "tool_hooks", None) or [])
    existing_tool_hooks.append(execution_log_tool_hook)
    existing_tool_hooks.append(mcp_safety_tool_hook)
    # Append agent-specific tool_hooks from definition
    if definition.agent_tool_hooks:
        existing_tool_hooks.extend(definition.agent_tool_hooks)
    # Serial tool execution: wrap entrypoint in a global asyncio.Lock so that
    # multiple tool_calls emitted by the model in one turn (dispatched by
    # agno's asyncio.gather) execute strictly one at a time. Last position so
    # it only serializes the entrypoint, not the logging/safety enrichment.
    if definition.force_serial_tool_calls:
        from hooks.serial_tool_lock_hook import serial_tool_lock_hook
        existing_tool_hooks.append(serial_tool_lock_hook)
    agent.tool_hooks = existing_tool_hooks

    # --- Register agent-specific post_hooks ---
    # combined_post_hook is already installed via create_normal_agent (see
    # engine.py:143). Definition-supplied hooks are appended and invoked by
    # agno's execute_post_hooks with per-hook kwargs filtering.
    if definition.agent_post_hooks:
        existing_post_hooks = list(getattr(agent, "post_hooks", None) or [])
        existing_post_hooks.extend(definition.agent_post_hooks)
        agent.post_hooks = existing_post_hooks

    # Observability: one INFO line per agent build so operators can tell at
    # startup which hooks are actually wired up.
    try:
        _tool_hook_names = [
            getattr(h, "__name__", repr(h)) for h in (agent.tool_hooks or [])
        ]
        _post_hook_names = [
            getattr(h, "__name__", repr(h))
            for h in (getattr(agent, "post_hooks", None) or [])
        ]
        logger.info(
            "Agent '%s' hooks registered: tool_hooks=[%s], post_hooks=[%s]",
            definition.name,
            ", ".join(_tool_hook_names),
            ", ".join(_post_hook_names),
        )
    except Exception:
        logger.debug("Failed to render hook registration log", exc_info=True)

    logger.info(
        "Created agent '%s' (type=%s, tools=%d, session=%s)",
        definition.name, definition.agent_type, len(tools), sid,
    )
    return agent
