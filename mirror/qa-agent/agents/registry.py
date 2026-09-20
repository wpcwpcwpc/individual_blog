"""
QA Agent System — Agent Registry

Global registry for all available agent definitions.
Supports both builtin agents and hot-loaded AGENT.md custom agents.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base import AgentDefinition

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Singleton registry mapping agent IDs and names to their definitions.

    Supports lookup by ``agent_id`` (kebab-case, preferred) or ``name`` (legacy).
    """

    def __init__(self) -> None:
        self._definitions: Dict[str, AgentDefinition] = {}  # keyed by ID
        self._by_name: Dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition) -> None:
        """Register an agent definition, keyed by agent_id (or name fallback)."""
        agent_id = definition.agent_id or definition.name
        if agent_id in self._definitions:
            logger.debug("Agent '%s' re-registered (overwrite)", agent_id)
        self._definitions[agent_id] = definition
        self._by_name[definition.name] = definition
        logger.debug("Registered agent: %s (id=%s)", definition.name, agent_id)

    def register_all(self, definitions: List[AgentDefinition]) -> None:
        for d in definitions:
            self.register(d)

    def get(self, key: str) -> Optional[AgentDefinition]:
        """Return an AgentDefinition by agent_id or name, or None."""
        if key in self._definitions:
            return self._definitions[key]
        return self._by_name.get(key)

    def all(self) -> List[AgentDefinition]:
        return list(self._definitions.values())

    def to_api_list(self) -> List[Dict]:
        """Return JSON-serializable list for GET /agents."""
        return [
            {
                "id": d.agent_id or d.name,
                "name": d.name,
                "description": d.description,
                "when_to_use": d.when_to_use,
                "tools": d.tool_names,
                "permission_mode": d.permission_mode,
                "agent_type": d.agent_type,
                "tags": d.tags,
                "inject_history": d.inject_history,
            }
            for d in self._definitions.values()
        ]

    def __contains__(self, key: str) -> bool:
        return key in self._definitions or key in self._by_name

    def __len__(self) -> int:
        return len(self._definitions)


# ---------------------------------------------------------------------------
# Module-level singleton, pre-populated with builtin agents
# ---------------------------------------------------------------------------
agent_registry = AgentRegistry()


def _register_builtins() -> None:
    """Register all builtin agent definitions."""
    from agents.builtin.general_agent import GeneralAgentDefinition
    from agents.builtin.coding_agent import build_coding_agent_definition

    agent_registry.register_all([
        GeneralAgentDefinition,
        # Built at import time so the approval tools + dangerous-command guard
        # are attached before the first session lookup.
        build_coding_agent_definition(),
    ])
    logger.info("Registered %d builtin agents", len(agent_registry))


# Auto-register builtins on import
_register_builtins()
