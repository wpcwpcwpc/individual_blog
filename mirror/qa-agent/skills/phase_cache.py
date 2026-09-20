"""
QA Agent System — Skill Phase Parser & Cache

Parses SKILL.md content into Phase blocks and caches structure metadata
for phase-aware progressive loading.

Key concepts:
- PhaseBlock: A single ### Phase N: section from SKILL.md
- SkillPhaseParseResult: Full parse output with preamble, constraints, phases
- SkillPhaseCache: Serializable cache for session_state persistence
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Regex for Phase headers: ### Phase 1: Title  or  ### Phase 1：标题
_PHASE_HEADER_RE = re.compile(
    r"^###\s+Phase\s+(\d+)\s*[:：]?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

# Regex for global constraints section: ## 全局约束
_GLOBAL_CONSTRAINTS_RE = re.compile(
    r"^##\s+全局约束\s*$",
    re.MULTILINE,
)

# Regex for required read markers: ⚠️ ... 必须先读取 `references/xxx.md`
_REQUIRED_READ_RE = re.compile(
    r"⚠️[^`]*`([^`]+\.md)`",
)

# Regex for suggested read markers: 📖 ... 读取 `references/xxx.md`
_SUGGESTED_READ_RE = re.compile(
    r"📖[^`]*`([^`]+\.md)`",
)


# ═══════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════


@dataclass
class PhaseBlock:
    """A single Phase section parsed from SKILL.md."""

    phase_number: int
    title: str
    content: str
    start_line: int
    end_line: int


@dataclass
class SkillPhaseParseResult:
    """Complete parse result of a SKILL.md with Phase markers."""

    global_preamble: str
    global_constraints: str
    phases: List[PhaseBlock]
    required_reads: Dict[int, List[str]]   # phase_idx → required file paths
    suggested_reads: Dict[int, List[str]]  # phase_idx → suggested file paths


@dataclass
class SkillPhaseCache:
    """Serializable cache of Skill Phase structure for session_state persistence.

    Stores only offsets and metadata — NOT full Phase content.
    Phase content is re-read from SKILL.md file on demand.
    """

    skill_name: str
    skill_base_dir: str
    phase_count: int
    phase_offsets: List[Tuple[int, int]]  # (start_line, end_line) per phase
    phase_titles: List[str]
    required_reads: Dict[int, List[str]]
    suggested_reads: Dict[int, List[str]]
    current_phase: int = 0  # 0-based index

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict for session_state persistence."""
        return {
            "skill_name": self.skill_name,
            "skill_base_dir": self.skill_base_dir,
            "phase_count": self.phase_count,
            "phase_offsets": [list(o) for o in self.phase_offsets],
            "phase_titles": list(self.phase_titles),
            "required_reads": {str(k): v for k, v in self.required_reads.items()},
            "suggested_reads": {str(k): v for k, v in self.suggested_reads.items()},
            "current_phase": self.current_phase,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillPhaseCache:
        """Deserialize from a plain dict."""
        return cls(
            skill_name=data["skill_name"],
            skill_base_dir=data["skill_base_dir"],
            phase_count=data["phase_count"],
            phase_offsets=[tuple(o) for o in data["phase_offsets"]],
            phase_titles=data["phase_titles"],
            required_reads={int(k): v for k, v in data.get("required_reads", {}).items()},
            suggested_reads={int(k): v for k, v in data.get("suggested_reads", {}).items()},
            current_phase=data.get("current_phase", 0),
        )

    def get_phase_content(self, phase_idx: int, skill_md_path: Path) -> str:
        """Re-read a specific Phase's content from the SKILL.md file.

        Args:
            phase_idx: 0-based phase index.
            skill_md_path: Absolute path to the SKILL.md file.

        Returns:
            The Phase content string, or empty string if out of range.
        """
        if phase_idx < 0 or phase_idx >= self.phase_count:
            return ""

        try:
            lines = skill_md_path.read_text(encoding="utf-8").splitlines()
            start, end = self.phase_offsets[phase_idx]
            return "\n".join(lines[start:end])
        except Exception as e:
            logger.warning(
                "Failed to read phase %d from %s: %s",
                phase_idx, skill_md_path, e,
            )
            return ""

    def get_global_preamble(self, skill_md_path: Path) -> str:
        """Re-read global preamble (content before first Phase)."""
        if not self.phase_offsets:
            return ""
        try:
            lines = skill_md_path.read_text(encoding="utf-8").splitlines()
            first_phase_start = self.phase_offsets[0][0]
            return "\n".join(lines[:first_phase_start])
        except Exception as e:
            logger.warning("Failed to read preamble from %s: %s", skill_md_path, e)
            return ""

    def get_global_constraints(self, expanded_content: str) -> str:
        """Extract global constraints from expanded content."""
        _, constraints = _extract_global_constraints(expanded_content)
        return constraints


# ═══════════════════════════════════════════════════════════════════
# Parsing functions
# ═══════════════════════════════════════════════════════════════════


def _extract_global_constraints(content: str) -> Tuple[str, str]:
    """Separate '## 全局约束' section from the rest of the content.

    Args:
        content: Full expanded SKILL.md content.

    Returns:
        Tuple of (content_without_constraints, constraints_section).
        If no constraints section found, returns (original_content, "").
    """
    match = _GLOBAL_CONSTRAINTS_RE.search(content)
    if match is None:
        return content, ""

    constraints_start = match.start()
    content_before = content[:constraints_start].rstrip()
    constraints = content[constraints_start:]

    return content_before, constraints


def _parse_read_markers(content: str) -> Tuple[List[str], List[str]]:
    """Extract required (⚠️) and suggested (📖) file read markers from content.

    Args:
        content: Phase block content text.

    Returns:
        Tuple of (required_reads, suggested_reads) — lists of file paths.
    """
    required = _REQUIRED_READ_RE.findall(content)
    suggested = _SUGGESTED_READ_RE.findall(content)

    # Deduplicate while preserving order
    seen = set()
    unique_required = []
    for path in required:
        if path not in seen:
            seen.add(path)
            unique_required.append(path)

    unique_suggested = []
    for path in suggested:
        if path not in seen:  # don't include paths already in required
            seen.add(path)
            unique_suggested.append(path)

    return unique_required, unique_suggested


def parse_skill_phases(expanded_content: str) -> Optional[SkillPhaseParseResult]:
    """Parse SKILL.md expanded content into Phase blocks.

    Splits content at `### Phase N:` markers and extracts:
    - Global preamble (before first Phase)
    - Global constraints (## 全局约束 section)
    - Per-phase content blocks
    - Per-phase required/suggested file reads

    Args:
        expanded_content: The fully expanded SKILL.md content.

    Returns:
        SkillPhaseParseResult if Phase markers found, None otherwise (fallback).
    """
    lines = expanded_content.splitlines()

    # Find all Phase header positions
    phase_starts: List[Tuple[int, int, str]] = []  # (line_idx, phase_number, title)
    for i, line in enumerate(lines):
        m = _PHASE_HEADER_RE.match(line)
        if m:
            phase_starts.append((i, int(m.group(1)), m.group(2).strip()))

    if not phase_starts:
        return None  # No Phase markers → fallback to full expansion

    # Extract global constraints first (operates on full content)
    content_without_constraints, global_constraints = _extract_global_constraints(expanded_content)

    # Re-split lines from content without constraints for accurate offsets
    # But we need offsets relative to original content for re-reading
    # So we work with original lines but exclude constraint lines from phase content

    # Find where global constraints start in line numbers
    constraints_start_line = len(lines)
    if global_constraints:
        gc_match = _GLOBAL_CONSTRAINTS_RE.search(expanded_content)
        if gc_match:
            constraints_start_line = expanded_content[:gc_match.start()].count("\n")

    # Global preamble: everything before the first Phase header
    preamble_end = phase_starts[0][0]
    global_preamble = "\n".join(lines[:preamble_end]).strip()

    # Build Phase blocks
    phases: List[PhaseBlock] = []
    required_reads: Dict[int, List[str]] = {}
    suggested_reads: Dict[int, List[str]] = {}

    for idx, (start_line, phase_num, title) in enumerate(phase_starts):
        # End line: start of next phase, or constraints start, or end of file
        if idx + 1 < len(phase_starts):
            end_line = phase_starts[idx + 1][0]
        else:
            end_line = min(constraints_start_line, len(lines))

        content = "\n".join(lines[start_line:end_line]).strip()

        phases.append(PhaseBlock(
            phase_number=phase_num,
            title=title,
            content=content,
            start_line=start_line,
            end_line=end_line,
        ))

        # Parse read markers for this phase
        req, sug = _parse_read_markers(content)
        if req:
            required_reads[idx] = req
        if sug:
            suggested_reads[idx] = sug

    return SkillPhaseParseResult(
        global_preamble=global_preamble,
        global_constraints=global_constraints,
        phases=phases,
        required_reads=required_reads,
        suggested_reads=suggested_reads,
    )


def build_phase_cache(
    skill_name: str,
    skill_base_dir: str,
    parse_result: SkillPhaseParseResult,
) -> SkillPhaseCache:
    """Build a SkillPhaseCache from a parse result.

    Args:
        skill_name: Name of the skill.
        skill_base_dir: Absolute path to skill directory.
        parse_result: Output from parse_skill_phases().

    Returns:
        A SkillPhaseCache ready for session_state storage.
    """
    return SkillPhaseCache(
        skill_name=skill_name,
        skill_base_dir=skill_base_dir,
        phase_count=len(parse_result.phases),
        phase_offsets=[
            (p.start_line, p.end_line) for p in parse_result.phases
        ],
        phase_titles=[p.title for p in parse_result.phases],
        required_reads=parse_result.required_reads,
        suggested_reads=parse_result.suggested_reads,
        current_phase=0,
    )
