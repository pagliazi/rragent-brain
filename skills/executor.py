"""
Skill Executor — execute loaded Skills in conversation context.

Skills are templates that guide the LLM through multi-step workflows.
Execution injects the skill body as a system message, then the LLM
follows the steps using its available tools.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .loader import Skill, SkillLoader
    from rragent.tools.registry import GlobalToolRegistry

logger = logging.getLogger("rragent.skills.executor")


@dataclass
class SkillExecution:
    """Record of a skill execution."""

    skill_name: str
    started_at: float
    completed_at: float = 0.0
    success: bool = False
    steps_completed: int = 0
    total_steps: int = 0
    error: str = ""


class SkillExecutor:
    """
    Execute Skills by injecting their instructions into the conversation.

    Skills don't execute code directly — they provide structured instructions
    that the LLM follows using its available tools. The executor:

    1. Validates that required tools are available
    2. Injects the skill body as a system-level instruction
    3. Tracks execution progress
    4. Records success/failure for Evolution Engine

    Active skills are tracked per-session and their status is included
    in the system prompt.
    """

    def __init__(
        self,
        skill_loader: SkillLoader,
        tool_registry: GlobalToolRegistry | None = None,
    ):
        self._loader = skill_loader
        self._registry = tool_registry
        self._active: dict[str, SkillExecution] = {}  # session_id -> execution
        self._history: list[SkillExecution] = []

    def prepare_skill(
        self,
        skill_name: str,
        session_id: str = "",
    ) -> str | None:
        """
        Prepare a skill for execution.

        Returns the skill instruction text to inject into the conversation,
        or None if the skill can't be activated.
        """
        skill = self._loader.get(skill_name)
        if not skill:
            logger.warning(f"Skill not found: {skill_name}")
            return None

        # Validate required tools are available
        missing = self._check_tools(skill.tools)
        if missing:
            logger.warning(
                f"Skill {skill_name} requires unavailable tools: {missing}"
            )
            # Return skill with warning rather than blocking
            warning = (
                f"\n\n> Note: Some tools required by this skill may need "
                f"to be discovered first via tool_search: {', '.join(missing)}"
            )
        else:
            warning = ""

        # Build instruction text
        instruction = (
            f"## Active Skill: {skill.name}\n\n"
            f"{skill.description}\n\n"
            f"{skill.body}"
            f"{warning}"
        )

        # Track execution
        execution = SkillExecution(
            skill_name=skill_name,
            started_at=time.time(),
            total_steps=self._count_steps(skill.body),
        )
        self._active[session_id] = execution

        return instruction

    def complete_skill(self, session_id: str, success: bool = True, error: str = ""):
        """Mark active skill as completed."""
        if session_id in self._active:
            execution = self._active.pop(session_id)
            execution.completed_at = time.time()
            execution.success = success
            execution.error = error
            self._history.append(execution)

    def get_active_skill(self, session_id: str) -> SkillExecution | None:
        """Get the currently active skill for a session."""
        return self._active.get(session_id)

    def get_active_skills_summary(self, session_id: str) -> str:
        """Get summary of active skill for system prompt injection."""
        execution = self._active.get(session_id)
        if not execution:
            return ""

        skill = self._loader.get(execution.skill_name)
        if not skill:
            return ""

        return (
            f"Currently executing skill: {skill.name}\n"
            f"Description: {skill.description}\n"
            f"Steps completed: {execution.steps_completed}/{execution.total_steps}"
        )

    def list_available(self) -> list[dict]:
        """List all available skills for slash command display."""
        skills = self._loader.list_skills()
        return [
            {
                "name": s.name,
                "description": s.description,
                "trigger": s.trigger,
                "source": s.source,
            }
            for s in skills
        ]

    def match_skill(self, user_message: str) -> str | None:
        """
        Check if a user message should trigger a skill.

        Returns skill name if matched, None otherwise.
        Matching is based on skill trigger patterns.
        """
        message_lower = user_message.lower()
        skills = self._loader.list_skills()

        for skill in skills:
            if not skill.trigger:
                continue

            # Simple substring matching on trigger keywords
            trigger_words = skill.trigger.lower().split()
            matched = sum(1 for w in trigger_words if w in message_lower)

            if matched >= len(trigger_words) * 0.6:  # 60% keyword match
                return skill.name

        return None

    def _check_tools(self, required_tools: list[str]) -> list[str]:
        """Check which required tools are not available."""
        if not self._registry or not required_tools:
            return []

        missing = []
        for tool_name in required_tools:
            if not self._registry.get_tool(tool_name):
                missing.append(tool_name)
        return missing

    def _count_steps(self, body: str) -> int:
        """Count numbered steps in skill body."""
        import re
        steps = re.findall(r'^\d+\.', body, re.MULTILINE)
        return len(steps) or 1

    @property
    def execution_history(self) -> list[SkillExecution]:
        return list(self._history)

    @property
    def stats(self) -> dict:
        total = len(self._history)
        successful = sum(1 for e in self._history if e.success)
        return {
            "total_executions": total,
            "successful": successful,
            "success_rate": successful / total if total > 0 else 0,
            "active_count": len(self._active),
        }
