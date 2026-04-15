"""
Autoresearch Loop — strategy optimization via keep/discard experiments.

Reference: pagliazi/autoresearch (Karpathy pattern)

Loop:
1. Human writes strategy direction (program.md pattern)
2. Agent modifies strategy code
3. Run backtest
4. Evaluate (sharpe, drawdown, return)
5. If improved: keep (git commit); else: discard (git reset)
6. Record to results.tsv
7. Repeat

Also used for prompt optimization (SOUL.md).
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rragent.tools.hermes.runtime import HermesNativeRuntime
    from rragent.tools.pyagent.bridge import PyAgentBridge

logger = logging.getLogger("rragent.evolution.autoresearch")


@dataclass
class ExperimentResult:
    """Result of a single experiment."""

    experiment_id: int
    description: str
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    annual_return: float = 0.0
    status: str = ""  # "kept" | "discarded" | "error"
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)


class StrategyResearchLoop:
    """
    Autoresearch-style strategy optimization loop.

    Uses git as experiment tracking:
    - Each successful improvement = git commit
    - Each failed experiment = git reset
    - results.tsv tracks all experiments

    Requires:
    - A git-initialized strategy directory
    - Hermes runtime for code modifications
    - PyAgent bridge for running backtests
    """

    EXPERIMENTS_DIR = Path.home() / ".rragent" / "experiments" / "strategies"

    def __init__(
        self,
        hermes_runtime: HermesNativeRuntime | None = None,
        pyagent_bridge: PyAgentBridge | None = None,
        experiments_dir: str | Path | None = None,
    ):
        self._hermes = hermes_runtime
        self._pyagent = pyagent_bridge
        self.experiments_dir = (
            Path(experiments_dir) if experiments_dir else self.EXPERIMENTS_DIR
        )
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        self._results: list[ExperimentResult] = []
        self._running = False

    async def run_experiment_loop(
        self,
        strategy_path: str,
        improvement_threshold: float = 0.05,
        max_experiments: int = 100,
        backtest_period: str = "2024-01-01:2025-12-31",
    ) -> list[ExperimentResult]:
        """
        Run the experiment loop.

        Args:
            strategy_path: Path to strategy.py file
            improvement_threshold: Min sharpe improvement to keep
            max_experiments: Max experiments to run
            backtest_period: Backtest date range
        """
        if not self._hermes or not self._hermes.available:
            logger.error("Hermes runtime required for autoresearch")
            return []

        self._running = True
        strategy = Path(strategy_path)

        # Ensure git repo
        await self._ensure_git(strategy.parent)

        # 1. Establish baseline
        logger.info("Running baseline backtest...")
        baseline = await self._run_backtest(str(strategy), backtest_period)
        if baseline is None:
            logger.error("Baseline backtest failed")
            return []

        best_sharpe = baseline.sharpe_ratio
        self._results.append(ExperimentResult(
            experiment_id=0,
            description="baseline",
            sharpe_ratio=baseline.sharpe_ratio,
            max_drawdown=baseline.max_drawdown,
            annual_return=baseline.annual_return,
            status="baseline",
        ))

        logger.info(f"Baseline: sharpe={best_sharpe:.2f}")

        for i in range(1, max_experiments + 1):
            if not self._running:
                break

            start_time = time.time()

            # 2. Agent proposes and implements modification
            modification = await self._hermes.run_task(
                prompt=(
                    f"You are optimizing a trading strategy. "
                    f"Current best Sharpe ratio: {best_sharpe:.2f}.\n"
                    f"Strategy file: {strategy}\n\n"
                    f"Propose ONE specific improvement and modify the file. "
                    f"Focus on: parameter tuning, entry/exit conditions, "
                    f"position sizing, or factor combinations.\n"
                    f"Explain your change briefly."
                ),
                toolsets=["core", "file"],
                max_iterations=10,
                quiet_mode=True,
            )

            if not modification.success:
                self._results.append(ExperimentResult(
                    experiment_id=i,
                    description="modification_failed",
                    status="error",
                    duration_s=time.time() - start_time,
                ))
                continue

            # 3. Run backtest
            result = await self._run_backtest(str(strategy), backtest_period)
            if result is None:
                await self._git_reset(strategy.parent)
                self._results.append(ExperimentResult(
                    experiment_id=i,
                    description=modification.output[:200],
                    status="error",
                    duration_s=time.time() - start_time,
                ))
                continue

            duration = time.time() - start_time

            # 4. Keep or discard
            if result.sharpe_ratio > best_sharpe + improvement_threshold:
                await self._git_commit(
                    strategy.parent,
                    f"Experiment #{i}: sharpe {result.sharpe_ratio:.2f} "
                    f"(+{result.sharpe_ratio - best_sharpe:.2f})",
                )
                best_sharpe = result.sharpe_ratio
                status = "kept"
                logger.info(
                    f"Experiment #{i} KEPT: sharpe={result.sharpe_ratio:.2f} "
                    f"(+{result.sharpe_ratio - best_sharpe:.2f})"
                )
            else:
                await self._git_reset(strategy.parent)
                status = "discarded"
                logger.info(
                    f"Experiment #{i} DISCARDED: sharpe={result.sharpe_ratio:.2f}"
                )

            self._results.append(ExperimentResult(
                experiment_id=i,
                description=modification.output[:200],
                sharpe_ratio=result.sharpe_ratio,
                max_drawdown=result.max_drawdown,
                annual_return=result.annual_return,
                status=status,
                duration_s=duration,
            ))

            # Update results file
            self._save_results_tsv()

        return self._results

    def stop(self):
        """Stop the experiment loop."""
        self._running = False

    async def _run_backtest(
        self,
        strategy_path: str,
        period: str,
    ) -> ExperimentResult | None:
        """Run backtest via PyAgent bridge."""
        if not self._pyagent:
            return None

        try:
            result = await self._pyagent.call_agent(
                agent="backtest",
                action="run",
                params={
                    "strategy": strategy_path,
                    "period": period,
                },
                timeout=300,
            )

            if not result or "error" in str(result).lower():
                return None

            # Parse backtest result
            data = result if isinstance(result, dict) else {}
            return ExperimentResult(
                experiment_id=0,
                description="",
                sharpe_ratio=float(data.get("sharpe_ratio", 0)),
                max_drawdown=float(data.get("max_drawdown", 0)),
                annual_return=float(data.get("annual_return", 0)),
            )
        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            return None

    async def _ensure_git(self, directory: Path):
        """Ensure directory is a git repo."""
        git_dir = directory / ".git"
        if not git_dir.exists():
            proc = await asyncio.create_subprocess_exec(
                "git", "init",
                cwd=str(directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()

    async def _git_commit(self, directory: Path, message: str):
        """Git add + commit."""
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=str(directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", message,
            cwd=str(directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

    async def _git_reset(self, directory: Path):
        """Git reset to discard changes."""
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", ".",
            cwd=str(directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

    def _save_results_tsv(self):
        """Save results to TSV file."""
        tsv_path = self.experiments_dir / "results.tsv"
        with open(tsv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow([
                "experiment_id", "sharpe", "drawdown", "return",
                "status", "duration_s", "description",
            ])
            for r in self._results:
                writer.writerow([
                    r.experiment_id,
                    f"{r.sharpe_ratio:.4f}",
                    f"{r.max_drawdown:.4f}",
                    f"{r.annual_return:.4f}",
                    r.status,
                    f"{r.duration_s:.1f}",
                    r.description[:100],
                ])

    @property
    def results(self) -> list[ExperimentResult]:
        return list(self._results)

    @property
    def best_result(self) -> ExperimentResult | None:
        kept = [r for r in self._results if r.status == "kept"]
        if not kept:
            return None
        return max(kept, key=lambda r: r.sharpe_ratio)
