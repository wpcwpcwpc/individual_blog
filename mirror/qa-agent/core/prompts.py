"""
System Prompt Builder — Tool Usage Section

Generates the "# Using your tools" section injected into every Agent's
system prompt. Tells the LLM which dedicated tool to use for which task,
common mistakes to avoid, and the "measure twice, cut once" philosophy.

References:
  - Claude Code getUsingYourToolsSection
  - Claude Code getSimpleDoingTasksSection (measure twice, cut once)
"""

from __future__ import annotations

import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)

# Tool name constants
FILE_READ = "file_read"
FILE_EDIT = "file_edit"
GLOB_SEARCH = "glob_search"
GREP = "grep"
BASH = "bash"
AGENT_SPAWN = "agent_spawn"


def get_tool_usage_section(enabled_tools: Optional[Set[str]] = None) -> str:
    """Build the "Using your tools" system prompt section.

    This section teaches the LLM:
    1. Which dedicated tool to use instead of bash equivalents
    2. The "read before edit" principle
    3. Parallel tool calling guidance
    4. Common mistakes and how to avoid them

    Args:
        enabled_tools: Set of tool names available to this Agent.
            If None, generates guidance for all tools.

    Returns:
        A multi-line string for injection into the system prompt.
    """
    all_tools = enabled_tools or {
        FILE_READ, FILE_EDIT, GLOB_SEARCH, GREP, BASH, AGENT_SPAWN,
    }

    sections = ["# Using your tools\n"]

    # ── Tool priority rules ─────────────────────────────────────────────
    priority_rules = []

    if FILE_READ in all_tools and BASH in all_tools:
        priority_rules.append(
            f"- To read files, use `{FILE_READ}` instead of bash `cat`, `head`, or `tail`."
        )

    if FILE_EDIT in all_tools and BASH in all_tools:
        priority_rules.append(
            f"- To edit files, use `{FILE_EDIT}` instead of bash `sed`, `awk`, or heredocs. "
            f"`{FILE_EDIT}` uses search/replace with validation — it prevents common mistakes."
        )

    if GLOB_SEARCH in all_tools and BASH in all_tools:
        priority_rules.append(
            f"- To find files, use `{GLOB_SEARCH}` instead of bash `find` or `ls`."
        )

    if GREP in all_tools and BASH in all_tools:
        priority_rules.append(
            f"- To search file contents, use `{GREP}` instead of bash `grep` or `rg`."
        )

    if BASH in all_tools:
        priority_rules.append(
            f"- Reserve `{BASH}` exclusively for system commands that have no dedicated tool: "
            f"installing packages, running tests, git operations, starting servers."
        )

    if priority_rules:
        sections.append(
            f"Do NOT use `{BASH}` when a dedicated tool is provided. "
            f"Dedicated tools have better validation, error guidance, and permission control.\n"
        )
        sections.extend(priority_rules)
        sections.append("")

    # ── Read-before-edit principle ──────────────────────────────────────
    if FILE_READ in all_tools and FILE_EDIT in all_tools:
        sections.append(
            "## Editing files: measure twice, cut once\n\n"
            f"ALWAYS read a file with `{FILE_READ}` before editing it with `{FILE_EDIT}`. "
            f"This ensures you see the latest content and avoids stale edits.\n\n"
            f"`{FILE_EDIT}` uses search/replace mode:\n"
            f"- Provide `old_string` (exact text to find) and `new_string` (replacement)\n"
            f"- The old_string must match EXACTLY, including whitespace and indentation\n"
            f"- To create a new file, use `old_string=''` with `new_string` as the full content\n"
            f"- If multiple matches exist, include more surrounding context to make it unique\n"
        )

    # ── Large-project search strategy ────────────────────────────────────
    if GREP in all_tools or GLOB_SEARCH in all_tools:
        sections.append(
            "## Search strategy for large projects\n\n"
            "Your workspace may contain tens of thousands of files. "
            "Recursive search (`grep`/`glob_search`) at the workspace root "
            "can be extremely slow and may time out.\n\n"
            "**NEVER run `grep` or `glob_search` at the workspace root directory "
            '(`"."` or the workspace path itself) unless the target directory is '
            "confirmed to be small (< 1000 files).**\n\n"
            "Instead, use a narrowing strategy — trade extra search calls for speed:\n\n"
            f"1. **Locate first**: Use `{GLOB_SEARCH}` with a non-recursive pattern "
            f'(e.g. `"*"`) to list top-level directories and understand the layout.\n'
            "2. **Guess the subdirectory**: Based on directory names and your task "
            "context, identify the most likely subdirectory.\n"
            f"3. **Search narrowly**: Run `{GREP}` or `{GLOB_SEARCH}` within that "
            "specific subdirectory.\n"
            "4. **Widen if needed**: If no results, try sibling directories or go one "
            "level up — but NEVER jump straight to the root.\n\n"
            "Example — finding a function `calc_damage`:\n"
            '  ❌ `grep(pattern="calc_damage", path=".")`              '
            "← scans 50k+ files, will time out\n"
            '  ✅ `grep(pattern="calc_damage", path="game/combat/")`   '
            "← scans ~200 files, instant\n"
            '  ✅ `grep(pattern="calc_damage", path="game/")`           '
            "← fallback if not found, ~2k files\n"
        )

    # ── Parallel calling ────────────────────────────────────────────────
    sections.append(
        "## Parallel tool calls\n\n"
        "You can call multiple tools in a single response. "
        "If there are no dependencies between the calls, make all independent calls in parallel. "
        "For example, reading multiple files or searching in different directories.\n"
    )

    # ── Agent spawning ──────────────────────────────────────────────────
    if AGENT_SPAWN in all_tools:
        sections.append(
            "## Sub-agents\n\n"
            f"Use `{AGENT_SPAWN}` to delegate complex sub-tasks to specialized agents. "
            f"Each sub-agent runs independently with its own context.\n"
        )

    # ── Work approach ───────────────────────────────────────────────────
    sections.append(
        "## Work approach\n\n"
        "For any task that involves modifying files or executing commands:\n"
        "1. **Explore first**: Read relevant files, search for patterns, understand the codebase\n"
        "2. **Plan**: Form a clear plan of what to change, which files, which lines\n"
        "3. **Implement**: Make focused, minimal changes one at a time\n"
        "4. **Verify**: Check the result — re-read modified files, run tests if applicable\n\n"
        "Do NOT skip steps 1 and 2 and jump straight to editing.\n"
    )

    return "\n".join(sections)


