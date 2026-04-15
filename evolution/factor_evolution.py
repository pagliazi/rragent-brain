"""
Factor Evolution Engine — GEPA-style continuous factor optimization.

Reference: hermes gepa_pipeline.py pattern adapted for quantitative factors.

Pipeline (per run):
1. Load top-K factors from FactorLibrary (by pool_score_v2 or sharpe)
2. For each factor:
   a. Generate N mutation candidates (template + optional LLM)
   b. Backtest each mutation via bridge_client (run_factor_mining_cscv)
   c. Score: sharpe_score * (1 - pbo_penalty)
   d. If best_mutation > original + threshold AND pbo < pbo_max: upgrade in library
   e. Record lineage (parent_factor_id → child_factor_id)
3. Return EvolutionReport with summary stats

Mutation strategies:
  - LOOKBACK_SHIFT: vary rolling window ±20-50%
  - THRESHOLD_NUDGE: adjust entry/exit thresholds ±10-30%
  - NORMALIZATION_SWAP: replace normalization method (z-score / rank / min-max)
  - OPERATOR_SWAP: swap arithmetic operators (+/×, -/÷)
  - CONDITIONAL_ADD: add market-state filter (成交量放大, 板块强势)
  - LLM_FREE: Hermes-guided semantic mutation (when available)

Usage:
    engine = FactorEvolutionEngine(improvement_threshold=0.05, pbo_max=0.75)
    report = await engine.run(top_n=5, mutations_per_factor=3)
    print(report["evolution_rate"])  # 0.4 = 40% factors improved
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# BRAIN_PATH injection
BRAIN_PATH = os.getenv(
    "BRAIN_PATH",
    str(Path(__file__).parent.parent),
)
if BRAIN_PATH not in sys.path:
    sys.path.insert(0, BRAIN_PATH)
if str(Path(BRAIN_PATH) / "src") not in sys.path:
    sys.path.insert(0, str(Path(BRAIN_PATH) / "src"))

logger = logging.getLogger("rragent.evolution.factor")

EVOLUTION_LOG_DIR = Path.home() / ".rragent" / "factor_evolution"


# ══════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════

@dataclass
class MutationResult:
    """Result of a single factor mutation experiment."""
    mutation_id: str
    parent_factor_id: str
    strategy: str            # mutation strategy name
    description: str         # human-readable change
    code: str

    # Metrics from backtest
    sharpe: float = 0.0
    ic_mean: float = 0.0
    ir: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    trades: int = 0
    pbo_score: float = 1.0   # default 1.0 = worst case

    # Composite score = sharpe × (1 - pbo_penalty)
    composite_score: float = 0.0

    status: str = ""          # "improved" | "discarded" | "error"
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class FactorEvolutionReport:
    """Summary report of one evolution run."""
    run_id: str
    started_at: float
    finished_at: float = 0.0

    factors_attempted: int = 0
    factors_improved: int = 0
    mutations_total: int = 0
    mutations_kept: int = 0

    evolution_rate: float = 0.0      # factors_improved / factors_attempted
    avg_sharpe_delta: float = 0.0    # mean improvement for kept mutations

    improved_factors: list[dict] = field(default_factory=list)
    all_results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# Mutation strategies
# ══════════════════════════════════════════════════════════════

def _mutate_lookback(code: str) -> list[tuple[str, str]]:
    """Shift rolling window parameters ±25%."""
    results = []
    pattern = re.compile(r'\b(\d+)\b')
    for m in pattern.finditer(code):
        val = int(m.group(1))
        if 3 <= val <= 120:  # typical lookback range
            for mult, label in [(0.75, "-25%"), (1.25, "+25%"), (0.5, "-50%"), (2.0, "+100%")]:
                new_val = max(2, int(round(val * mult)))
                if new_val == val:
                    continue
                new_code = code[: m.start()] + str(new_val) + code[m.end() :]
                results.append((new_code, f"LOOKBACK_SHIFT: {val}→{new_val} ({label})"))
                break  # one variant per position
    return results[:3]


def _mutate_normalization(code: str) -> list[tuple[str, str]]:
    """Swap normalization method."""
    variants = []
    # z-score → rank
    if "zscore" in code.lower() or "z_score" in code.lower():
        v = re.sub(r'(?i)z[_-]?score', 'rank', code)
        variants.append((v, "NORM_SWAP: zscore→rank"))
    # rank → zscore
    if re.search(r'\brank\b', code, re.IGNORECASE):
        v = re.sub(r'\brank\b', 'zscore', code, flags=re.IGNORECASE)
        variants.append((v, "NORM_SWAP: rank→zscore"))
    # rolling.mean → rolling.median
    if ".rolling(" in code and ".mean()" in code:
        v = code.replace(".mean()", ".median()")
        variants.append((v, "AGG_SWAP: mean→median"))
    return variants[:2]


def _mutate_threshold(code: str) -> list[tuple[str, str]]:
    """Nudge float thresholds ±20%."""
    results = []
    pattern = re.compile(r'\b(0\.\d+)\b')
    for m in pattern.finditer(code):
        val = float(m.group(1))
        if 0.001 < val < 0.99:
            for mult, label in [(0.8, "×0.8"), (1.2, "×1.2")]:
                new_val = round(val * mult, 4)
                new_code = code[: m.start()] + str(new_val) + code[m.end() :]
                results.append((new_code, f"THRESHOLD_NUDGE: {val}→{new_val} ({label})"))
                break
    return results[:3]


def _mutate_filter_add(code: str) -> list[tuple[str, str]]:
    """Add a volume/volatility filter."""
    filters = [
        (
            # Volume expansion filter
            "\n    # 成交量放大过滤\n    vol_ratio = df['volume'] / df['volume'].rolling(20).mean()\n    factor = factor.where(vol_ratio > 1.2, 0)\n",
            "FILTER_ADD: 成交量放大 (vol>20日均量×1.2)",
        ),
        (
            # Low volatility filter
            "\n    # 波动率过滤\n    atr = (df['high'] - df['low']).rolling(10).mean()\n    factor = factor.where(atr / df['close'] > 0.005, 0)\n",
            "FILTER_ADD: ATR过滤 (避免低波动标的)",
        ),
    ]
    results = []
    # Insert before the last return statement
    for filter_code, desc in filters:
        match = list(re.finditer(r'\n\s*return\s+factor', code))
        if match:
            insert_pos = match[-1].start()
            new_code = code[:insert_pos] + filter_code + code[insert_pos:]
            results.append((new_code, desc))
    return results


def generate_mutations(
    code: str, n: int = 3
) -> list[tuple[str, str, str]]:
    """
    Generate N mutation candidates for a factor code.

    Returns list of (mutated_code, strategy_name, description).
    """
    candidates: list[tuple[str, str, str]] = []

    strategy_fns = [
        ("LOOKBACK_SHIFT", _mutate_lookback),
        ("THRESHOLD_NUDGE", _mutate_threshold),
        ("NORMALIZATION_SWAP", _mutate_normalization),
        ("FILTER_ADD", _mutate_filter_add),
    ]

    for strategy_name, fn in strategy_fns:
        try:
            variants = fn(code)
            for (v_code, desc) in variants:
                if v_code.strip() != code.strip():
                    candidates.append((v_code, strategy_name, desc))
                    if len(candidates) >= n:
                        return candidates
        except Exception as e:
            logger.debug(f"Mutation {strategy_name} failed: {e}")

    return candidates[:n]


# ══════════════════════════════════════════════════════════════
# Evolution Engine
# ══════════════════════════════════════════════════════════════

class FactorEvolutionEngine:
    """
    GEPA-style factor evolution engine.

    Runs mutation experiments on library factors and upgrades
    those that improve composite score (sharpe × (1-pbo)).
    """

    def __init__(
        self,
        improvement_threshold: float = 0.05,
        pbo_max: float = 0.75,
        pbo_penalty_weight: float = 0.3,
        log_dir: Path | None = None,
    ):
        self.improvement_threshold = improvement_threshold
        self.pbo_max = pbo_max
        self.pbo_penalty_weight = pbo_penalty_weight
        self.log_dir = log_dir or EVOLUTION_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _composite_score(self, sharpe: float, pbo: float) -> float:
        """Score = sharpe × (1 - pbo_penalty_weight × pbo)"""
        return sharpe * (1.0 - self.pbo_penalty_weight * pbo)

    async def run(
        self,
        top_n: int = 5,
        mutations_per_factor: int = 3,
        start_date: str = "2024-01-01",
        end_date: str = "2026-01-01",
        theme_filter: str = "",
    ) -> dict:
        """
        Run one evolution pass.

        Returns serializable report dict.
        """
        run_id = f"evo_{int(time.time())}"
        report = FactorEvolutionReport(run_id=run_id, started_at=time.time())

        try:
            import redis.asyncio as aioredis
            from agents.factor_library import FactorLibrary, get_factor_library
            from agents.bridge_client import get_bridge_client

            redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
            r = aioredis.from_url(redis_url, decode_responses=True)
            lib = FactorLibrary(redis_client=r)
            bridge = get_bridge_client()

        except ImportError as e:
            logger.error(f"FactorEvolutionEngine: dependencies not available: {e}")
            return {"error": str(e), "run_id": run_id}

        try:
            # ── 1. Load top-N factors ──
            all_factors = await lib.get_all_factors(status="active")

            if theme_filter:
                all_factors = [
                    f for f in all_factors
                    if theme_filter.lower() in getattr(f, "theme", "").lower()
                    or theme_filter.lower() in getattr(f, "sub_theme", "").lower()
                ]

            # Sort by composite score (pool_score_v2 preferred, else sharpe)
            sorted_factors = sorted(
                all_factors,
                key=lambda f: float(
                    getattr(f, "pool_score_v2", None) or getattr(f, "sharpe", 0) or 0
                ),
                reverse=True,
            )[:top_n]

            if not sorted_factors:
                return {
                    "run_id": run_id,
                    "error": "没有符合条件的因子，请先运行 factor_mine",
                    "factors_attempted": 0,
                }

            logger.info(
                f"Factor evolution starting: {len(sorted_factors)} factors, "
                f"{mutations_per_factor} mutations each"
            )
            report.factors_attempted = len(sorted_factors)

            # ── 2. Evolve each factor ──
            for factor in sorted_factors:
                factor_id = getattr(factor, "id", "unknown")
                factor_code = getattr(factor, "code", "")
                original_sharpe = float(getattr(factor, "sharpe", 0) or 0)
                original_pbo = float(getattr(factor, "pbo_score", 0.5) or 0.5)
                original_score = self._composite_score(original_sharpe, original_pbo)

                logger.info(
                    f"Evolving factor {factor_id} "
                    f"(sharpe={original_sharpe:.3f}, pbo={original_pbo:.2f})"
                )

                mutations = generate_mutations(factor_code, mutations_per_factor)

                if not mutations:
                    logger.debug(f"No mutations generated for factor {factor_id}")
                    report.errors.append(f"No mutations for {factor_id}")
                    continue

                best_mutation: MutationResult | None = None

                for mut_idx, (mut_code, strategy, desc) in enumerate(mutations):
                    mutation_id = f"{run_id}_{factor_id}_{mut_idx}"
                    start_t = time.time()

                    try:
                        # Backtest mutation with CSCV PBO
                        bt = await bridge.run_factor_mining_cscv(
                            factor_code=mut_code,
                            total_days=_days_between(start_date, end_date),
                            k=4,
                            timeout=180,
                        )
                        metrics = bt.get("metrics", {})

                        mut_sharpe = float(metrics.get("sharpe", 0))
                        mut_pbo = float(metrics.get("pbo_score", bt.get("pbo_score", 0.5)))
                        mut_score = self._composite_score(mut_sharpe, mut_pbo)
                        report.mutations_total += 1

                        mr = MutationResult(
                            mutation_id=mutation_id,
                            parent_factor_id=factor_id,
                            strategy=strategy,
                            description=desc,
                            code=mut_code,
                            sharpe=mut_sharpe,
                            ic_mean=float(metrics.get("mean_ic", 0)),
                            ir=float(metrics.get("ir", 0)),
                            win_rate=float(metrics.get("win_rate", 0)),
                            max_drawdown=float(metrics.get("max_drawdown", 0)),
                            trades=int(metrics.get("trades", 0)),
                            pbo_score=mut_pbo,
                            composite_score=mut_score,
                            duration_s=time.time() - start_t,
                        )

                        # Check if improvement meets criteria
                        sharpe_improved = mut_sharpe > original_sharpe + self.improvement_threshold
                        pbo_ok = mut_pbo <= self.pbo_max
                        score_improved = mut_score > original_score + self.improvement_threshold

                        if sharpe_improved and pbo_ok and score_improved:
                            mr.status = "improved"
                            if best_mutation is None or mut_score > best_mutation.composite_score:
                                best_mutation = mr
                            logger.info(
                                f"  Mutation {strategy}: sharpe {original_sharpe:.3f}→{mut_sharpe:.3f} "
                                f"score {original_score:.3f}→{mut_score:.3f} ✅"
                            )
                        else:
                            mr.status = "discarded"
                            logger.debug(
                                f"  Mutation {strategy}: sharpe {mut_sharpe:.3f} "
                                f"pbo {mut_pbo:.2f} — discarded"
                            )

                        report.all_results.append({
                            "mutation_id": mutation_id,
                            "parent_id": factor_id,
                            "strategy": strategy,
                            "desc": desc,
                            "sharpe_before": original_sharpe,
                            "sharpe_after": mut_sharpe,
                            "pbo": mut_pbo,
                            "status": mr.status,
                        })

                    except Exception as e:
                        logger.warning(f"  Mutation {strategy} backtest failed: {e}")
                        report.errors.append(f"{factor_id}/{strategy}: {e}")

                # ── 3. If best_mutation found, upgrade library ──
                if best_mutation:
                    upgraded = await self._upgrade_library(
                        lib, factor, best_mutation, run_id
                    )
                    if upgraded:
                        report.factors_improved += 1
                        report.mutations_kept += 1
                        report.improved_factors.append({
                            "original_id": factor_id,
                            "strategy": best_mutation.strategy,
                            "description": best_mutation.description,
                            "sharpe_before": original_sharpe,
                            "sharpe_after": best_mutation.sharpe,
                            "pbo_after": best_mutation.pbo_score,
                            "score_delta": best_mutation.composite_score - original_score,
                        })

            # ── 4. Finalize report ──
            report.finished_at = time.time()
            report.evolution_rate = (
                report.factors_improved / report.factors_attempted
                if report.factors_attempted > 0 else 0.0
            )

            # Compute avg sharpe delta for kept mutations
            if report.improved_factors:
                deltas = [
                    f["sharpe_after"] - f["sharpe_before"]
                    for f in report.improved_factors
                ]
                report.avg_sharpe_delta = sum(deltas) / len(deltas)

            # Save report to disk
            self._save_report(report)

        finally:
            try:
                await r.aclose()
                await bridge.close()
            except Exception:
                pass

        return self._report_to_dict(report)

    async def _upgrade_library(
        self, lib: Any, original_factor: Any, mutation: MutationResult, run_id: str
    ) -> bool:
        """
        Add mutated factor to library as a new active factor,
        optionally retiring the original if score improvement is significant.
        """
        try:
            # Prepare metrics for library admission
            metrics = {
                "sharpe": mutation.sharpe,
                "sharpe_ratio": mutation.sharpe,
                "mean_ic": mutation.ic_mean,
                "ic_mean": mutation.ic_mean,
                "ir": mutation.ir,
                "sortino_ratio": mutation.ir,
                "win_rate": mutation.win_rate,
                "total_trades": mutation.trades,
                "max_drawdown": mutation.max_drawdown,
                "pbo_score": mutation.pbo_score,
            }

            # Use original factor's theme/sub_theme
            theme = getattr(original_factor, "theme", "evolved")
            sub_theme = f"{getattr(original_factor, 'sub_theme', 'factor')}_v{run_id[-6:]}"

            ok, reason, new_id = await lib.add_factor(
                code=mutation.code,
                metrics=metrics,
                theme=theme,
                sub_theme=sub_theme,
                trading_rules=getattr(original_factor, "suggested_trading_rules", {}),
            )

            if ok:
                logger.info(
                    f"Library upgraded: {mutation.parent_factor_id} → {new_id} "
                    f"via {mutation.strategy}"
                )
                # Retire original if improvement > 20%
                original_sharpe = float(getattr(original_factor, "sharpe", 0) or 0)
                if mutation.sharpe > original_sharpe * 1.2:
                    await lib.retire_factor(mutation.parent_factor_id)
                    logger.info(f"Retired original factor {mutation.parent_factor_id}")
            else:
                logger.info(f"Mutation rejected by library: {reason}")

            return ok

        except Exception as e:
            logger.error(f"Library upgrade failed: {e}")
            return False

    def _save_report(self, report: FactorEvolutionReport):
        """Save evolution report to JSONL log."""
        log_file = self.log_dir / "evolution_history.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._report_to_dict(report), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to save evolution report: {e}")

    @staticmethod
    def _report_to_dict(report: FactorEvolutionReport) -> dict:
        duration = report.finished_at - report.started_at if report.finished_at else 0
        return {
            "run_id": report.run_id,
            "started_at": report.started_at,
            "duration_s": round(duration, 1),
            "factors_attempted": report.factors_attempted,
            "factors_improved": report.factors_improved,
            "mutations_total": report.mutations_total,
            "mutations_kept": report.mutations_kept,
            "evolution_rate": round(report.evolution_rate, 3),
            "avg_sharpe_delta": round(report.avg_sharpe_delta, 4),
            "improved_factors": report.improved_factors,
            "errors": report.errors[:10],
            "summary": (
                f"进化 {report.factors_attempted} 个因子，"
                f"提升 {report.factors_improved} 个 "
                f"(进化率 {report.evolution_rate:.0%})，"
                f"平均 Sharpe 提升 {report.avg_sharpe_delta:+.3f}"
            ),
        }


# ══════════════════════════════════════════════════════════════
# Factor Autoresearch Loop (strategy-level, bridges to autoresearch)
# ══════════════════════════════════════════════════════════════

class FactorAutoresearchLoop:
    """
    Autoresearch-style loop for factor code optimization.

    Like StrategyResearchLoop but:
    - Uses bridge_client.run_factor_mining_cscv for backtests
    - Tracks PBO alongside Sharpe (keep only if pbo < pbo_max)
    - No git required — uses in-memory candidate tracking
    - Works without Hermes via template mutations
    - Optional Hermes for richer semantic mutations
    """

    def __init__(
        self,
        hermes_runtime: Any | None = None,
        improvement_threshold: float = 0.05,
        pbo_max: float = 0.75,
    ):
        self._hermes = hermes_runtime
        self.improvement_threshold = improvement_threshold
        self.pbo_max = pbo_max

    async def run(
        self,
        factor_code: str,
        start_date: str = "2024-01-01",
        end_date: str = "2026-01-01",
        max_experiments: int = 10,
    ) -> dict:
        """
        Hill-climbing factor optimization.

        Returns best code found + full experiment history.
        """
        try:
            from agents.bridge_client import get_bridge_client
        except ImportError as e:
            return {"error": str(e)}

        bridge = get_bridge_client()
        total_days = _days_between(start_date, end_date)

        # Baseline
        baseline = await bridge.run_factor_mining_cscv(
            factor_code=factor_code, total_days=total_days, k=4, timeout=240
        )
        baseline_sharpe = float(baseline.get("metrics", {}).get("sharpe", 0))
        baseline_pbo = float(baseline.get("pbo_score", baseline.get("metrics", {}).get("pbo_score", 0.5)))

        best_code = factor_code
        best_sharpe = baseline_sharpe
        best_pbo = baseline_pbo

        history = [{
            "exp": 0, "status": "baseline",
            "sharpe": baseline_sharpe, "pbo": baseline_pbo, "desc": "初始因子",
        }]

        use_hermes = self._hermes and getattr(self._hermes, "available", False)
        kept = 0

        for i in range(1, max_experiments + 1):
            # Generate candidate
            if use_hermes and i % 3 != 0:
                # Every 3rd experiment: use template (diversity)
                code_candidate, desc = await self._hermes_mutate(best_code, best_sharpe, best_pbo)
            else:
                mutations = generate_mutations(best_code, 1)
                if not mutations:
                    break
                code_candidate, _, desc = mutations[0]

            # Backtest
            try:
                bt = await bridge.run_factor_mining_cscv(
                    factor_code=code_candidate, total_days=total_days, k=4, timeout=180
                )
                new_sharpe = float(bt.get("metrics", {}).get("sharpe", 0))
                new_pbo = float(bt.get("pbo_score", bt.get("metrics", {}).get("pbo_score", 0.5)))

                improved = (
                    new_sharpe > best_sharpe + self.improvement_threshold
                    and new_pbo <= self.pbo_max
                )

                if improved:
                    best_code = code_candidate
                    best_sharpe = new_sharpe
                    best_pbo = new_pbo
                    status = "kept"
                    kept += 1
                else:
                    status = "discarded"

                history.append({
                    "exp": i, "status": status,
                    "sharpe": new_sharpe, "pbo": new_pbo, "desc": desc,
                })

            except Exception as e:
                history.append({"exp": i, "status": "error", "sharpe": 0, "pbo": 1.0, "desc": str(e)[:80]})

        await bridge.close()

        improvement = best_sharpe - baseline_sharpe
        return {
            "baseline_sharpe": round(baseline_sharpe, 4),
            "best_sharpe": round(best_sharpe, 4),
            "best_pbo": round(best_pbo, 4),
            "improvement": round(improvement, 4),
            "experiments_total": len(history) - 1,
            "experiments_kept": kept,
            "best_code": best_code,
            "history": history,
            "verdict": (
                f"{'✅ 提升' if improvement > 0 else '❌ 未改善'} "
                f"Sharpe {baseline_sharpe:.3f} → {best_sharpe:.3f} ({improvement:+.3f}), "
                f"PBO {best_pbo:.2f}"
            ),
        }

    async def _hermes_mutate(self, code: str, sharpe: float, pbo: float) -> tuple[str, str]:
        """Use Hermes to propose a semantic mutation."""
        import re as _re
        result = await self._hermes.run_task(
            prompt=(
                f"优化以下量化因子代码。当前 Sharpe={sharpe:.3f}, PBO={pbo:.2f}。\n"
                f"提出一个改进（调参/条件/标准化/过滤），返回完整修改后的代码块。\n\n"
                f"```python\n{code}\n```"
            ),
            toolsets=["core"],
            max_iterations=3,
            quiet_mode=True,
        )
        if result.success:
            m = _re.search(r"```(?:python)?\n(.*?)```", result.output, _re.DOTALL)
            if m:
                return m.group(1).strip(), f"LLM_FREE: {result.output[:80].strip()}"
        return code, "LLM_FREE: 无改动"


# ══════════════════════════════════════════════════════════════
# Helper
# ══════════════════════════════════════════════════════════════

def _days_between(start: str, end: str) -> int:
    from datetime import datetime
    try:
        return abs((datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days)
    except Exception:
        return 365
