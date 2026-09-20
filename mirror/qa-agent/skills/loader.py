"""
QA Agent System — Skill Loader

Scans project-level (.claude/skills/) and user-level (~/.qa-agent/skills/)
directories for SKILL.md files and returns parsed SkillDefinition objects.
Uses python-frontmatter for YAML Frontmatter parsing.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import frontmatter

logger = logging.getLogger(__name__)


_VALID_LOAD_MODES = {"default", "on-demand"}


@dataclass
class SkillDefinition:
    """Parsed definition of a single Skill from SKILL.md."""
    name: str                          # Directory name, used as tool identifier
    description: str                   # Required Frontmatter field
    prompt_template: str               # Markdown body (the expandable prompt)
    base_dir: Path                     # Absolute path to the Skill directory

    # Optional Frontmatter fields
    tools: List[str] = field(default_factory=list)
    agent: Optional[str] = None        # Bound Agent name
    paths: List[str] = field(default_factory=list)
    args: List[str] = field(default_factory=list)
    effort: int = 1                    # 1-5 scale
    when_to_use: str = ""
    load_mode: str = "on-demand"       # "default" | "on-demand"
    summary: str = ""                  # Short summary for skill-reminder

    @property
    def tool_name(self) -> str:
        """Agno tool name: skill__<skill-name>"""
        return f"skill__{self.name.replace('-', '_')}"


class SkillLoader:
    """Loads and indexes Skills from one or more directories.

    Skill discovery order (later entries shadow earlier on name conflict):
    1. User-level: ~/.qa-agent/skills/<name>/SKILL.md
    2. Project-level: .claude/skills/<name>/SKILL.md  ← highest priority
    """

    def __init__(self) -> None:
        self._skills: Dict[str, SkillDefinition] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_from_dirs(self, dirs: List[Path]) -> None:
        """Scan the given directories and load all valid Skills.

        Args:
            dirs: Ordered list of directories to scan (later dirs take priority).
        """
        for base in dirs:
            self._scan_directory(base)

        logger.info("Loaded %d skills from %d directories", len(self._skills), len(dirs))

    def get(self, name: str) -> Optional[SkillDefinition]:
        """Return a SkillDefinition by name, or None."""
        return self._skills.get(name)

    def all(self) -> List[SkillDefinition]:
        """Return all loaded SkillDefinitions."""
        return list(self._skills.values())

    def get_on_demand_skills(self) -> List[SkillDefinition]:
        """Return Skills with load_mode == 'on-demand'."""
        return [s for s in self._skills.values() if s.load_mode == "on-demand"]

    def get_default_skills(self) -> List[SkillDefinition]:
        """Return Skills with load_mode == 'default'."""
        return [s for s in self._skills.values() if s.load_mode == "default"]

    def search(self, query: str, top_k: int = 5) -> List[SkillDefinition]:
        """Search skills by weighted keyword matching.

        Scoring weights:
        - name substring match: +10
        - description substring match: +5
        - when_to_use substring match: +3
        - per-word matches in name: +2 each
        - per-word matches in description/when_to_use: +1 each

        Args:
            query: Natural-language search query.
            top_k: Maximum number of results to return.

        Returns:
            List of matching SkillDefinitions sorted by descending score.
        """
        if not query or not query.strip():
            return self.all()[:top_k]

        query_lower = query.lower().strip()
        words = [w for w in query_lower.split() if len(w) > 1]

        scored: List[tuple[int, SkillDefinition]] = []
        for skill in self._skills.values():
            score = 0
            name_lower = skill.name.lower()
            desc_lower = skill.description.lower()
            when_lower = skill.when_to_use.lower()

            # Substring match on full query
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            if query_lower in when_lower:
                score += 3

            # Per-word matching
            for word in words:
                if word in name_lower:
                    score += 2
                if word in desc_lower:
                    score += 1
                if word in when_lower:
                    score += 1

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_k]]

    def to_api_list(self) -> List[Dict]:
        """Return a JSON-serializable list for GET /skills."""
        return [
            {
                "name": s.name,
                "tool_name": s.tool_name,
                "description": s.description,
                "when_to_use": s.when_to_use,
                "tools": s.tools,
                "args": s.args,
                "effort": s.effort,
                "agent": s.agent,
                "load_mode": s.load_mode,
                "summary": s.summary,
            }
            for s in self._skills.values()
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_directory(self, base: Path) -> None:
        """Scan a single root directory for skill subdirectories."""
        if not base.exists() or not base.is_dir():
            logger.debug("Skill directory does not exist, skipping: %s", base)
            return

        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                logger.warning("Skill dir '%s' has no SKILL.md, skipping", skill_dir.name)
                continue
            skill = self._parse_skill(skill_dir, skill_md)
            if skill is not None:
                self._skills[skill.name] = skill  # later dirs override earlier

    def _parse_skill(self, skill_dir: Path, skill_md: Path) -> Optional[SkillDefinition]:
        """Parse a SKILL.md file into a SkillDefinition."""
        try:
            post = frontmatter.load(str(skill_md))
        except Exception as e:
            logger.error("Failed to parse SKILL.md at %s: %s", skill_md, e)
            return None

        metadata = post.metadata
        description = metadata.get("description", "").strip()
        if not description:
            logger.error("Skill '%s' missing required 'description' field — skipped", skill_dir.name)
            return None

        # Validate effort
        effort = metadata.get("effort", 1)
        if not isinstance(effort, int) or not (1 <= effort <= 5):
            effort = 1

        # Validate load_mode
        load_mode = metadata.get("load_mode", "on-demand")
        if load_mode not in _VALID_LOAD_MODES:
            logger.warning(
                "Skill '%s' has invalid load_mode '%s', falling back to 'on-demand'",
                skill_dir.name, load_mode,
            )
            load_mode = "on-demand"

        skill = SkillDefinition(
            name=skill_dir.name,
            description=description,
            prompt_template=post.content,
            base_dir=skill_dir.resolve(),
            tools=metadata.get("tools", []) or [],
            agent=metadata.get("agent"),
            paths=metadata.get("paths", []) or [],
            args=metadata.get("args", []) or [],
            effort=effort,
            when_to_use=metadata.get("when_to_use", ""),
            load_mode=load_mode,
            summary=metadata.get("summary", ""),
        )

        logger.debug(
            "Loaded skill '%s' (args=%s, effort=%d, load_mode=%s, bound_agent=%s)",
            skill.name, skill.args, skill.effort, skill.load_mode, skill.agent,
        )
        return skill


def _resolve_project_skills_dir(path: str) -> Path:
    """Resolve project skills dir, frozen-aware.

    In frozen mode (PyInstaller), relative paths resolve against sys._MEIPASS.
    Otherwise, they are relative to CWD (dev mode).
    """
    p = Path(path)
    if p.is_absolute():
        return p
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / p
    return p


def load_skills(
    project_skills_dir: str = ".claude/skills",
    user_skills_dir: str = "~/.qa-agent/skills",
) -> SkillLoader:
    """Convenience function: create a SkillLoader with standard dirs."""
    loader = SkillLoader()
    dirs = [
        Path(user_skills_dir).expanduser(),
        _resolve_project_skills_dir(project_skills_dir),
    ]
    loader.load_from_dirs(dirs)
    return loader


def build_skill_index_prompt(skills: List[SkillDefinition]) -> str:
    """Generate a Markdown Skill index for injection into Agent instructions.

    Lists each Skill's name, description, and when_to_use in a compact table
    so the Agent is aware of available Skills without loading full prompts.

    Args:
        skills: List of on-demand SkillDefinitions to index.

    Returns:
        Formatted Markdown string, or empty string if list is empty.
    """
    if not skills:
        return ""

    lines = [
        "\n## 可用 Skills",
        "",
        "以下 Skills 可通过用户的 / 命令激活。激活后你会在消息中收到完整的 Skill 指令。",
        "",
        "| Skill | 说明 | 使用时机 |",
        "|-------|------|----------|",
    ]
    for s in skills:
        when = s.when_to_use if s.when_to_use else "—"
        lines.append(f"| {s.name} | {s.description} | {when} |")

    return "\n".join(lines)
