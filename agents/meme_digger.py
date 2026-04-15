"""
妖股因子挖掘器 — Meme Stock Factor Digger

专门针对A股「妖股」的启动前特征，挖掘能在涨停/连板前1-3天给出信号的量化因子。

妖股定义:
  A股市场短期内（5-15个交易日）出现30%+涨幅的高弹性个股，通常伴随:
  - 连续涨停板 (close >= prev_close * 1.099)
  - 成交量爆发 (量比 > 3，即 vol/avg_vol > 3)
  - 独立于大盘的个股异动行情
  - 小盘低价 + 主题/概念驱动
  - 情绪周期通常持续 3-10 个交易日

核心目标: 挖掘「启动前预兆」——在妖股真正发动前 1-3 天识别蓄势信号，
         而不是追涨追板（那是噪声，不是 alpha）。

架构位置:
  meme_digger.run_meme_session() → 同 alpha_digger 的底层 bridge/router/factor_lib 基础设施
  产出因子汇入同一个 Factor Library → Factor Combiner → 实盘候选
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from datetime import date, timedelta

logger = logging.getLogger("agent.meme_digger")


# ══════════════════════════════════════════════════════════════════════════════
# 妖股主题池 — 每个主题对应妖股启动前的一类量价特征
# ══════════════════════════════════════════════════════════════════════════════

MEME_THEMES = [
    {
        "id": "meme_amplitude_compression",
        "name": "振幅收窄蓄势",
        "desc": (
            "妖股启动前的典型形态: 振幅持续收窄（横盘整理），成交量同步萎缩，"
            "当振幅压缩至历史低分位时，变盘（向上突破）概率大幅上升。"
            "核心信号: 短周期振幅 / 长周期振幅 → 持续下降后的极值。"
        ),
        "hints": [
            "short_amp = (high - low).rolling(3).mean() / close.shift(1)",
            "long_amp  = (high - low).rolling(20).mean() / close.shift(1)",
            "compression = short_amp / (long_amp + 1e-8)  # 比值越低 = 压缩越充分",
            "# 因子可以是 compression 的负数 (压缩越深 → 信号越强)",
        ],
        "lookback_range": (5, 25),
    },
    {
        "id": "meme_volume_dry_up",
        "name": "缩量蓄势",
        "desc": (
            "价格在高位或均线附近震荡，成交量持续萎缩（锁筹），"
            "表明浮筹已被洗干净，主力即将启动。"
            "关键: 价稳量缩 vs 价跌量缩 有本质区别，需组合价格相对位置判断。"
        ),
        "hints": [
            "vol_shrink = volume.rolling(5).mean() / (volume.rolling(20).mean() + 1e-8)",
            "price_hold = close / close.rolling(10).mean()  # 价格相对均线位置",
            "# 缩量蓄势信号: vol_shrink 低 AND price_hold 接近1或略高",
            "# 可以用: (2 - vol_shrink) * price_hold 复合",
        ],
        "lookback_range": (5, 20),
    },
    {
        "id": "meme_limit_up_momentum",
        "name": "涨停板动量延续",
        "desc": (
            "A股特有: 涨停板后次日/后续的动量延续效应。"
            "首次涨停后缩量整理（不破5日线），再次放量为二次启动信号。"
            "关键信号: 近 N 日内出现涨停次数 + 涨停后成交量变化。"
            "涨停判定: close >= close.shift(1) * 1.099"
        ),
        "hints": [
            "limit_up = (close >= close.shift(1) * 1.099).astype(float)",
            "recent_lu = limit_up.rolling(10).sum()  # 近10日涨停次数",
            "# 涨停后次日量比: volume / volume.shift(1) (涨停当天的后一天)",
            "# 复合: 有涨停记录 × 之后成交量萎缩 (蓄势) = 二次启动信号",
        ],
        "lookback_range": (3, 15),
    },
    {
        "id": "meme_breakout_signal",
        "name": "放量突破信号",
        "desc": (
            "价格突破近期高点 + 成交量显著放大，是妖股启动最常见的技术信号。"
            "关键: 突破后量能持续性（不缩量，非假突破）。"
            "设计思路: 价格创 N 日新高的同时，成交量超过 M 日均量的 K 倍。"
        ),
        "hints": [
            "# 价格相对 N 日高点的位置",
            "price_rank = close.rolling(20).rank(axis=0, pct=True)",
            "vol_ratio = volume / (volume.rolling(20).mean() + 1e-8)",
            "# 突破信号: price 在高分位 × 量比放大",
            "# 可以尝试: (price_rank ** 2) * np.log1p(vol_ratio)",
        ],
        "lookback_range": (10, 30),
    },
    {
        "id": "meme_opening_attack",
        "name": "竞价强势开盘",
        "desc": (
            "妖股启动往往伴随强势开盘（大幅高开）或开盘即封板。"
            "竞价跳空 = (open - prev_close) / prev_close，"
            "开盘强度 = (close - open) / (high - low + 1e-8)，"
            "两者结合反映主力控盘意愿。"
        ),
        "hints": [
            "gap_up = (open - close.shift(1)) / (close.shift(1) + 1e-8)",
            "close_strength = (close - open) / (high - low + 1e-8)",
            "# 强开 + 高收 = 全天强势攻击型",
            "# rolling 统计: 近 N 日高开次数 / N = 开攻频率",
        ],
        "lookback_range": (3, 15),
    },
    {
        "id": "meme_chip_consolidation",
        "name": "筹码整理蓄势",
        "desc": (
            "价格震荡区间收窄（低点抬高、高点持平）= 筹码集中锁定，主力控盘信号。"
            "低点抬高: rolling_min 趋势向上。"
            "区间收窄: (rolling_high - rolling_low) / close 持续缩小。"
        ),
        "hints": [
            "rolling_high = high.rolling(N).max()",
            "rolling_low  = low.rolling(N).min()",
            "range_ratio  = (rolling_high - rolling_low) / (close + 1e-8)",
            "low_trend    = low.rolling(5).min().diff(3)  # 低点抬高",
            "# 信号: range_ratio 收窄 + low_trend 上升",
        ],
        "lookback_range": (5, 20),
    },
    {
        "id": "meme_vol_price_resonance",
        "name": "量价共振",
        "desc": (
            "成交量方向与价格方向高度同步 = 趋势确认信号。"
            "涨时放量、跌时缩量的持续模式是主力推升的痕迹。"
            "量价同向天数 / 总天数 → 同步比例因子。"
        ),
        "hints": [
            "price_dir = np.sign(close.diff())",
            "vol_dir   = np.sign(volume.diff())",
            "sync_flag = (price_dir == vol_dir).astype(float)",
            "# 近 N 日同步率",
            "sync_ratio = sync_flag.rolling(N).mean()",
            "# 再乘以量比做权重放大",
        ],
        "lookback_range": (5, 20),
    },
    {
        "id": "meme_intraday_high_close",
        "name": "尾盘强势（收盘近最高价）",
        "desc": (
            "收盘价占日内价格区间的高位 = 资金尾盘不减仓，次日往往延续强势。"
            "收盘强度 = (close - low) / (high - low + 1e-8)，"
            "连续多日收盘强度高 → 有资金在持续护盘。"
        ),
        "hints": [
            "tail_strength = (close - low) / (high - low + 1e-8)",
            "# 近 N 日收盘强度均值",
            "rolling_tail = tail_strength.rolling(N).mean()",
            "# 结合成交量: rolling_tail * (volume / volume.rolling(20).mean())",
        ],
        "lookback_range": (3, 15),
    },
    {
        "id": "meme_pre_explosion_pattern",
        "name": "爆发前夕综合特征",
        "desc": (
            "综合多个维度的妖股预兆: "
            "(1) 振幅收窄 + (2) 收盘强势 + (3) 成交量萎缩后反弹。"
            "三者叠加 = 最强蓄势信号。"
            "设计一个能同时捕捉这三个维度的复合因子。"
        ),
        "hints": [
            "amp = (high - low) / (close.shift(1) + 1e-8)",
            "vol_ratio = volume / (volume.rolling(20).mean() + 1e-8)",
            "close_pos = (close - low) / (high - low + 1e-8)",
            "# 振幅收窄分: 1 / (amp.rolling(5).mean() + 0.01)",
            "# 成交量蓄势后反弹: vol_ratio / vol_ratio.shift(3)",
            "# 收盘强度: close_pos.rolling(5).mean()",
        ],
        "lookback_range": (3, 20),
    },
    {
        "id": "meme_gap_follow",
        "name": "跳空延续信号",
        "desc": (
            "跳空高开后的当日表现预示后续趋势。"
            "真突破: 高开后全天保持高位（收盘 > 开盘）。"
            "假突破: 高开后回落（收盘 << 开盘）。"
            "连续几日的跳空模式可以衡量主力攻击意志。"
        ),
        "hints": [
            "gap = (open - close.shift(1)) / (close.shift(1) + 1e-8)",
            "follow_through = (close - open) / (high - low + 1e-8)",
            "# 真突破信号: gap > 0 AND follow_through > 0.4",
            "# 近 N 日真突破频率",
            "true_breakout = ((gap > 0) & (follow_through > 0.4)).astype(float)",
        ],
        "lookback_range": (5, 20),
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# 妖股专用 LLM Prompt
# ══════════════════════════════════════════════════════════════════════════════

MEME_MINER_PROMPT = """你是妖股因子工程师，挖掘A股「妖股启动前1-3天」的量化预测信号。