def get_coordinator_tool_section() -> str:
    """Build tool usage section specifically for Coordinator agents.

    Coordinator agents can ONLY use orchestration tools.
    """
    return """# Using your tools

You are a Coordinator — an orchestrator, NOT an executor.

## CRITICAL: YOU CANNOT USE TOOLS DIRECTLY

You can ONLY use these tools:
- `agent_spawn` — Launch a Worker to do actual work
- `send_message` — Continue an existing Worker conversation
- `task_stop` — Stop a running Worker

## YOUR WORKFLOW

### Phase 1: RESEARCH (Parallel OK)
Spawn multiple Workers to explore different aspects of the task.
Workers are ASYNC — launch independent workers together, don't wait!

### Phase 2: SYNTHESIS (You do this)
Read all Worker reports. Understand the full picture.
Create a DETAILED implementation plan with:
- Exact file paths
- Exact line numbers
- Exact code changes

⚠️ DO NOT give vague instructions to Workers.
❌ "Based on your findings, fix the issue"
✅ "In src/auth.py:42, change `timeout = 30` to `timeout = 60`"

### Phase 3: IMPLEMENTATION (Sequential for related files)
Send detailed instructions to Workers. Provide COMPLETE context.
Workers do NOT remember previous conversations unless you include it.

### Phase 4: VERIFICATION
Spawn a Verifier Worker to run tests and validate results.
Report the final outcome to the user.

## PARALLELISM IS YOUR SUPERPOWER
Workers are async. Launch independent workers concurrently.
Don't serialize work that can run simultaneously.
"""


