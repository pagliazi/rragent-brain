"""
Factor Evolver — 因子参数调优进化引擎

职责:
  1. 对库中 active 因子进行参数微调（rolling 窗口、阈值等）
  2. 提交沙箱验证进化后的版本
  3. 如进化成功（Sharpe 提升 >5%），更新因子记录
  4. 连续 3 次进化失败 → 标记为 low_pool
  5. 进化后触发重新分类（high_pool / low_pool）

参数进化策略:
  - 提取代码中的数值参数（rolling N, 阈值 k），随机微调 ±20%
  - LLM 辅助参数建议（高优先级因子）
  - 每个因子最多尝试 3 个进化变体，取最优
"""
from __future__ import annotations

import asyncio
import logging
import re
import random
import time
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("agent.factor_evolver")

# 进化成功判定: Sharpe 提升比例
EVOLUTION_IMPROVE_THRESHOLD = 0.03   # 3% 提升认为成功 (接近阈值因子更易满足)
# 每轮批量进化的因子数
EVOLUTION_BATCH_SIZE = 20
# 每个因子尝试的变体数 (系统网格搜索用，实际由下方 GRID_MULTIPLIERS 决定)
VARIANTS_PER_FACTOR = 6
# 系统网格搜索: 对窗口参数乘以这些倍数 (覆盖 ±50% 范围)
GRID_MULTIPLIERS = [0.5, 0.67, 0.8, 1.25, 1.5, 2.0]
# 进化失败阈值 → tier_retired / low_pool (与 factor_library.FAILURE_THRESHOLD_EVOLUTION 同步)
FAILURE_THRESHOLD = 5


def _mutate_numeric_params(code: str, mutation_strength: float = 0.2,
                           int_multiplier: float = 0.0) -> str:
    """对因子代码中的数值参数进行参数调优。

    策略:
      - window = N / lookback = N: 最常见的命名窗口变量 (覆盖 ~90% 因子)
        如果 int_multiplier > 0，使用固定倍数（系统网格搜索）；
        否则随机扰动 ±mutation_strength（传统随机方式）。
      - rolling(N) / rolling(window=N) / shift(N) / ewm(span=N): 函数调用形式
      - decay_weight = X / alpha = X: 常见命名浮点系数（始终随机扰动）
      - 浮点阈值比较 (> 1.x, < 0.x): 内联阈值
      - 保持整数约束 (rolling 窗口必须是正整数 ≥ 2)
    """
    import re as _re

    def _ri_grid(n):
        """按系统倍数调整整数 n。"""
        new_n = max(2, round(n * int_multiplier))
        return new_n if new_n != n else n + 1  # 确保不同

    def _ri_rand(n):
        """随机扰动整数 n。"""
        lo = max(2, int(n * (1 - mutation_strength)))
        hi = max(lo + 1, int(n * (1 + mutation_strength)))
        return random.randint(lo, hi)

    def _ri(n):
        return _ri_grid(n) if int_multiplier > 0 else _ri_rand(n)

    def _rf(v):
        """随机扰动浮点 v，返回新浮点字符串。"""
        if v <= 0 or v >= 10:
            return str(v)
        delta = v * 0.15 * random.uniform(-1, 1)
        new_v = round(max(0.001, min(9.99, v + delta)), 3)
        return str(new_v)

    def _mutate_int(m):
        new_n = _ri(int(m.group(1)))
        return m.group(0).replace(m.group(1), str(new_n), 1)

    def _mutate_float(m):
        return m.group(0).replace(m.group(1), _rf(float(m.group(1))), 1)

    # ── 命名整数变量 (覆盖绝大多数因子) ──
    code = _re.sub(
        r'\b(window|lookback|period|span|n_days|lag|fast|slow)\s*=\s*(\d+)',
        lambda m: f'{m.group(1)} = {_ri(int(m.group(2)))}',
        code,
    )

    # ── 函数调用形式 ──
    code = _re.sub(r'rolling\((\d+)\)', _mutate_int, code)
    code = _re.sub(r'rolling\(window=(\d+)\)', _mutate_int, code)
    code = _re.sub(r'shift\((\d+)\)', _mutate_int, code)
    code = _re.sub(r'ewm\(span=(\d+)\)', _mutate_int, code)

    # ── 命名浮点系数 (始终随机) ──
    code = _re.sub(
        r'\b(decay_weight|alpha|beta|decay_factor)\s*=\s*(\d+\.\d+)',
        lambda m: f'{m.group(1)} = {_rf(float(m.group(2)))}',
        code,
    )

    # ── 内联浮点阈值 ──
    code = _re.sub(r'>\s*(1\.\d+)', _mutate_float, code)
    code = _re.sub(r'<\s*(0\.\d+)', _mutate_float, code)

    return code