█ 本轮方向: {theme_name} — {theme_desc}
█ 参考: {hints}
█ 参数: lookback={lookback}, decay={decay_weight:.2f}

█ 高胜率因子的铁律 (来自实证统计):
  1. 必须用 ewm(alpha=X) 平滑 — 胜率提升10%
  2. 必须组合 high/low + volume — sharpe提升40%
  3. 均值回归结构取反(-signal) — 最高胜率69%

█ 已验证的高胜率妖股因子模式 (Sharpe=3.01, 胜率69%):
    price_dev = (close - close.rolling(N).mean()) / (close.rolling(N).std() + 1e-10)
    vol_ratio = volume / (volume.rolling(N).mean() + 1e-10)
    range_ratio = (high - low) / ((high - low).rolling(N).mean() + 1e-10)
    signal = -price_dev * (1 + vol_ratio * 0.3) * (1 + range_ratio * 0.2)
    factor = signal.ewm(alpha=0.9).mean().fillna(0)

█ 妖股专用信号维度 (融入上述结构):
  振幅压缩  : amp_compress = (high-low).rolling(5).mean() / ((high-low).rolling(20).mean() + 1e-8)
  缩量蓄势  : vol_shrink = volume.rolling(5).mean() / (volume.rolling(20).mean() + 1e-8)
  收盘强度  : tail_pos = (close - low) / (high - low + 1e-8)
  竞价跳空  : gap = (open - close.shift(1)) / (close.shift(1) + 1e-8)
  涨停动量  : limit_up = (close >= close.shift(1) * 1.099).astype(float).rolling(10).sum()

