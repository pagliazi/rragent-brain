"""
Skill Creator — automatic Skill generation from detected patterns.

Takes tool chain patterns from PatternDetector and creates
YAML+Markdown skill files that can be loaded by the Skill system.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rragent.evolution.pattern_detector import ToolChainPattern
    from rragent.evolution.failure_detector import FailurePattern
    from rragent.evolution.skill_guard import SkillGuard, TrustLevel
    from rragent.tools.hermes.runtime import HermesNativeRuntime

logger = logging.getLogger("rragent.evolution.skill_creator")


@dataclass
class CreatedSkill:
    """Result of skill creation."""

    name: str
    path: str
    content: str
    source: str  # "pattern" | "correction" | "manual"
    created_at: float = field(default_factory=time.time)
    scan_passed: bool = True


class SkillCreator:
    """
    Create Skills from detected patterns.

    Two creation modes:
    1. Template-based: Fill in a YAML+MD template from pattern data
    2. LLM-based: Use Hermes to generate a full skill from pattern description

    All created skills are scanned by SkillGuard before activation.
    """

    SKILLS_DIR = Path.home() / ".rragent" / "skills"
    TEMPLATE = """---
name: {name}
description: {description}
trigger: {trigger}
source: {source}
created: {created}
tools: {tools}
---

# {name}

{description}

## Steps

{steps}

## Parameters

{parameters}

## Common Pitfalls

{pitfalls}

## Verification

{verification}
"""

    def __init__(
        self,
        hermes_runtime: HermesNativeRuntime | None = None,
        skill_guard: SkillGuard | None = None,
        skills_dir: str | Path | None = None,
    ):
        self._hermes = hermes_runtime
        self._guard = skill_guard
        self.skills_dir = Path(skills_dir) if skills_dir else self.SKILLS_DIR
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._created: list[CreatedSkill] = []

    async def create_from_pattern(self, pattern: ToolChainPattern) -> CreatedSkill | None:
        """
        Create a skill from a detected tool chain pattern.

        Uses LLM if available, otherwise falls back to template.
        """
        name = self._generate_name(pattern.chain)

        # Check if skill already exists
        skill_path = self.skills_dir / f"{name}.md"
        if skill_path.exists():
            logger.info(f"Skill already exists: {name}")
            return None

        if self._hermes and self._hermes.available:
            content = await self._create_with_llm(name, pattern)
        else:
            content = self._create_from_template(name, pattern)

        if not content:
            return None

        # Security scan
        if self._guard:
            from rragent.evolution.skill_guard import TrustLevel
            scan = self._guard.scan(name, content, TrustLevel.AGENT_CREATED)
            if not scan.passed:
                logger.warning(
                    f"Skill {name} failed security scan: {scan.reason}"
                )
                return None

        # Write skill file
        skill_path.write_text(content, encoding="utf-8")
        logger.info(f"Created skill: {name} at {skill_path}")

        skill = CreatedSkill(
            name=name,
            path=str(skill_path),
            content=content,
            source="pattern",
        )
        self._created.append(skill)
        return skill

    async def create_from_failure(self, failure: FailurePattern) -> CreatedSkill | None:
        """Create a recovery skill from a detected failure pattern."""
        name = f"fix_{failure.tool}_{failure.scenario}"
        skill_path = self.skills_dir / f"{name}.md"
        if skill_path.exists():
            return None

        content = self._create_recovery_template(name, failure)

        if self._guard:
            from rragent.evolution.skill_guard import TrustLevel
            scan = self._guard.scan(name, content, TrustLevel.AGENT_CREATED)
            if not scan.passed:
                logger.warning(f"Recovery skill {name} failed security scan")
                return None

        skill_path.write_text(content, encoding="utf-8")
        logger.info(f"Created recovery skill: {name}")

        skill = CreatedSkill(
            name=name,
            path=str(skill_path),
            content=content,
            source="correction",
        )
        self._created.append(skill)
        return skill

    async def _create_with_llm(
        self,
        name: str,
        pattern: ToolChainPattern,
    ) -> str:
        """Use Hermes to generate a rich skill description."""
        prompt = f"""
