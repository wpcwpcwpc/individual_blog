"""
QA Agent System — Model Slot Registry

Provides role-based model assignment through named "slots". Each slot
represents a thinking mode (orchestrate, reason) and resolves
to a specific LLM model configuration.

Resolution chain (highest priority first):
  L2: Session-level overrides (from API request)
  L3: .env slot configuration (LLM_SLOT_{NAME}_*)
  L4: Global default (LLM_MODEL / LLM_BASE_URL / LLM_API_KEY)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ModelSlot Enum
# ---------------------------------------------------------------------------

class ModelSlot(str, Enum):
    """Predefined model slots — each maps to a distinct Agent thinking mode.

    - DEFAULT:     Global default, used by execution-focused agents (strongest model)
    - ORCHESTRATE: Coordination & delegation (cheap, good at instruction-following)
    - REASON:      Deep analysis & planning (strong reasoning, can be slow)
    - VISION:      Multimodal image-to-text (vision-capable OpenAI-compatible model)
    """
    DEFAULT = "default"
    ORCHESTRATE = "orchestrate"
    REASON = "reason"
    VISION = "vision"


# ---------------------------------------------------------------------------
# SlotConfig
# ---------------------------------------------------------------------------

@dataclass
class SlotConfig:
    """Configuration for a single model slot. All fields are optional;
    unset fields fall back to the global default during resolution."""

    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None

    def merge_with(self, fallback: SlotConfig) -> SlotConfig:
        """Merge with a fallback config. This instance's non-None fields
        take priority; None fields are filled from *fallback*.

        Returns a new SlotConfig (does not mutate either instance).
        """
        return SlotConfig(
            model=self.model if self.model is not None else fallback.model,
            base_url=self.base_url if self.base_url is not None else fallback.base_url,
            api_key=self.api_key if self.api_key is not None else fallback.api_key,
        )

    def is_complete(self) -> bool:
        """True if all three fields are set (non-None)."""
        return (
            self.model is not None
            and self.base_url is not None
            and self.api_key is not None
        )


# ---------------------------------------------------------------------------
# ModelSlotRegistry
# ---------------------------------------------------------------------------

class ModelSlotRegistry:
    """Central registry that resolves ModelSlot → OpenAICompatModel instance.

    Loads slot configs from QAAgentSettings at init time.
    Supports session-level overrides at resolve time.
    """

    def __init__(self, settings: Any) -> None:
        """Initialize from QAAgentSettings.

        Args:
            settings: QAAgentSettings instance with llm_* and llm_slot_* fields.
        """
        self._settings = settings

        # L4: Global default
        self._global_default = SlotConfig(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )

        # L3: .env slot configs (only non-None fields are set)
        self._env_slots: Dict[ModelSlot, SlotConfig] = {}
        self._load_env_slots(settings)

        logger.info(
            "ModelSlotRegistry initialized: %d env slots configured (%s)",
            len(self._env_slots),
            ", ".join(s.value for s in self._env_slots) or "none",
        )

    def _load_env_slots(self, settings: Any) -> None:
        """Load slot configs from settings fields."""
        slot_field_map = {
            ModelSlot.ORCHESTRATE: ("llm_slot_orchestrate_model",
                                    "llm_slot_orchestrate_base_url",
                                    "llm_slot_orchestrate_api_key"),
            ModelSlot.REASON:      ("llm_slot_reason_model",
                                    "llm_slot_reason_base_url",
                                    "llm_slot_reason_api_key"),
            ModelSlot.VISION:      ("llm_slot_vision_model",
                                    "llm_slot_vision_base_url",
                                    "llm_slot_vision_api_key"),
        }

        for slot, (model_field, url_field, key_field) in slot_field_map.items():
            model = getattr(settings, model_field, None)
            base_url = getattr(settings, url_field, None)
            api_key = getattr(settings, key_field, None)

            # Only register if at least one field is configured
            if model is not None or base_url is not None or api_key is not None:
                self._env_slots[slot] = SlotConfig(
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                )

    def resolve(
        self,
        slot: ModelSlot,
        session_overrides: Optional[Dict[str, SlotConfig]] = None,
    ) -> Any:
        """Resolve a slot to an OpenAICompatModel instance.

        Resolution chain (highest priority first):
          L2: session_overrides[slot.value]
          L3: _env_slots[slot]
          L4: _global_default

        Args:
            slot: The ModelSlot to resolve.
            session_overrides: Optional session-level slot overrides.
                Keys are slot value strings (e.g. "orchestrate").

        Returns:
            Configured OpenAICompatModel instance (wrapped with RateLimitedModel).
        """
        # Start with L4 global default
        resolved = self._global_default

        # L3: env slot config
        if slot in self._env_slots:
            resolved = self._env_slots[slot].merge_with(resolved)

        # L2: session overrides
        if session_overrides and slot.value in session_overrides:
            override = session_overrides[slot.value]
            resolved = override.merge_with(resolved)

        model = self._build_model(resolved)

        logger.debug(
            "Resolved slot '%s' → model=%s, base_url=%s",
            slot.value, resolved.model, resolved.base_url,
        )
        return model

    def _build_model(self, config: SlotConfig) -> Any:
        """Build an OpenAICompatModel + RateLimitedModel from a resolved SlotConfig."""
        from core.models import build_model_from_slot_config
        return build_model_from_slot_config(config, self._settings)

    def get_slot_info(self) -> Dict[str, Dict[str, str]]:
        """Return info about all slots for API consumption.

        Returns:
            Dict mapping slot name → { model, base_url, source }.
        """
        info = {}
        for slot in ModelSlot:
            if slot == ModelSlot.DEFAULT:
                info[slot.value] = {
                    "model": self._global_default.model,
                    "base_url": self._global_default.base_url,
                    "source": "env",
                }
            elif slot in self._env_slots:
                resolved = self._env_slots[slot].merge_with(self._global_default)
                info[slot.value] = {
                    "model": resolved.model,
                    "base_url": resolved.base_url,
                    "source": "env",
                }
            else:
                info[slot.value] = {
                    "model": self._global_default.model,
                    "base_url": self._global_default.base_url,
                    "source": "fallback",
                }
        return info


# ---------------------------------------------------------------------------
# parse_model_id — converts AgentDefinition.model_id to ModelSlot
# ---------------------------------------------------------------------------

def parse_model_id(model_id: Optional[str]) -> ModelSlot:
    """Parse an AgentDefinition.model_id string into a ModelSlot.

    Supported formats:
      - None          → ModelSlot.DEFAULT
      - "slot:reason" → ModelSlot.REASON (etc.)
      - "inherit"     → DEPRECATED, treated as DEFAULT with warning
      - anything else → DEFAULT with warning

    Args:
        model_id: The model_id string from AgentDefinition.

    Returns:
        Resolved ModelSlot enum member.
    """
    if model_id is None:
        return ModelSlot.DEFAULT

    if model_id == "inherit":
        logger.warning(
            "model_id='inherit' is deprecated. Use 'slot:<name>' instead. "
            "Falling back to DEFAULT slot."
        )
        return ModelSlot.DEFAULT

    if model_id.startswith("slot:"):
        name = model_id[5:]
        try:
            return ModelSlot(name)
        except ValueError:
            logger.warning(
                "Unknown model slot '%s', falling back to DEFAULT. "
                "Valid slots: %s",
                name, [s.value for s in ModelSlot],
            )
            return ModelSlot.DEFAULT

    logger.warning(
        "Unrecognized model_id '%s', falling back to DEFAULT slot.", model_id
    )
    return ModelSlot.DEFAULT


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

def _create_registry() -> ModelSlotRegistry:
    """Create the global registry singleton. Deferred to avoid circular imports."""
    from core.config import settings
    return ModelSlotRegistry(settings)


# Lazy singleton — initialized on first access
_registry: Optional[ModelSlotRegistry] = None


def get_model_slot_registry() -> ModelSlotRegistry:
    """Get the global ModelSlotRegistry singleton (lazy init)."""
    global _registry
    if _registry is None:
        _registry = _create_registry()
    return _registry
