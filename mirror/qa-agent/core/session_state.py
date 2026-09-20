"""
QA Agent System — Session State

Defines `QASessionState`, a dataclass mounted on Agno `RunContext.session_state`
to carry per-session runtime metadata through the Agent lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class QASessionState:
    """Custom runtime state attached to every Agent session.

    Mounted via ``Agent(session_state=QASessionState(...))`` and accessible
    inside tools / hooks as ``agent.session_state`` or ``run_context.session_state``.
    """

    # ---- Execution tracking ----
    turn_count: int = 0
    stop_reason: Optional[str] = None  # "end_turn" | "max_turns" | "cancelled" | None
    max_turns: int = 25

    # ---- Context window management ----
    current_context_tokens: int = 0
    max_context_tokens: int = 1_000_000  # matches a 1M-token-context flagship model
    # L4 soft/hard thresholds populated from settings at construction
    # (engine.py / coordinator_agent.py). Methods below read these
    # fields — never hardcoded defaults — so .env overrides actually
    # take effect.
    context_soft_threshold: float = 0.40
    context_hard_threshold: float = 0.60

    # ---- Permission ----
    permission_mode: str = "default"  # "default" | "plan" | "bypass"

    # ---- Agent identity ----
    agent_type: str = "normal"  # "normal" | "coordinator" | "worker"
    agent_name: str = ""
    session_id: str = ""
    worker_id: Optional[str] = None  # Set when running inside a Team

    # ---- Target-under-test context ----
    game_version: str = ""
    module: str = ""  # e.g. "combat", "inventory", "quest"

    # ---- Interrupt state ----
    pending_interrupt_id: Optional[str] = None

    # ---- Compression bookkeeping ----
    compression_count: int = 0  # How many times context was compressed
    last_compression_turn: int = 0

    # ---- Task Plan (P1: structured task tracking) ----
    task_plan: Optional[Dict] = None  # Stores TaskPlan.to_dict() for persistence

    # ---- Active Skill Phase tracking ----
    active_skill_name: Optional[str] = None   # Name of currently executing Skill
    active_skill_phase: int = 0               # Current Phase index (0-based)
    skill_phase_cache: Optional[Dict] = None  # SkillPhaseCache.to_dict() for persistence

    # ---- Workspace ----
    workspace_root: Optional[str] = None     # User-specified workspace root directory

    # ---- Session File Uploads ----
    # Metadata list of files uploaded via POST /sessions/{sid}/uploads.
    # Each entry: {file_id, name, size, type, abs_path}. Session-scoped:
    # cleared on session delete (delete_user_session Step 9 rmtree).
    uploads: list = field(default_factory=list)

    # ---- Worker context injection (task 2.1) ----
    context_ids: list = field(default_factory=list)  # artifact ObjectId strings to inject on start

    # ---- Diminishing returns detection (task 2.2) ----
    last_turn_tokens: int = 0               # Token count at previous turn
    diminishing_turn_count: int = 0         # Consecutive turns with low delta

    # ---- Coordinator session tracking ----
    coordinator_session: bool = False         # True if this is a Coordinator Agent session
    active_workers: List[str] = field(default_factory=list)  # Tracked worker IDs

    # ---- System Reminder flags ----
    _reminder_pending: bool = False           # True = inject reminder on next pre_hook
    _reminder_reason: Optional[str] = None    # "interrupt" | "compact"

    # ---- Dynamic Skill activation ----
    activated_skills: List[str] = field(default_factory=list)      # Names of on-demand Skills activated in this session
    skill_summaries: Dict[str, str] = field(default_factory=dict)  # skill_name -> summary text for reminder injection
    _skill_just_activated: bool = False       # Transient flag: True during the turn that activated skills

    # ---- Helpers ----

    @property
    def context_usage_ratio(self) -> float:
        """Current context usage as a fraction of max."""
        if self.max_context_tokens <= 0:
            return 0.0
        return self.current_context_tokens / self.max_context_tokens

    def increment_turn(self) -> None:
        """Advance the turn counter by 1."""
        self.turn_count += 1

    def is_over_soft_threshold(self) -> bool:
        return self.context_usage_ratio >= self.context_soft_threshold

    def is_over_hard_threshold(self) -> bool:
        return self.context_usage_ratio >= self.context_hard_threshold

    def should_stop(self) -> bool:
        """Return True if the agent has exceeded max turns."""
        return self.turn_count >= self.max_turns
