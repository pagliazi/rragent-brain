"""
/evolve command — manually trigger evolution pipeline.

Usage:
  /evolve status         — show evolution engine status
  /evolve run            — trigger one evolution check cycle
  /evolve gepa           — trigger daily GEPA pipeline
  /evolve skills         — list auto-created skills
  /evolve prune          — prune low-confidence system memories
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rragent.evolution.engine import EvolutionEngine
    from rragent.evolution.gepa_pipeline import GEPAPipeline
    from rragent.evolution.skill_creator import SkillCreator
    from rragent.context.memory.tier3_system import SystemMemory

logger = logging.getLogger("rragent.commands.evolve")


class EvolveCommand:
    """Handle /evolve slash command."""

    name = "evolve"
    description = "Control the evolution and self-learning systems"
    usage = "/evolve [status|run|gepa|skills|prune]"

    def __init__(
        self,
        evolution_engine: EvolutionEngine | None = None,
        gepa_pipeline: GEPAPipeline | None = None,
        skill_creator: SkillCreator | None = None,
        system_memory: SystemMemory | None = None,
    ):
        self._engine = evolution_engine
        self._gepa = gepa_pipeline
        self._skill_creator = skill_creator
        self._memory = system_memory

    async def execute(self, args: str) -> str:
        """Execute the evolve command."""
        subcommand = args.strip().split()[0] if args.strip() else "status"

        handlers = {
            "status": self._status,
            "run": self._run_cycle,
            "gepa": self._run_gepa,
            "skills": self._list_skills,
            "prune": self._prune_memory,
        }

        handler = handlers.get(subcommand)
        if not handler:
            return f"Unknown subcommand: {subcommand}\n\nUsage: {self.usage}"

        return await handler()

    async def _status(self) -> str:
        """Show evolution engine status."""
        parts = ["## Evolution System Status\n"]

        if self._engine:
            stats = self._engine.stats
            parts.append(
                f"### Evolution Engine\n"
                f"- Running: {stats.get('running', False)}\n"
                f"- Checks: {stats.get('checks', 0)}\n"
                f"- Patterns found: {stats.get('patterns_found', 0)}\n"
                f"- Skills created: {stats.get('skills_created', 0)}\n"
                f"- Failures found: {stats.get('failures_found', 0)}\n"
                f"- Recipes created: {stats.get('recipes_created', 0)}\n"
                f"- Errors: {stats.get('errors', 0)}\n"
                f"- Circuit breaker: {stats.get('circuit_breaker', {})}"
            )
        else:
            parts.append("### Evolution Engine: not initialized")

        if self._gepa:
            results = self._gepa.results
            parts.append(
                f"\n### GEPA Pipeline\n"
                f"- Total optimizations: {len(results)}\n"
                f"- Deployed: {sum(1 for r in results if r.deployed)}"
            )

        if self._skill_creator:
            created = self._skill_creator.created_skills
            parts.append(
                f"\n### Skill Creator\n"
                f"- Auto-created skills: {len(created)}"
            )

        if self._memory:
            stats = self._memory.stats
            parts.append(
                f"\n### System Memory\n"
                f"- Entries: {stats.get('total_entries', 0)}\n"
                f"- Categories: {stats.get('categories', {})}"
            )

        return "\n".join(parts)

    async def _run_cycle(self) -> str:
        """Trigger one evolution check cycle."""
        if not self._engine:
            return "Evolution engine not available"

        try:
            await self._engine._check_cycle()
            return "Evolution check cycle completed. Use `/evolve status` to see results."
        except Exception as e:
            return f"Evolution cycle failed: {e}"

    async def _run_gepa(self) -> str:
        """Trigger GEPA daily pipeline."""
        if not self._gepa:
            return "GEPA pipeline not available (Hermes runtime required)"

        results = await self._gepa.daily_evolution()

        if not results:
            return "GEPA: not enough traces for optimization (need 10+)"

        lines = ["## GEPA Evolution Results\n"]
        for r in results:
            lines.append(
                f"- **{r.target}**: "
                f"score {r.original_score:.2f} -> {r.optimized_score:.2f} "
                f"({r.improvement:+.1%}) "
                f"{'[DEPLOYED]' if r.deployed else '[not deployed]'}\n"
                f"  {r.details}"
            )

        return "\n".join(lines)

    async def _list_skills(self) -> str:
        """List auto-created skills."""
        if not self._skill_creator:
            return "Skill creator not available"

        skills = self._skill_creator.created_skills
        if not skills:
            return "No auto-created skills yet"

        lines = ["## Auto-Created Skills\n"]
        for s in skills:
            import time as t
            created = t.strftime("%Y-%m-%d %H:%M", t.localtime(s.created_at))
            lines.append(
                f"- **{s.name}** ({s.source}, {created})\n"
                f"  Path: {s.path}\n"
                f"  Scan passed: {s.scan_passed}"
            )

        return "\n".join(lines)

    async def _prune_memory(self) -> str:
        """Prune low-confidence system memories."""
        if not self._memory:
            return "System memory not available"

        pruned = self._memory.prune()
        self._memory.update_index()
        return f"Pruned {pruned} low-confidence memory entries. Index updated."
