"""
Quant research tools — factor research, backtest analysis, strategy optimization,
and factor evolution.

Integrates:
  - alpha_digger (exploration) + quant_pipeline (exploitation)
  - bridge_client (backtests via ReachRich sandbox)
  - factor_library (Redis-backed factor store)
  - evolution/factor_evolution (GEPA-style factor mutation loop)
  - evolution/autoresearch_loop (strategy hill-climbing)

All tools bypass PyAgent Redis for direct low-latency access.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict
from typing import Any

# ── Ensure BRAIN_PATH in sys.path so agents/* can be imported ──
BRAIN_PATH = os.getenv(
    "BRAIN_PATH",
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        )
    ),
)
if BRAIN_PATH not in sys.path:
    sys.path.insert(0, BRAIN_PATH)

from rragent.tools.base import Tool, ToolSpec, ToolResult

logger = logging.getLogger("rragent.tools.quant")


# ══════════════════════════════════════════════════════════════
# Helper: bridge client (reusable across tools)
# ══════════════════════════════════════════════════════════════

def _get_bridge():
    """Return a BridgeClient configured from env vars."""
    from agents.bridge_client import BridgeClient
    base_url = os.getenv("BRIDGE_BASE_URL", "http://127.0.0.1:8001/api/bridge")
    secret = os.getenv("BRIDGE_SECRET", "")
    token = os.getenv("REACHRICH_TOKEN", "")
    return BridgeClient(base_url=base_url, secret=secret, token=token)


def _get_factor_library():
    """Return FactorLibrary backed by REDIS_URL."""
    import redis.asyncio as aioredis
    from agents.factor_library import FactorLibrary
    r = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True
    )
    return FactorLibrary(redis_client=r), r


# ══════════════════════════════════════════════════════════════
# Tool 1: FactorResearchTool
# ══════════════════════════════════════════════════════════════

class FactorResearchTool(Tool):
    """
    主题驱动的因子研究。

    两种模式:
    - explore: 盲搜（alpha_digger），在给定主题周围随机探索新因子
    - exploit: 定向研究（quant_pipeline），针对明确主题生成并优化策略代码

    返回: 发现的因子代码、Sharpe/IC/IR 指标、入库状态
    """

    spec = ToolSpec(
        name="factor_research",
        description=(
            "主题驱动因子研究。explore 模式盲搜新因子；exploit 模式定向生成并优化策略。"
            "输入: topic（主题，如'涨停连板动量'）, mode（explore/exploit）, rounds, factors_per_round"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "研究主题，如 '涨停连板动量', '龙头溢价', '波动收缩突破'",
                },
                "mode": {
                    "type": "string",
                    "enum": ["explore", "exploit"],
                    "default": "explore",
                    "description": "explore=盲搜新因子; exploit=定向优化策略",
                },
                "rounds": {
                    "type": "integer",
                    "default": 3,
                    "description": "挖掘轮数（explore 模式有效）",
                },
                "factors_per_round": {
                    "type": "integer",
                    "default": 3,
                    "description": "每轮生成因子数",
                },
                "start_date": {
                    "type": "string",
                    "default": "2024-01-01",
                    "description": "回测起始日期",
                },
                "end_date": {
                    "type": "string",
                    "default": "2026-01-01",
                    "description": "回测结束日期",
                },
            },
            "required": ["topic"],
        },
        is_tier0=False,
        timeout=600,
        category="quant",
        keywords=["因子", "研究", "挖掘", "alpha", "explore", "exploit", "主题"],
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        topic = input.get("topic", "")
        mode = input.get("mode", "explore")
        rounds = int(input.get("rounds", 3))
        factors_per_round = int(input.get("factors_per_round", 3))
        start_date = input.get("start_date", "2024-01-01")
        end_date = input.get("end_date", "2026-01-01")

        if mode == "explore":
            return await self._explore(topic, rounds, factors_per_round, start_date, end_date)
        else:
            return await self._exploit(topic, start_date, end_date, rounds)

    # ── Explore: alpha_digger around a theme ──

    async def _explore(
        self, topic: str, rounds: int, factors_per_round: int,
        start_date: str, end_date: str,
    ) -> ToolResult:
        try:
            from agents.alpha_digger import (
                EXPLORATION_THEMES,
                run_mining_round,
            )
            from agents.llm_router import get_llm_router
            from agents.factor_library import get_factor_library

            # Find matching theme from EXPLORATION_THEMES
            matched_theme = None
            for t in EXPLORATION_THEMES:
                if topic.lower() in t.get("name", "").lower() or \
                   topic.lower() in t.get("desc", "").lower() or \
                   any(topic.lower() in h.lower() for h in t.get("hints", [])):
                    matched_theme = t
                    break

            router = get_llm_router()
            bridge = _get_bridge()
            lib = get_factor_library()

            all_results = []
            admitted_total = 0

            for i in range(rounds):
                logger.info(f"Factor research round {i+1}/{rounds}: {topic}")
                try:
                    round_result = await run_mining_round(
                        router=router,
                        bridge=bridge,
                        factor_lib=lib,
                        notify_fn=None,
                        n_factors=factors_per_round,
                    )
                    admitted_total += round_result.get("admitted", 0)
                    all_results.append({
                        "round": i + 1,
                        "generated": round_result.get("generated", 0),
                        "tested": round_result.get("tested", 0),
                        "admitted": round_result.get("admitted", 0),
                        "top_sharpe": max(
                            (f.get("metrics", {}).get("sharpe", 0)
                             for f in round_result.get("factors", [])),
                            default=0,
                        ),
                    })
                except Exception as e:
                    logger.warning(f"Round {i+1} failed: {e}")
                    all_results.append({"round": i + 1, "error": str(e)})

            await bridge.close()

            summary = {
                "topic": topic,
                "mode": "explore",
                "rounds_completed": len(all_results),
                "total_admitted": admitted_total,
                "theme_matched": matched_theme.get("name") if matched_theme else None,
                "round_results": all_results,
            }
            return ToolResult.success(json.dumps(summary, ensure_ascii=False, default=str))

        except ImportError as e:
            return ToolResult.error(f"依赖模块未就绪: {e}. 请确认 BRAIN_PATH 和 Redis 正常。")
        except Exception as e:
            return ToolResult.error(f"因子探索失败: {e}")

    # ── Exploit: quant_pipeline directed strategy ──

    async def _exploit(
        self, topic: str, start_date: str, end_date: str, max_rounds: int
    ) -> ToolResult:
        try:
            from agents.quant_pipeline import (
                ALPHA_GENERATOR_PROMPT,
                STRATEGY_OPTIMIZER_PROMPT,
                PM_PROMPT,
            )
            from agents.llm_router import get_llm_router

            router = get_llm_router()
            bridge = _get_bridge()

            # Round 1: generate initial factor/strategy
            logger.info(f"Exploit mode: generating strategy for topic '{topic}'")
            gen_prompt = ALPHA_GENERATOR_PROMPT.format(topic=topic)
            gen_reply = await router.chat(gen_prompt)
            strategy_code = _extract_code_block(gen_reply)

            if not strategy_code:
                await bridge.close()
                return ToolResult.error(f"LLM 未生成有效代码 for topic: {topic}")

            # Backtest round 1
            bt_result = await bridge.run_factor_mining(
                factor_code=strategy_code,
                start_date=start_date,
                end_date=end_date,
            )
            best_metrics = bt_result.get("metrics", {})
            best_code = strategy_code
            best_sharpe = float(best_metrics.get("sharpe", 0))

            history = [{"round": 1, "sharpe": best_sharpe, "status": "baseline"}]

            # Rounds 2+: optimizer refines
            for rnd in range(2, max_rounds + 1):
                opt_prompt = STRATEGY_OPTIMIZER_PROMPT.format(
                    topic=topic,
                    current_code=best_code,
                    metrics=json.dumps(best_metrics, ensure_ascii=False),
                    history=json.dumps(history, ensure_ascii=False),
                )
                opt_reply = await router.chat(opt_prompt)
                new_code = _extract_code_block(opt_reply)
                if not new_code:
                    continue

                bt_r = await bridge.run_factor_mining(
                    factor_code=new_code,
                    start_date=start_date,
                    end_date=end_date,
                )
                new_sharpe = float(bt_r.get("metrics", {}).get("sharpe", 0))

                if new_sharpe > best_sharpe + 0.05:
                    best_sharpe = new_sharpe
                    best_code = new_code
                    best_metrics = bt_r.get("metrics", {})
                    status = "improved"
                else:
                    status = "discarded"

                history.append({"round": rnd, "sharpe": new_sharpe, "status": status})

            await bridge.close()
            return ToolResult.success(json.dumps({
                "topic": topic,
                "mode": "exploit",
                "best_sharpe": best_sharpe,
                "best_metrics": best_metrics,
                "best_code": best_code[:500] + ("..." if len(best_code) > 500 else ""),
                "optimization_history": history,
            }, ensure_ascii=False, default=str))

        except ImportError as e:
            return ToolResult.error(f"依赖模块未就绪: {e}")
        except Exception as e:
            return ToolResult.error(f"定向研究失败: {e}")


# ══════════════════════════════════════════════════════════════
# Tool 2: BacktestAnalysisTool
# ══════════════════════════════════════════════════════════════

class BacktestAnalysisTool(Tool):
    """
    回测结果深度分析。

    输入策略/因子代码 + 回测指标，输出:
    - 质量评级 A/B/C/D
    - 过拟合风险评分 (0-10)
    - IC 衰减估计
    - PBO 样本外偏差检测
    - 具体优化建议

    集成了 backtest_agent._quant_quality_check 的评级逻辑。
    """

    spec = ToolSpec(
        name="backtest_analysis",
        description=(
            "回测结果深度分析：评级(A/B/C/D)、过拟合风险、IC衰减、PBO检测、优化建议。"
            "支持因子(factor)和策略(strategy)两种分析类型。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "因子或策略 Python 代码",
                },
                "metrics": {
                    "type": "object",
                    "description": "回测指标 dict，包含 sharpe/win_rate/trades/max_drawdown 等",
                },
                "analysis_type": {
                    "type": "string",
                    "enum": ["factor", "strategy"],
                    "default": "factor",
                    "description": "分析类型: factor=因子, strategy=策略",
                },
                "run_cscv": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否运行 CSCV PBO 检测（较慢，需要回测服务）",
                },
                "start_date": {
                    "type": "string",
                    "default": "2024-01-01",
                },
                "end_date": {
                    "type": "string",
                    "default": "2026-01-01",
                },
            },
            "required": ["metrics"],
        },
        is_tier0=False,
        timeout=300,
        category="quant",
        keywords=["分析", "评级", "过拟合", "PBO", "IC", "回测", "质量"],
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        code = input.get("code", "")
        metrics = input.get("metrics", {})
        analysis_type = input.get("analysis_type", "factor")
        run_cscv = input.get("run_cscv", False)
        start_date = input.get("start_date", "2024-01-01")
        end_date = input.get("end_date", "2026-01-01")

        if not metrics:
            return ToolResult.error("metrics 不能为空")

        # ── Grade + Risk Score ──
        grade, risk_score, warnings, recommendations = _quant_grade(
            metrics, code, analysis_type
        )

        # ── IC Decay Estimate ──
        ic_analysis = _analyze_ic(metrics)

        # ── PBO Check (optional, requires bridge) ──
        pbo_result = None
        if run_cscv and code:
            try:
                bridge = _get_bridge()
                pbo_result = await bridge.run_factor_mining_cscv(
                    factor_code=code,
                    total_days=int((
                        _days_between(start_date, end_date)
                    )),
                    k=4,
                    timeout=240,
                )
                await bridge.close()
            except Exception as e:
                logger.warning(f"CSCV PBO check failed: {e}")

        # ── Factor-specific: check decay halflife ──
        decay_analysis = {}
        if analysis_type == "factor":
            ic_mean = float(metrics.get("mean_ic", metrics.get("ic_mean", 0)))
            ir = float(metrics.get("ir", 0))
            if ic_mean > 0:
                # Estimate effective horizon from IC/IR ratio
                effective_horizon = max(1, round(ir / max(ic_mean, 0.001)))
                decay_analysis = {
                    "estimated_ic_horizon_days": effective_horizon,
                    "ic_stability": "稳定" if ir > 1.5 else "一般" if ir > 0.8 else "不稳定",
                    "holding_suggestion": f"建议持有 {effective_horizon}-{effective_horizon*2} 天",
                }

        # ── Composite Result ──
        result = {
            "grade": grade,
            "risk_score": risk_score,
            "analysis_type": analysis_type,
            "metrics_summary": {
                "sharpe": metrics.get("sharpe", metrics.get("sharpe_ratio", "N/A")),
                "win_rate": metrics.get("win_rate", "N/A"),
                "trades": metrics.get("trades", metrics.get("total_trades", "N/A")),
                "max_drawdown": metrics.get("max_drawdown", "N/A"),
                "ic_mean": metrics.get("mean_ic", metrics.get("ic_mean", "N/A")),
                "ir": metrics.get("ir", "N/A"),
            },
            "ic_analysis": ic_analysis,
            "decay_analysis": decay_analysis,
            "warnings": warnings,
            "recommendations": recommendations,
            "verdict": _grade_verdict(grade, risk_score),
        }

        if pbo_result:
            result["pbo_check"] = {
                "pbo_score": pbo_result.get("pbo_score", "N/A"),
                "status": pbo_result.get("status", "N/A"),
                "interpretation": _interpret_pbo(pbo_result.get("pbo_score", 1.0)),
            }

        return ToolResult.success(json.dumps(result, ensure_ascii=False, default=str))


# ══════════════════════════════════════════════════════════════
# Tool 3: StrategyOptimizeTool
# ══════════════════════════════════════════════════════════════

class StrategyOptimizeTool(Tool):
    """
    策略自动优化 — 结合 autoresearch 爬山算法。

    两种优化路径:
    1. 参数扫描（无需 Hermes）: 提取代码中的数值参数，±10%/±20% 变体回测比较
    2. LLM 引导（有 Hermes 时）: Hermes 理解代码语义后提出改进方案

    每次实验: 修改代码 → 回测 → sharpe 提升 > threshold → 保留
    返回: 最佳 Sharpe、优化历史、改进后的代码
    """

    spec = ToolSpec(
        name="strategy_optimize",
        description=(
            "策略自动优化（autoresearch 爬山算法）。自动尝试参数变体和结构改进，"
            "返回最优 Sharpe 版本。支持纯参数扫描（快速）和 LLM 引导优化（精准）。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "需要优化的策略或因子 Python 代码",
                },
                "start_date": {
                    "type": "string",
                    "default": "2024-01-01",
                },
                "end_date": {
                    "type": "string",
                    "default": "2026-01-01",
                },
                "max_experiments": {
                    "type": "integer",
                    "default": 8,
                    "description": "最大实验次数（参数扫描模式）",
                },
                "improvement_threshold": {
                    "type": "number",
                    "default": 0.05,
                    "description": "Sharpe 提升阈值，超过才保留",
                },
                "mode": {
                    "type": "string",
                    "enum": ["param_scan", "llm_guided", "auto"],
                    "default": "auto",
                    "description": "优化模式: param_scan=参数扫描, llm_guided=LLM引导, auto=自动选择",
                },
                "code_type": {
                    "type": "string",
                    "enum": ["factor", "strategy"],
                    "default": "factor",
                },
            },
            "required": ["code"],
        },
        is_tier0=False,
        timeout=900,
        category="quant",
        keywords=["优化", "策略", "autoresearch", "爬山", "参数扫描", "提升"],
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        code = input.get("code", "")
        start_date = input.get("start_date", "2024-01-01")
        end_date = input.get("end_date", "2026-01-01")
        max_experiments = int(input.get("max_experiments", 8))
        threshold = float(input.get("improvement_threshold", 0.05))
        mode = input.get("mode", "auto")
        code_type = input.get("code_type", "factor")

        if not code.strip():
            return ToolResult.error("code 不能为空")

        try:
            bridge = _get_bridge()

            # ── Step 1: Baseline backtest ──
            logger.info("StrategyOptimize: running baseline backtest")
            if code_type == "factor":
                baseline = await bridge.run_factor_mining(
                    factor_code=code, start_date=start_date, end_date=end_date
                )
            else:
                baseline = await bridge.run_backtest(
                    code=code, start_date=start_date, end_date=end_date
                )

            baseline_sharpe = float(
                baseline.get("metrics", {}).get("sharpe",
                baseline.get("metrics", {}).get("sharpe_ratio", 0))
            )

            if baseline_sharpe == 0:
                await bridge.close()
                return ToolResult.error(
                    f"基准回测失败或 Sharpe=0。回测返回: {json.dumps(baseline, ensure_ascii=False)[:300]}"
                )

            logger.info(f"Baseline sharpe: {baseline_sharpe:.3f}")

            best_code = code
            best_sharpe = baseline_sharpe
            best_metrics = baseline.get("metrics", {})
            history = [{"exp": 0, "sharpe": baseline_sharpe, "status": "baseline", "desc": "初始基准"}]

            # ── Step 2: Determine optimization mode ──
            use_llm = False
            if mode == "llm_guided":
                use_llm = True
            elif mode == "auto":
                # Try hermes if available
                try:
                    from rragent.tools.hermes.runtime import HermesNativeRuntime
                    _rt = HermesNativeRuntime()
                    use_llm = _rt.available
                except Exception:
                    use_llm = False

            if use_llm:
                result = await self._llm_optimize(
                    bridge, best_code, best_sharpe, best_metrics,
                    start_date, end_date, max_experiments, threshold,
                    code_type, history
                )
            else:
                result = await self._param_scan_optimize(
                    bridge, best_code, best_sharpe, best_metrics,
                    start_date, end_date, max_experiments, threshold,
                    code_type, history
                )

            await bridge.close()
            return result

        except ImportError as e:
            return ToolResult.error(f"依赖未就绪: {e}")
        except Exception as e:
            return ToolResult.error(f"策略优化失败: {e}")

    async def _param_scan_optimize(
        self, bridge, best_code, best_sharpe, best_metrics,
        start_date, end_date, max_experiments, threshold,
        code_type, history
    ) -> ToolResult:
        """Parameter grid scan: extract numbers from code, try ±10%/±20% variants."""

        candidates = _generate_param_variants(best_code, max_experiments)
        kept = 0

        for i, (variant_code, desc) in enumerate(candidates):
            try:
                if code_type == "factor":
                    bt = await bridge.run_factor_mining(
                        factor_code=variant_code, start_date=start_date, end_date=end_date
                    )
                else:
                    bt = await bridge.run_backtest(
                        code=variant_code, start_date=start_date, end_date=end_date
                    )

                new_sharpe = float(
                    bt.get("metrics", {}).get("sharpe",
                    bt.get("metrics", {}).get("sharpe_ratio", 0))
                )

                if new_sharpe > best_sharpe + threshold:
                    best_sharpe = new_sharpe
                    best_code = variant_code
                    best_metrics = bt.get("metrics", {})
                    status = "kept"
                    kept += 1
                else:
                    status = "discarded"

                history.append({"exp": i + 1, "sharpe": new_sharpe, "status": status, "desc": desc})
                logger.info(f"Param scan exp {i+1}: sharpe={new_sharpe:.3f} [{status}] — {desc}")

            except Exception as e:
                logger.warning(f"Param scan exp {i+1} failed: {e}")
                history.append({"exp": i + 1, "sharpe": 0, "status": "error", "desc": str(e)[:80]})

        return self._format_result(best_code, best_sharpe, best_metrics, history, "param_scan", kept)

    async def _llm_optimize(
        self, bridge, best_code, best_sharpe, best_metrics,
        start_date, end_date, max_experiments, threshold,
        code_type, history
    ) -> ToolResult:
        """LLM-guided optimization via Hermes."""
        from rragent.tools.hermes.runtime import HermesNativeRuntime
        hermes = HermesNativeRuntime()
        kept = 0

        for i in range(1, max_experiments + 1):
            prompt = (
                f"优化以下{'因子' if code_type == 'factor' else '策略'}代码。"
                f"当前最优 Sharpe: {best_sharpe:.3f}\n"
                f"当前指标: {json.dumps(best_metrics, ensure_ascii=False)}\n\n"
                f"代码:\n```python\n{best_code}\n```\n\n"
                f"提出一个具体改进（参数调整、条件优化、过滤逻辑），返回完整修改后的代码。"
                f"只返回代码块，不要多余解释。"
            )
            hermes_result = await hermes.run_task(
                prompt=prompt, toolsets=["core"], max_iterations=5, quiet_mode=True
            )
            if not hermes_result.success:
                history.append({"exp": i, "sharpe": 0, "status": "error", "desc": "Hermes 修改失败"})
                continue

            new_code = _extract_code_block(hermes_result.output) or hermes_result.output

            try:
                if code_type == "factor":
                    bt = await bridge.run_factor_mining(
                        factor_code=new_code, start_date=start_date, end_date=end_date
                    )
                else:
                    bt = await bridge.run_backtest(
                        code=new_code, start_date=start_date, end_date=end_date
                    )

                new_sharpe = float(
                    bt.get("metrics", {}).get("sharpe",
                    bt.get("metrics", {}).get("sharpe_ratio", 0))
                )

                if new_sharpe > best_sharpe + threshold:
                    best_sharpe = new_sharpe
                    best_code = new_code
                    best_metrics = bt.get("metrics", {})
                    status = "kept"
                    kept += 1
                else:
                    status = "discarded"

                desc = hermes_result.output[:120].strip().replace("\n", " ")
                history.append({"exp": i, "sharpe": new_sharpe, "status": status, "desc": desc})

            except Exception as e:
                history.append({"exp": i, "sharpe": 0, "status": "error", "desc": str(e)[:80]})

        return self._format_result(best_code, best_sharpe, best_metrics, history, "llm_guided", kept)

    @staticmethod
    def _format_result(code, sharpe, metrics, history, mode, kept) -> ToolResult:
        baseline = next((h for h in history if h["status"] == "baseline"), {})
        baseline_sharpe = baseline.get("sharpe", 0)
        improvement = sharpe - baseline_sharpe

        return ToolResult.success(json.dumps({
            "optimization_mode": mode,
            "baseline_sharpe": round(baseline_sharpe, 4),
            "best_sharpe": round(sharpe, 4),
            "improvement": round(improvement, 4),
            "improvement_pct": f"{improvement / max(abs(baseline_sharpe), 0.01) * 100:.1f}%",
            "experiments_kept": kept,
            "experiments_total": len(history) - 1,
            "best_metrics": metrics,
            "best_code_preview": code[:600] + ("..." if len(code) > 600 else ""),
            "history": history,
            "verdict": (
                f"{'✅ 显著提升' if improvement > 0.1 else '⚠️ 小幅提升' if improvement > 0 else '❌ 未能改善'} "
                f"Sharpe {baseline_sharpe:.3f} → {sharpe:.3f} ({improvement:+.3f})"
            ),
        }, ensure_ascii=False, default=str))


# ══════════════════════════════════════════════════════════════
# Tool 4: FactorEvolveTool
# ══════════════════════════════════════════════════════════════

class FactorEvolveTool(Tool):
    """
    因子进化 — GEPA 遗传算法驱动因子库持续优化。

    流程:
    1. 从因子库加载 top-N 因子（按 pool_score_v2 或 sharpe）
    2. 对每个因子生成变异候选（模板变异 + 可选 LLM 变异）
    3. 通过 bridge_client 回测变异版本（含 CSCV PBO 检测）
    4. 若变异 sharpe > 原始 + threshold 且 pbo < 0.75: 升级因子库
    5. 追踪变异谱系（parent_factor_id）

    适合: 定期运行（日级进化）或手动触发特定主题因子优化
    """

    spec = ToolSpec(
        name="factor_evolve",
        description=(
            "因子进化优化（GEPA 遗传算法）。对库中 top-N 因子生成变异并回测，"
            "若提升则自动升级因子库。返回进化报告：提升率、最优变异、谱系追踪。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "default": 5,
                    "description": "进化的顶级因子数量",
                },
                "mutations_per_factor": {
                    "type": "integer",
                    "default": 3,
                    "description": "每个因子生成的变异数",
                },
                "improvement_threshold": {
                    "type": "number",
                    "default": 0.05,
                    "description": "Sharpe 提升阈值",
                },
                "pbo_max": {
                    "type": "number",
                    "default": 0.75,
                    "description": "允许的最大 PBO 过拟合概率",
                },
                "start_date": {
                    "type": "string",
                    "default": "2024-01-01",
                },
                "end_date": {
                    "type": "string",
                    "default": "2026-01-01",
                },
                "theme_filter": {
                    "type": "string",
                    "default": "",
                    "description": "只进化特定主题的因子（空=全部）",
                },
            },
        },
        is_tier0=False,
        timeout=1200,
        category="quant",
        keywords=["进化", "因子", "GEPA", "遗传", "变异", "优化", "迭代"],
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        top_n = int(input.get("top_n", 5))
        mutations_per = int(input.get("mutations_per_factor", 3))
        threshold = float(input.get("improvement_threshold", 0.05))
        pbo_max = float(input.get("pbo_max", 0.75))
        start_date = input.get("start_date", "2024-01-01")
        end_date = input.get("end_date", "2026-01-01")
        theme_filter = input.get("theme_filter", "")

        try:
            from evolution.factor_evolution import FactorEvolutionEngine

            engine = FactorEvolutionEngine(
                improvement_threshold=threshold,
                pbo_max=pbo_max,
            )
            result = await engine.run(
                top_n=top_n,
                mutations_per_factor=mutations_per,
                start_date=start_date,
                end_date=end_date,
                theme_filter=theme_filter,
            )
            return ToolResult.success(json.dumps(result, ensure_ascii=False, default=str))

        except ImportError as e:
            return ToolResult.error(f"进化引擎未就绪: {e}")
        except Exception as e:
            return ToolResult.error(f"因子进化失败: {e}")


# ══════════════════════════════════════════════════════════════
# Tool 5: FactorCompareTool
# ══════════════════════════════════════════════════════════════

class FactorCompareTool(Tool):
    """
    多因子对比分析 — 横向比较因子库中的因子性能。

    功能:
    - 按主题/评级分组统计
    - IC 相关矩阵（检测冗余）
    - Sharpe 分布直方图数据
    - 最优组合候选推荐
    """

    spec = ToolSpec(
        name="factor_compare",
        description=(
            "多因子对比分析：按主题分组统计、Sharpe分布、相关矩阵、最优组合推荐。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "group_by": {
                    "type": "string",
                    "enum": ["theme", "tier", "all"],
                    "default": "theme",
                    "description": "分组维度",
                },
                "top_n": {
                    "type": "integer",
                    "default": 30,
                    "description": "纳入对比的因子数",
                },
                "recommend_combine": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否推荐最优组合",
                },
            },
        },
        is_tier0=False,
        timeout=60,
        category="quant",
        keywords=["对比", "分析", "因子", "相关", "组合", "推荐"],
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        group_by = input.get("group_by", "theme")
        top_n = int(input.get("top_n", 30))
        recommend = input.get("recommend_combine", True)

        try:
            lib, redis_client = _get_factor_library()
            factors = await lib.get_all_factors(status="active")
            await redis_client.aclose()

            if not factors:
                return ToolResult.success("因子库为空，请先运行 factor_mine 挖掘因子。")

            sorted_f = sorted(
                factors,
                key=lambda f: float(getattr(f, "pool_score_v2", 0) or getattr(f, "sharpe", 0) or 0),
                reverse=True,
            )[:top_n]

            # ── Group statistics ──
            groups: dict[str, list] = {}
            for f in sorted_f:
                key = getattr(f, group_by, "unknown") if group_by != "all" else "all"
                groups.setdefault(key, []).append(f)

            group_stats = {}
            for grp, flist in groups.items():
                sharpes = [float(getattr(f, "sharpe", 0) or 0) for f in flist]
                irs = [float(getattr(f, "ir", 0) or 0) for f in flist]
                group_stats[grp] = {
                    "count": len(flist),
                    "avg_sharpe": round(sum(sharpes) / len(sharpes), 3),
                    "max_sharpe": round(max(sharpes), 3),
                    "avg_ir": round(sum(irs) / len(irs), 3),
                    "top_factor": getattr(flist[0], "sub_theme", getattr(flist[0], "theme", "?")),
                }

            # ── Sharpe distribution ──
            all_sharpes = [float(getattr(f, "sharpe", 0) or 0) for f in sorted_f]
            sharpe_dist = {
                "mean": round(sum(all_sharpes) / len(all_sharpes), 3),
                "max": round(max(all_sharpes), 3),
                "min": round(min(all_sharpes), 3),
                "above_1": sum(1 for s in all_sharpes if s >= 1.0),
                "above_0_5": sum(1 for s in all_sharpes if s >= 0.5),
            }

            # ── Combination recommendations ──
            combo_recs = []
            if recommend and len(sorted_f) >= 2:
                # Recommend diverse combos: pick from different themes
                theme_reps: dict[str, Any] = {}
                for f in sorted_f:
                    t = getattr(f, "theme", "unknown")
                    if t not in theme_reps:
                        theme_reps[t] = f
                top_diverse = list(theme_reps.values())[:5]
                combo_recs = [{
                    "factor": getattr(f, "sub_theme", getattr(f, "theme", "?")),
                    "theme": getattr(f, "theme", "?"),
                    "sharpe": getattr(f, "sharpe", 0),
                    "reason": "不同主题代表，降低相关性",
                } for f in top_diverse]

            return ToolResult.success(json.dumps({
                "total_factors": len(factors),
                "analyzed": len(sorted_f),
                "group_by": group_by,
                "group_stats": group_stats,
                "sharpe_distribution": sharpe_dist,
                "combination_recommendations": combo_recs,
            }, ensure_ascii=False, default=str))

        except Exception as e:
            return ToolResult.error(f"因子对比失败: {e}")


# ══════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════

def _extract_code_block(text: str) -> str:
    """Extract first Python code block from LLM reply."""
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # No fence — check if it looks like code
    if "def " in text and "return" in text:
        return text.strip()
    return ""


def _generate_param_variants(code: str, max_variants: int) -> list[tuple[str, str]]:
    """
    Extract numeric parameters from code and generate ±10%/±20% variants.

    Returns list of (modified_code, description) tuples.
    """
    # Find all integer/float literals that look like parameters
    pattern = re.compile(r'\b(\d+(?:\.\d+)?)\b')
    numbers = []
    for m in pattern.finditer(code):
        val_str = m.group(1)
        val = float(val_str)
        # Only meaningful parameter ranges (exclude years, binary flags)
        if 2 <= val <= 500 and val not in (0, 1):
            numbers.append((m.start(), m.end(), val_str, val))

    if not numbers:
        return []

    variants = []
    # Pick up to max_variants/2 unique positions, try +10% and -10%
    positions_tried = set()
    multipliers = [0.8, 0.9, 1.1, 1.2]

    for start, end, val_str, val in numbers:
        if start in positions_tried:
            continue
        for mult in multipliers:
            new_val = val * mult
            # Keep as int if original was int
            if "." not in val_str:
                new_val_str = str(int(round(new_val)))
            else:
                new_val_str = f"{new_val:.4g}"

            if new_val_str == val_str:
                continue

            new_code = code[:start] + new_val_str + code[end:]
            desc = f"参数 {val_str} → {new_val_str} (×{mult:.1f})"
            variants.append((new_code, desc))
            positions_tried.add(start)

            if len(variants) >= max_variants:
                return variants

    return variants[:max_variants]


def _quant_grade(
    metrics: dict, code: str, analysis_type: str
) -> tuple[str, int, list[str], list[str]]:
    """
    Compute grade (A-D), risk score, warnings, recommendations.
    Logic matches backtest_agent._quant_quality_check.
    """
    risk = 0
    warnings = []
    recommendations = []

    sharpe = float(metrics.get("sharpe", metrics.get("sharpe_ratio", 0)))
    trades = int(metrics.get("trades", metrics.get("total_trades", 0)))
    win_rate = float(metrics.get("win_rate", 0))
    max_dd = float(metrics.get("max_drawdown", 0))
    ic_mean = float(metrics.get("mean_ic", metrics.get("ic_mean", 0)))
    ir = float(metrics.get("ir", 0))
    pbo = float(metrics.get("pbo_score", 0))

    # Sharpe checks
    if sharpe > 3.0:
        risk += 3
        warnings.append(f"Sharpe={sharpe:.2f} 异常高，强烈怀疑过拟合")
        recommendations.append("缩短样本内期 + 严格样本外验证")
    elif sharpe > 2.0:
        risk += 1
        warnings.append(f"Sharpe={sharpe:.2f} 偏高，建议样本外验证")
    elif sharpe < 0.3:
        risk += 1
        warnings.append(f"Sharpe={sharpe:.2f} 偏低，策略可能无效")
        recommendations.append("增加因子信号强度或调整入场条件")

    # Trade count
    if trades < 30:
        risk += 2
        warnings.append(f"交易次数={trades} 过少，统计显著性不足")
        recommendations.append("延长回测周期或降低入场门槛以增加交易次数")
    elif trades < 100:
        risk += 1
        warnings.append(f"交易次数={trades} 偏少，建议 ≥100 笔")

    # Win rate
    if win_rate > 0.80:
        risk += 1
        warnings.append(f"胜率={win_rate:.0%} 异常高，可能存在幸存者偏差")

    # PBO
    if pbo > 0.75:
        risk += 2
        warnings.append(f"PBO={pbo:.2f} 过高，样本外大概率失效")
        recommendations.append("重新设计因子，避免数据窥探")
    elif pbo > 0.6:
        risk += 1
        warnings.append(f"PBO={pbo:.2f} 偏高，需样本外验证")

    # Factor-specific: IC
    if analysis_type == "factor" and ic_mean > 0:
        if ic_mean < 0.002:
            risk += 1
            warnings.append(f"IC均值={ic_mean:.4f} 过低，因子预测力弱")
            recommendations.append("提升因子信噪比，考虑与其他因子组合")
        if ir < 0.2:
            risk += 1
            warnings.append(f"IR={ir:.2f} 过低，因子稳定性差")
            recommendations.append("分析 IC 时序，找出衰减区间并过滤")

    # Max drawdown
    if abs(max_dd) > 0.40:
        risk += 1
        warnings.append(f"最大回撤={max_dd:.0%}，风险较高")
        recommendations.append("增加止损条件或降低仓位")

    # Code complexity check
    if code:
        magic_numbers = len(re.findall(r'\b\d+(?:\.\d+)?\b', code))
        conditions = len(re.findall(r'\b(?:if|elif|and|or)\b', code))
        if magic_numbers > 15:
            risk += 2
            warnings.append(f"代码含 {magic_numbers} 个魔法数字，过拟合风险高")
        if conditions > 20:
            risk += 1
            warnings.append(f"条件分支 {conditions} 个，策略可能过度复杂")

    # Grade
    if risk == 0:
        grade = "A"
    elif risk <= 2:
        grade = "B"
    elif risk <= 4:
        grade = "C"
    else:
        grade = "D"

    return grade, risk, warnings, recommendations


def _grade_verdict(grade: str, risk: int) -> str:
    verdicts = {
        "A": "✅ 优秀 — 可考虑入库部署",
        "B": "🟡 良好 — 建议样本外验证后使用",
        "C": "⚠️ 一般 — 需优化后再评估",
        "D": "❌ 差 — 过拟合风险过高，不建议使用",
    }
    return verdicts.get(grade, "未知")


def _analyze_ic(metrics: dict) -> dict:
    """Analyze IC quality from available metrics."""
    ic_mean = float(metrics.get("mean_ic", metrics.get("ic_mean", 0)))
    ir = float(metrics.get("ir", 0))
    sharpe = float(metrics.get("sharpe", 0))

    quality = "N/A"
    if ic_mean > 0:
        if ic_mean >= 0.05:
            quality = "强 (IC≥0.05，优秀因子)"
        elif ic_mean >= 0.02:
            quality = "良 (IC≥0.02，可用)"
        elif ic_mean >= 0.005:
            quality = "弱 (IC≥0.005，需组合使用)"
        else:
            quality = "极弱 (IC<0.005，信号不足)"

    stability = "N/A"
    if ir > 0:
        if ir >= 2.0:
            stability = "非常稳定 (IR≥2)"
        elif ir >= 1.0:
            stability = "稳定 (IR≥1)"
        elif ir >= 0.5:
            stability = "一般 (IR≥0.5)"
        else:
            stability = "不稳定 (IR<0.5)"

    return {
        "ic_mean": ic_mean,
        "ir": ir,
        "ic_quality": quality,
        "ic_stability": stability,
        "sharpe_based_alpha": "存在 Alpha" if sharpe > 0.5 else "Alpha 不明显",
    }


def _interpret_pbo(pbo: float) -> str:
    if pbo >= 0.75:
        return "高过拟合风险 — 样本外大概率失效"
    elif pbo >= 0.6:
        return "中等过拟合风险 — 谨慎使用"
    elif pbo >= 0.4:
        return "低风险 — 较可靠"
    else:
        return "极低过拟合风险 — 样本外表现良好"


def _days_between(start: str, end: str) -> int:
    """Calculate days between two date strings."""
    from datetime import datetime
    try:
        d1 = datetime.strptime(start, "%Y-%m-%d")
        d2 = datetime.strptime(end, "%Y-%m-%d")
        return abs((d2 - d1).days)
    except Exception:
        return 365