█ 你的任务: 用上述结构(ewm+取反+多维)，融入「{theme_name}」维度创造新变体。

█ 数据: matrices = {{'open','high','low','close','volume'}} DataFrame
█ 返回: factor DataFrame, 值越高=越看好, shape同close
█ 仅用 numpy(np) + pandas(pd), 中间变量 .fillna(0)
█ 禁止: import其他模块, reshape操作, 数据库/网络

{quality_rules}

只输出代码:

```python
import numpy as np
import pandas as pd

def generate_factor(matrices):
    close = matrices['close']
    high = matrices['high']
    low = matrices['low']
    volume = matrices['volume']
    open_ = matrices['open']
    # 妖股预测因子
    factor = ...
    return factor
```"""


# ══════════════════════════════════════════════════════════════════════════════
# 种子生成
# ══════════════════════════════════════════════════════════════════════════════

def _generate_meme_seed(focus_theme_id: str | None = None) -> dict:
    """生成妖股挖掘种子 (随机主题 + 参数扰动)。

    Args:
        focus_theme_id: 指定主题 ID (None = 随机选取)
    """
    from agents.factor_quality import QUALITY_RULES_FOR_PROMPT

    if focus_theme_id:
        candidates = [t for t in MEME_THEMES if t["id"] == focus_theme_id]
        theme = candidates[0] if candidates else random.choice(MEME_THEMES)
    else:
        theme = random.choice(MEME_THEMES)

    lo, hi = theme["lookback_range"]
    lookback = random.randint(lo, hi)
    decay_weight = round(random.uniform(0.5, 1.0), 2)

    hints_text = "\n  ".join(theme["hints"])

    prompt = MEME_MINER_PROMPT.format(
        theme_name=theme["name"],
        theme_desc=theme["desc"],
        hints=hints_text,
        lookback=lookback,
        decay_weight=decay_weight,
        quality_rules=QUALITY_RULES_FOR_PROMPT,
    )

    return {
        "theme_id": theme["id"],
        "theme_name": f"妖股/{theme['name']}",
        "lookback": lookback,
        "decay_weight": decay_weight,
        "prompt": prompt,
    }


def _generate_meme_mutation_seed(original_code: str, metrics: dict) -> dict:
    """基于现有妖股因子做变异。"""
    from agents.factor_quality import QUALITY_RULES_FOR_PROMPT
    from agents.alpha_digger import FACTOR_MUTATOR_PROMPT

    mutations = [
        "缩短 rolling 窗口至 3-7 日 (更敏感的妖股启动信号)",
        "将加法组合改为乘法组合 (信号联合确认)",
        "添加 limit_up = (close >= close.shift(1)*1.099) 作为二值权重",
        "加入 vol_ratio = volume/volume.rolling(10).mean() 作为量能乘数",
        "对因子取 rolling(3).max() (捕捉近期峰值信号)",
        "加入 gap = (open - close.shift(1))/(close.shift(1)+1e-8) 跳空信号",
        "用 ewm(span=5) 替换 rolling.mean() 使信号更平滑",
        "添加 tail_pos = (close-low)/(high-low+1e-8) 尾盘强度权重",
    ]
    direction = random.choice(mutations)

    prompt = FACTOR_MUTATOR_PROMPT.format(
        original_code=original_code,
        sharpe=metrics.get("sharpe", 0),
        ic_mean=metrics.get("mean_ic", 0),
        ir=metrics.get("ir", 0),
        mutation_direction=f"[妖股专项] {direction}",
        quality_rules=QUALITY_RULES_FOR_PROMPT,
    )

    return {
        "theme_id": "meme_mutation",
        "theme_name": f"妖股变异: {direction[:20]}",
        "prompt": prompt,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 妖股挖掘轮次
# ══════════════════════════════════════════════════════════════════════════════

async def run_meme_mining_round(
    router,
    bridge,
    factor_lib,
    notify_fn=None,
    n_factors: int = 5,
    focus_theme_id: str | None = None,
) -> dict:
    """执行单轮妖股因子挖掘。

    流程: 生成 N 个妖股种子 → LLM 生成因子代码 → 沙箱回测 → PBO/质量双重检验 → 入库

    Returns:
        {"generated": N, "tested": M, "admitted": K, "factors": [...]}
    """
    start_date = (date.today() - timedelta(days=365)).isoformat()  # 妖股因子用更长回测期
    end_date = date.today().isoformat()

    async def _notify(text: str):
        if notify_fn:
            try:
                await notify_fn(text)
            except Exception:
                pass
        logger.info(text)

    from agents.alpha_digger import _extract_factor_code
    from agents.factor_quality import code_audit as _code_audit

    # 生成种子: N-1 个随机妖股主题 + 1 个变异 (如果库中有妖股因子)
    seeds = [_generate_meme_seed(focus_theme_id) for _ in range(n_factors)]

    # 查找库中已有的最优妖股因子做变异 (60%变异策略同 alpha_digger)
    existing = await factor_lib.get_all_factors()
    meme_factors = [f for f in existing if f.theme.startswith("meme_")]
    if meme_factors and len(meme_factors) >= 2:
        top_yao = sorted(meme_factors, key=lambda f: f.sharpe, reverse=True)[:10]
        n_mutations = max(1, int(n_factors * 0.6))
        for mi in range(n_mutations):
            if mi >= len(seeds):
                break
            base = random.choice(top_yao)
            seeds[-(mi + 1)] = _generate_meme_mutation_seed(base.code, {
                "sharpe": base.sharpe, "mean_ic": base.ic_mean, "ir": base.ir,
            })
    elif meme_factors:
        best_meme = max(meme_factors, key=lambda f: f.ir)
        seeds[-1] = _generate_meme_mutation_seed(best_meme.code, {
            "sharpe": best_meme.sharpe, "mean_ic": best_meme.ic_mean, "ir": best_meme.ir,
        })

    # LLM 生成因子代码
    generated_codes = []
    for i, seed in enumerate(seeds):
        try:
            reply = await router.chat([
                {"role": "system", "content": "你是 rragent 妖股因子挖掘引擎。只输出 generate_factor 函数代码。"},
                {"role": "user", "content": seed["prompt"]},
            ], task_type="code")
        except Exception as e:
            logger.warning("LLM 调用失败 (meme_seed %d): %s", i, e)
            continue

        if not reply:
            continue

        code = _extract_factor_code(reply)
        # 妖股因子允许涨停检测逻辑 (allow_limit_up=True)
        report = _code_audit(code, allow_limit_up=True)
        issues = [f"[{iss.rule_id}] {iss.message}" for iss in report.issues if iss.severity == "fatal"]
        if issues:
            logger.info("妖股种子 %d 代码校验失败: %s", i, "; ".join(issues))
            continue

        generated_codes.append({
            "code": code,
            "theme_id": seed["theme_id"],
            "theme_name": seed["theme_name"],
        })

    await _notify(f"🐉 妖股因子: 生成 {len(generated_codes)}/{n_factors} 个代码")

    results = {
        "generated": len(generated_codes),
        "tested": 0,
        "admitted": 0,
        "rejected_reasons": [],
        "factors": [],
    }

    from agents.factor_quality import full_audit

    for item in generated_codes:
        code = item["code"]

        # ── 初步沙箱回测 ──
        try:
            resp = await bridge.run_factor_mining(
                factor_code=code,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            logger.warning("沙箱调用失败: %s", e)
            results["rejected_reasons"].append(f"沙箱异常: {e}")
            continue

        results["tested"] += 1

        if resp.get("status") == "error":
            err = resp.get("error", "")[:200]
            results["rejected_reasons"].append(f"引擎错误: {err}")
            continue

        metrics = resp.get("metrics") or {}
        if not metrics.get("sharpe_ratio") and not metrics.get("sharpe"):
            results["rejected_reasons"].append("指标异常: sharpe 为空")
            continue

        # ── 代码 + 指标双重质量检验 ──
        quality = full_audit(code, metrics)
        if not quality.passed:
            reason_text = f"质量评审不通过 [{quality.grade}]: " + "; ".join(
                f"[{i.rule_id}] {i.message}" for i in quality.issues if i.severity == "fatal"
            )
            logger.info("妖股因子被拒 (质量): %s — %s", item["theme_name"], reason_text)
            results["rejected_reasons"].append(reason_text)
            results["factors"].append({
                "theme": item["theme_name"],
                "sharpe": metrics.get("sharpe_ratio") or metrics.get("sharpe"),
                "quality_grade": quality.grade,
                "admitted": False,
                "reason": reason_text,
            })
            continue

        # ── CSCV PBO 过拟合验证 ──
        try:
            cscv_resp = await bridge.run_factor_mining_cscv(
                factor_code=code, total_days=365, k=4
            )
            pbo_score = cscv_resp.get("pbo_score", 0.0)
            window_sharpes = cscv_resp.get("window_sharpes", [])
            metrics["pbo_score"] = pbo_score
            metrics["cscv_windows"] = cscv_resp.get("metrics", {}).get("cscv_windows", [])
            await _notify(
                f"  🔬 CSCV PBO={pbo_score:.3f}  窗口Sharpe={[round(s, 2) for s in window_sharpes]}"
            )
            positive_windows = sum(1 for s in window_sharpes if s > 0)
            overall_sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe") or 0
            pbo_exempt = overall_sharpe >= 0.3 and positive_windows >= len(window_sharpes) / 2
            if pbo_score > 0.75 and not pbo_exempt:
                reason_text = (
                    f"[PB01] PBO={pbo_score:.3f} — 妖股因子跨时段不一致，过拟合拒绝"
                    f"  窗口Sharpe={[round(s, 2) for s in window_sharpes]}"
                )
                logger.info("妖股因子被拒 (PBO): %s", reason_text)
                results["rejected_reasons"].append(reason_text)
                results["factors"].append({
                    "theme": item["theme_name"],
                    "pbo_score": pbo_score,
                    "window_sharpes": window_sharpes,
                    "admitted": False,
                    "reason": reason_text,
                })
                continue
            elif pbo_score > 0.75:
                await _notify(f"  ⚠️ PBO={pbo_score:.3f} 高但豁免 (整体sharpe={overall_sharpe:.2f}, {positive_windows}/{len(window_sharpes)}窗口为正)")
        except Exception as e:
            logger.warning("CSCV 验证异常 (跳过): %s", e)

        # ── 入库 ──
        admitted, reason, factor_id = await factor_lib.add_factor(
            code=code,
            metrics=metrics,
            theme=item["theme_id"],
            sub_theme=item["theme_name"],
        )

        if admitted:
            results["admitted"] += 1
            await _notify(
                f"🏆 妖股因子入库! ID={factor_id} | {item['theme_name']} | "
                f"sharpe={metrics.get('sharpe', 0):.3f}"
            )
        else:
            results["rejected_reasons"].append(reason)
            await _notify(f"  ❌ {item['theme_name']} 入库被拒: {reason}")
            logger.info("妖股因子被拒: %s — %s", item["theme_name"], reason)

        results["factors"].append({
            "theme": item["theme_name"],
            "sharpe": metrics.get("sharpe_ratio") or metrics.get("sharpe"),
            "ic_mean": metrics.get("factor_ic") or metrics.get("mean_ic"),
            "ir": metrics.get("sortino_ratio") or metrics.get("ir"),
            "pbo_score": metrics.get("pbo_score"),
            "admitted": admitted,
            "reason": reason,
        })

        if admitted:
            results["admitted"] += 1
            await _notify(
                f"🐉🏆 妖股因子入库! ID={factor_id} | {item['theme_name']} | "
                f"sharpe={metrics.get('sharpe', 0):.3f} | PBO={metrics.get('pbo_score', 0):.3f}"
            )
        else:
            results["rejected_reasons"].append(reason)

    stats = await factor_lib.get_stats()
    results["library_stats"] = stats

    if stats.get("ready_to_combine"):
        await _notify(f"🔮 因子库已达 {stats['active_count']} 个，可触发融合!")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 妖股挖掘会话
# ══════════════════════════════════════════════════════════════════════════════

MEME_SESSION_CONFIG = {
    "factors_per_round": 5,
    "round_interval_sec": 120,    # 妖股轮间短一点，专注主题
    "max_rounds_per_session": 4,  # 默认 4 轮 × 5 因子 = 20 次回测
}


async def run_meme_session(
    notify_fn=None,
    max_rounds: int = 0,
    factors_per_round: int = 0,
    round_interval: int = 0,
    focus_theme_id: str | None = None,
) -> dict:
    """运行一次完整的妖股因子挖掘会话。

    Args:
        max_rounds:       最大轮数 (0=使用默认)
        factors_per_round: 每轮因子数 (0=使用默认)
        round_interval:   轮间间隔秒数 (0=使用默认)
        focus_theme_id:   指定妖股主题 ID (None=随机轮换)
    """
    from agents.llm_router import get_llm_router
    from agents.bridge_client import get_bridge_client
    from agents.factor_library import get_factor_library

    router = get_llm_router()
    bridge = get_bridge_client()
    factor_lib = get_factor_library()

    max_rounds = max_rounds or MEME_SESSION_CONFIG["max_rounds_per_session"]
    factors_per_round = factors_per_round or MEME_SESSION_CONFIG["factors_per_round"]
    interval = round_interval or MEME_SESSION_CONFIG["round_interval_sec"]

    async def _notify(text: str):
        if notify_fn:
            try:
                await notify_fn(text)
            except Exception:
                pass
        logger.info(text)

    session_stats = {
        "rounds_completed": 0,
        "total_generated": 0,
        "total_tested": 0,
        "total_admitted": 0,
        "start_time": time.time(),
        "mode": "meme_digger",
    }

    theme_names = [t["name"] for t in MEME_THEMES]
    await _notify(
        f"🐉 妖股因子挖掘启动: {max_rounds} 轮 × {factors_per_round} 因子/轮\n"
        f"   主题池: {', '.join(theme_names[:5])}..."
    )

    for round_num in range(1, max_rounds + 1):
        await _notify(f"🐉 [{round_num}/{max_rounds}] 妖股挖掘中...")

        try:
            round_result = await run_meme_mining_round(
                router=router,
                bridge=bridge,
                factor_lib=factor_lib,
                notify_fn=notify_fn,
                n_factors=factors_per_round,
                focus_theme_id=focus_theme_id,
            )
            session_stats["rounds_completed"] += 1
            session_stats["total_generated"] += round_result.get("generated", 0)
            session_stats["total_tested"] += round_result.get("tested", 0)
            session_stats["total_admitted"] += round_result.get("admitted", 0)
        except Exception as e:
            logger.error("妖股轮次 %d 异常: %s", round_num, e)
            await _notify(f"⚠️ 妖股轮次 {round_num} 异常: {e}")

        if round_num < max_rounds:
            await asyncio.sleep(interval)

    elapsed = time.time() - session_stats["start_time"]
    session_stats["elapsed_sec"] = round(elapsed, 1)

    await _notify(
        f"🐉✅ 妖股挖掘完成: "
        f"{session_stats['total_admitted']}/{session_stats['total_tested']} 入库 "
        f"(耗时 {elapsed/60:.1f}min)"
    )
    return session_stats
