"""
/research command — start an autoresearch experiment loop.

Usage:
  /research strategy.py --period 2024-01-01:2025-12-31 --max 50
  /research --stop
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rragent.evolution.autoresearch_loop import StrategyResearchLoop

logger = logging.getLogger("rragent.commands.research")


class ResearchCommand:
    """Handle /research slash command."""

    name = "research"
    description = "Start or stop a strategy optimization experiment loop"
    usage = "/research <strategy_path> [--period START:END] [--max N] [--stop]"

    def __init__(self, research_loop: StrategyResearchLoop | None = None):
        self._loop = research_loop

    async def execute(self, args: str) -> str:
        """Execute the research command."""
        parts = args.strip().split()

        if not parts:
            return self._help()

        if "--stop" in parts:
            return self._stop()

        strategy_path = parts[0]
        period = "2024-01-01:2025-12-31"
        max_experiments = 100

        for i, part in enumerate(parts):
            if part == "--period" and i + 1 < len(parts):
                period = parts[i + 1]
            elif part == "--max" and i + 1 < len(parts):
                try:
                    max_experiments = int(parts[i + 1])
                except ValueError:
                    pass

        return await self._start(strategy_path, period, max_experiments)

    async def _start(self, strategy_path: str, period: str, max_experiments: int) -> str:
        if not self._loop:
            return "Research loop not available (Hermes runtime required)"

        # Run in background
        import asyncio
        asyncio.create_task(
            self._loop.run_experiment_loop(
                strategy_path=strategy_path,
                backtest_period=period,
                max_experiments=max_experiments,
            )
        )

        return (
            f"Strategy research loop started:\n"
            f"- Strategy: {strategy_path}\n"
            f"- Period: {period}\n"
            f"- Max experiments: {max_experiments}\n\n"
            f"Use `/research --stop` to stop the loop.\n"
            f"Results will be saved to ~/.rragent/experiments/strategies/results.tsv"
        )

    def _stop(self) -> str:
        if self._loop:
            self._loop.stop()
            results = self._loop.results
            best = self._loop.best_result

            summary = f"Research loop stopped. {len(results)} experiments run.\n"
            if best:
                summary += (
                    f"Best result: Experiment #{best.experiment_id}, "
                    f"Sharpe={best.sharpe_ratio:.2f}"
                )
            return summary
        return "No research loop running"

    def _help(self) -> str:
        return (
            f"Usage: {self.usage}\n\n"
            f"Start an autoresearch experiment loop for strategy optimization.\n"
            f"Each experiment modifies the strategy, runs a backtest, and "
            f"keeps improvements or discards failures (git-based tracking).\n\n"
            f"Options:\n"
            f"  --period START:END  Backtest period (default: 2024-01-01:2025-12-31)\n"
            f"  --max N             Max experiments (default: 100)\n"
            f"  --stop              Stop running experiment loop"
        )