Create a reusable Skill file in YAML+Markdown format.

Pattern detected ({pattern.occurrence_count} occurrences):
{pattern.describe()}

Requirements:
1. YAML frontmatter with: name, description, trigger, tools
2. Markdown body with: Steps, Parameters, Common Pitfalls, Verification
3. Steps should be specific and actionable
4. Include the exact tool calls and parameters
5. Note any order dependencies between steps

Output ONLY the skill file content (YAML frontmatter + Markdown body).
Do not include any other text.
"""
        result = await self._hermes.run_task(
            prompt=prompt,
            toolsets=["core"],
            max_iterations=5,
            quiet_mode=True,
        )

        if result.success and result.output:
            return result.output
        return ""

    def _create_from_template(
        self,
        name: str,
        pattern: ToolChainPattern,
    ) -> str:
        """Create skill from template (no LLM needed)."""
        chain_str = " -> ".join(pattern.chain)
        tools_str = ", ".join(set(pattern.chain))

        steps = []
        for i, tool in enumerate(pattern.chain, 1):
            params = pattern.common_params.get(tool, {})
            param_str = ", ".join(f"{k}={v}" for k, v in params.items())
            steps.append(f"{i}. Call `{tool}`" + (f" with {param_str}" if param_str else ""))

        parameters = []
        for tool, params in pattern.common_params.items():
            for k, v in params.items():
                parameters.append(f"- `{tool}.{k}`: default `{v}`")

        return self.TEMPLATE.format(
            name=name,
            description=f"Auto-generated skill for pattern: {chain_str}",
            trigger=f"When user requests a workflow involving: {tools_str}",
            source="evolution_engine",
            created=time.strftime("%Y-%m-%d %H:%M"),
            tools=tools_str,
            steps="\n".join(steps) or "1. (steps to be refined)",
            parameters="\n".join(parameters) or "- None detected",
            pitfalls="- Ensure all tools are available before starting\n- Check tool_search if any tool is not loaded",
            verification="- Verify final output matches expected format\n- Check for error results in tool outputs",
        )

    def _create_recovery_template(self, name: str, failure: FailurePattern) -> str:
        """Create a recovery skill from failure pattern."""
        return self.TEMPLATE.format(
            name=name,
            description=f"Recovery procedure for {failure.tool} {failure.scenario} errors",
            trigger=f"When {failure.tool} fails with: {failure.common_error[:100]}",
            source="failure_detector",
            created=time.strftime("%Y-%m-%d %H:%M"),
            tools=failure.tool,
            steps=(
                f"1. Check if error matches: {failure.common_error[:100]}\n"
                f"2. Check time correlation: {failure.time_correlation or 'none detected'}\n"
                f"3. Check cascading tools: {', '.join(failure.cascading_tools) or 'none'}\n"
                f"4. Apply recovery: adjust timeout/parameters based on scenario\n"
                f"5. Retry the operation"
            ),
            parameters=f"- Context: {failure.context_summary}",
            pitfalls=(
                f"- This error occurred {failure.occurrence_count} times\n"
                f"- Time pattern: {failure.time_correlation or 'no pattern detected'}"
            ),
            verification="- Verify the operation succeeds after recovery\n- Monitor for recurrence",
        )

    def _generate_name(self, chain: list[str]) -> str:
        """Generate a readable skill name from tool chain."""
        # Remove common prefixes
        clean = []
        for tool in chain:
            name = tool.replace("pyagent_", "").replace("hermes_", "")
            clean.append(name)

        # Join with underscores, deduplicate
        seen = set()
        parts = []
        for name in clean:
            if name not in seen:
                seen.add(name)
                parts.append(name)

        return "_".join(parts[:4])  # Max 4 parts

    @property
    def created_skills(self) -> list[CreatedSkill]:
        return list(self._created)
