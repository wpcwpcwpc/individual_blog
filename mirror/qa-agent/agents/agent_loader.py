"""
QA Agent System — AGENT.md Hot Loader

Scans .claude/agents/ for AGENT.md files and loads them as custom
AgentDefinitions, registering them alongside builtin agents.

AGENT.md format:
---
description: "Agent description"
tools: [file_read, bash]
permission_mode: default
when_to_use: "When to use this agent"
tags: [custom, qa]
inject_history: true
---

Agent instructions in Markdown body.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import frontmatter

from agents.base import AgentDefinition

logger = logging.getLogger(__name__)


def load_custom_agents(
    agents_dir: str = ".claude/agents",
) -> List[AgentDefinition]:
    """Scan the agents directory and load all valid AGENT.md definitions.

    Args:
        agents_dir: Path to directory containing agent subdirectories.

    Returns:
        List of loaded AgentDefinition objects.
    """
    base = Path(agents_dir)
    definitions = []

    if not base.exists() or not base.is_dir():
        logger.debug("Custom agents directory not found: %s", agents_dir)
        return definitions

    for agent_dir in sorted(base.iterdir()):
        if not agent_dir.is_dir():
            continue

        agent_md = agent_dir / "AGENT.md"
        if not agent_md.exists():
            # Also accept top-level AGENT.md files
            agent_md = base / f"{agent_dir.name}.md"
            if not agent_md.exists():
                continue

        definition = _parse_agent_md(agent_dir.name, agent_md)
        if definition is not None:
            definitions.append(definition)

    logger.info("Loaded %d custom agent(s) from %s", len(definitions), agents_dir)
    return definitions


def _parse_agent_md(name: str, path: Path) -> Optional[AgentDefinition]:
    """Parse an AGENT.md file into an AgentDefinition."""
    try:
        post = frontmatter.load(str(path))
    except Exception as e:
        logger.error("Failed to parse AGENT.md at %s: %s", path, e)
        return None

    meta = post.metadata
    description = meta.get("description", "").strip()
    if not description:
        logger.error("AGENT.md '%s' missing required 'description' field — skipped", name)
        return None

    instructions = post.content.strip()
    if not instructions:
        logger.warning("AGENT.md '%s' has empty instructions body", name)

    # Parse permission_mode
    permission_mode = meta.get("permission_mode", "default")
    if permission_mode not in ("default", "plan", "bypass"):
        logger.warning("AGENT.md '%s': unknown permission_mode '%s', using 'default'", name, permission_mode)
        permission_mode = "default"

    definition = AgentDefinition(
        name=name,
        description=description,
        instructions=instructions,
        tool_names=meta.get("tools", []) or [],
        permission_mode=permission_mode,
        agent_type="normal",
        max_turns=int(meta.get("max_turns", 25)),
        inject_history=bool(meta.get("inject_history", True)),
        when_to_use=meta.get("when_to_use", ""),
        tags=meta.get("tags", []) or [],
    )

    logger.debug("Loaded custom agent: %s (tools=%s)", name, definition.tool_names)
    return definition


def reload_custom_agents(
    agents_dir: str = ".claude/agents",
) -> int:
    """Reload custom agents and update the global registry.

    Returns:
        Number of agents loaded/updated.
    """
    from agents.registry import agent_registry

    definitions = load_custom_agents(agents_dir)
    for d in definitions:
        agent_registry.register(d)  # overwrites existing if name matches

    return len(definitions)