def build_system_prompt(
    *,
    agent_instructions: str = "",
    enabled_tools: Optional[Set[str]] = None,
    is_coordinator: bool = False,
    memory_section: str = "",
    env_info: str = "",
) -> str:
    """Build the complete system prompt for an Agent.

    Combines:
    1. Agent-specific instructions
    2. Tool usage section (normal or coordinator)
    3. Memory section (if available)
    4. Environment info

    Args:
        agent_instructions: The agent's custom instructions (from AGENT.md or builtin).
        enabled_tools: Set of tool names available to this agent.
        is_coordinator: If True, use Coordinator-specific tool guidance.
        memory_section: Optional memory/context section.
        env_info: Optional environment info section.

    Returns:
        Complete system prompt string.
    """
    parts = []

    # Agent identity & instructions
    if agent_instructions:
        parts.append(agent_instructions)

    # Tool usage guidance
    if is_coordinator:
        parts.append(get_coordinator_tool_section())
    else:
        parts.append(get_tool_usage_section(enabled_tools))

    # Memory
    if memory_section:
        parts.append(memory_section)

    # Environment
    if env_info:
        parts.append(env_info)

    return "\n\n---\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# Task Management prompt section (P1)
# ═══════════════════════════════════════════════════════════════════════


def get_task_management_section() -> str:
    """Build the "## Task Management" system prompt section.

    Instructs the Agent to:
    1. Call task_list() first to check for an existing plan
    2. Create a plan via task_update if none exists
    3. Work through tasks sequentially, marking each complete
    """
    return """\
## Task Management

Your FIRST action for any task MUST be:
1. Call `task_list()` to check if a task plan already exists for this session.
2. If **no plan exists**: Analyze the user's request, then create a structured plan:
   ```
   task_update(task_id=0, status="create", summary='{"goal": "...", "strategy": "...", "tasks": ["step 1", "step 2", ...]}')
   ```
   - **goal**: What you want to achieve (1 clear sentence)
   - **strategy**: Your approach (1-2 sentences)
   - **tasks**: 3-10 concrete, ordered steps — each should be verifiable
3. If **a plan exists**: Continue from the current active task.

### Working through tasks

- Focus on ONE task at a time — the one marked active (▶️)
- After completing a task, call `task_update(task_id=N, status="done", summary="brief result")`
- The system automatically advances to the next task
- If a task fails, call `task_update(task_id=N, status="failed", summary="what went wrong")`
- If a task is not applicable, call `task_update(task_id=N, status="skipped", summary="reason")`

### When all tasks are complete

Generate a comprehensive summary/report of everything you accomplished.
Include key findings, results, and any issues discovered.
"""


# ═══════════════════════════════════════════════════════════════════════
# Backward-compatible exports (used by core/engine.py)
# ═══════════════════════════════════════════════════════════════════════

PLAN_FIRST_INSTRUCTIONS = """\
## Work approach — Plan first, execute second

For any task that involves modifying files or executing commands:
1. **Explore first**: Read relevant files, search for patterns, understand the context
2. **Plan**: Form a clear plan — which files, which lines, what changes
3. **Implement**: Make focused, minimal changes one at a time
4. **Verify**: Re-read modified files, run tests if applicable

Do NOT skip steps 1 and 2 and jump straight to editing.
"""

TOOL_ANTI_PATTERNS = """\
## Tool usage rules

- Use `file_read` to read files, NOT `bash` with `cat`/`head`/`tail`
- Use `file_edit` to edit files, NOT `bash` with `sed`/`awk`
- Use `grep` to search file contents, NOT `bash` with `grep`/`rg`
- Use `glob_search` to find files, NOT `bash` with `find`/`ls`
- Reserve `bash` for system commands: installing packages, running tests, git operations
- ALWAYS read a file before editing it — measure twice, cut once
- You can call multiple independent tools in parallel in one response
"""