async def evolve_one_factor(
    factor,          # FactorRecord
    bridge,          # BridgeClient
    factor_lib,      # FactorLibrary
    notify_fn=None,
) -> dict:
    """对单个因子进行参数进化，尝试 VARIANTS_PER_FACTOR 个变体。

    Returns:
        {"evolved": bool, "best_sharpe": float, "attempts": int, "reason": str}
    """
    from agents.factor_quality import code_audit

    baseline_sharpe = factor.sharpe
    start_date = (date.today() - timedelta(days=365)).isoformat()  # 1 年窗口
    end_date = date.today().isoformat()

    best_code = None
    best_metrics = None
    best_sharpe = baseline_sharpe
    attempts = 0

    # 系统网格搜索: 测试各倍数对应的窗口参数
    for variant_idx, grid_mult in enumerate(GRID_MULTIPLIERS):
        try:
            mutated_code = _mutate_numeric_params(
                factor.code,
                mutation_strength=0.15,
                int_multiplier=grid_mult,
            )
        except Exception as e:
            logger.warning("参数微调失败 %s v%d: %s", factor.id, variant_idx, e)
            continue

        # 如果微调后代码与原来一样，跳过
        if mutated_code.strip() == factor.code.strip():
            continue

        # 静态代码审查
        audit = code_audit(mutated_code)
        if not audit.passed:
            continue

        attempts += 1
        try:
            resp = await bridge.run_factor_mining(
                factor_code=mutated_code,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            logger.warning("进化沙箱调用失败 %s v%d: %s", factor.id, variant_idx, e)
            continue

        if resp.get("status") == "error":
            continue

        metrics = resp.get("metrics") or {}
        new_sharpe = metrics.get("sharpe", 0)

        if new_sharpe > best_sharpe:
            best_sharpe = new_sharpe
            best_code = mutated_code
            best_metrics = metrics

    # 判断进化是否成功
    improvement = (best_sharpe - baseline_sharpe) / max(abs(baseline_sharpe), 0.01)
    if best_code and improvement >= EVOLUTION_IMPROVE_THRESHOLD:
        await factor_lib.record_evolution_success(factor.id, best_code, best_metrics)
        if notify_fn:
            try:
                await notify_fn(
                    f"🧬 进化成功 {factor.id[:12]} ({factor.theme}): "
                    f"Sharpe {baseline_sharpe:.3f} → {best_sharpe:.3f} (+{improvement*100:.1f}%)"
                )
            except Exception:
                pass
        return {"evolved": True, "best_sharpe": best_sharpe, "attempts": attempts,
                "reason": f"Sharpe +{improvement*100:.1f}%"}
    else:
        failures = await factor_lib.increment_evolution_failure(factor.id)
        if failures >= FAILURE_THRESHOLD:
            wr = factor.win_rate if factor.win_rate <= 1 else factor.win_rate / 100
            await factor_lib.set_pool(factor.id, "low_pool", factor.sharpe * wr)
            reason = f"连续{failures}次进化失败，降入低因子池"
            logger.info("因子降池: %s %s", factor.id, reason)
        else:
            reason = f"本轮进化未达阈值 (best={best_sharpe:.3f}, 需>{baseline_sharpe*(1+EVOLUTION_IMPROVE_THRESHOLD):.3f})"
        return {"evolved": False, "best_sharpe": best_sharpe, "attempts": attempts, "reason": reason}


async def run_batch_evolution(
    factor_lib,
    bridge,
    notify_fn=None,
    max_factors: int = EVOLUTION_BATCH_SIZE,
) -> dict:
    """批量进化因子。

    Returns:
        {"total": N, "evolved": K, "demoted": M, "details": [...]}
    """
    candidates = await factor_lib.get_factors_for_evolution(limit=max_factors)

    if not candidates:
        return {"total": 0, "evolved": 0, "demoted": 0, "details": [],
                "message": "无需进化的因子 (所有因子已在 high_pool/low_pool 或进化失败次数已满)"}

    if notify_fn:
        try:
            await notify_fn(f"🔬 开始批量进化 {len(candidates)} 个因子...")
        except Exception:
            pass

    results = {"total": len(candidates), "evolved": 0, "demoted": 0, "details": []}

    for factor in candidates:
        result = await evolve_one_factor(factor, bridge, factor_lib, notify_fn)
        results["details"].append({
            "factor_id": factor.id,
            "theme": factor.theme,
            "baseline_sharpe": factor.sharpe,
            **result,
        })
        if result["evolved"]:
            results["evolved"] += 1
        elif getattr(factor, "evolution_failures", 0) >= FAILURE_THRESHOLD:
            # 已达退休阈值，被降池
            results["demoted"] += 1

        # 短暂延迟避免沙箱过载
        await asyncio.sleep(1)

    # 进化完成后重新分类
    classification = await factor_lib.classify_all_factors()
    results["classification"] = classification

    if notify_fn:
        try:
            await notify_fn(
                f"✅ 批量进化完成: 进化成功={results['evolved']}/{results['total']}, "
                f"降池={results['demoted']}\n"
                f"分类: 高池={classification['high_pool']}, "
                f"低池={classification['low_pool']}, 活跃={classification['active']}"
            )
        except Exception:
            pass

    return results
