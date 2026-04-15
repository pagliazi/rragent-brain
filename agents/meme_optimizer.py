"""
妖股因子迭代优化器 — Yao Factor Iteration Optimizer

职责:
  1. 分析因子库中妖股因子的主题分布和质量趋势
  2. 计算各主题的成功率，调整下次挖掘的主题权重
  3. 使用最优因子运行截面筛选，生成当日妖股信号
  4. 将分析结果持久化到 Redis 供前端展示
  5. 每次迭代写入事件日志，供前端展示优化历程

Redis key 设计:
  rragent:meme:theme_stats      Hash  主题性能统计
  rragent:meme:iteration_log    List  迭代事件日志 (最近 100 条)
  rragent:meme:signals:latest   String  最新股票信号 JSON
  rragent:meme:dashboard_cache  String  dashboard 缓存 (TTL=300s)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("agent.meme_optimizer")

REDIS_KEY_THEME_STATS    = "rragent:meme:theme_stats"
REDIS_KEY_ITER_LOG       = "rragent:meme:iteration_log"
REDIS_KEY_SIGNALS        = "rragent:meme:signals:latest"
REDIS_KEY_DASH_CACHE     = "rragent:meme:dashboard_cache"
REDIS_KEY_THEME_WEIGHTS  = "rragent:meme:theme_weights"

# 主题中文名映射
THEME_NAMES = {
    "meme_amplitude_compression": "振幅收窄蓄势",
    "meme_volume_dry_up":         "缩量蓄势",
    "meme_limit_up_momentum":     "涨停板动量",
    "meme_breakout_signal":       "放量突破",
    "meme_opening_attack":        "竞价强势",
    "meme_chip_consolidation":    "筹码整理",
    "meme_vol_price_resonance":   "量价共振",
    "meme_intraday_high_close":   "尾盘强势",
    "meme_pre_explosion_pattern": "爆发前夕",
    "meme_gap_follow":            "跳空延续",
    "meme_mutation":              "变异进化",
}


# ══════════════════════════════════════════════════════════════════════════════
# 核心分析函数
# ══════════════════════════════════════════════════════════════════════════════

async def analyze_library(factor_lib, r=None) -> dict:
    """分析妖股因子库，计算主题统计，返回 dashboard 数据结构。

    Args:
        factor_lib: FactorLibrary 实例
        r: Redis 连接 (传入则缓存结果)

    Returns:
        dict: 完整的 dashboard payload
    """
    all_factors = await factor_lib.get_all_factors(status="active")
    meme_factors = [f for f in all_factors if f.theme.startswith("meme_")]

    # ── 主题统计 ──
    theme_map: dict[str, list] = {}
    for f in meme_factors:
        tid = f.theme
        theme_map.setdefault(tid, []).append(f)

    theme_stats = []
    for tid, factors in sorted(theme_map.items(), key=lambda x: -len(x[1])):
        sharpes  = [f.sharpe   for f in factors if f.sharpe  > 0]
        win_rates= [f.win_rate for f in factors if f.win_rate > 0]
        pbos     = [f.id for f in factors]   # pbo_score not stored in FactorRecord directly
        avg_sh   = sum(sharpes)  / len(sharpes)   if sharpes   else 0
        avg_wr   = sum(win_rates)/ len(win_rates) if win_rates else 0
        best_sh  = max(sharpes,  default=0)
        theme_stats.append({
            "theme_id":   tid,
            "theme_name": THEME_NAMES.get(tid, tid),
            "count":      len(factors),
            "avg_sharpe": round(avg_sh, 3),
            "best_sharpe":round(best_sh, 3),
            "avg_win_rate":round(avg_wr, 4),
        })

    # ── 最优因子 TOP 10 ──
    top_factors = sorted(meme_factors, key=lambda f: f.sharpe, reverse=True)[:10]
    top_list = []
    for f in top_factors:
        top_list.append({
            "id":        f.id,
            "theme_id":  f.theme,
            "theme_name":THEME_NAMES.get(f.theme, f.theme),
            "sub_theme": f.sub_theme,
            "sharpe":    round(f.sharpe, 3),
            "win_rate":  round(f.win_rate, 4),
            "ic_mean":   round(f.ic_mean, 4),
            "ir":        round(f.ir, 3),
            "trades":    f.trades,
            "status":    f.status,
            "created_at":f.created_at if hasattr(f, "created_at") else None,
        })

    # ── 全局摘要 ──
    all_sharpes   = [f.sharpe   for f in meme_factors if f.sharpe   > 0]
    all_win_rates = [f.win_rate for f in meme_factors if f.win_rate > 0]
    summary = {
        "total_factors":   len(meme_factors),
        "total_themes":    len(theme_map),
        "avg_sharpe":      round(sum(all_sharpes)   / len(all_sharpes),    3) if all_sharpes   else 0,
        "best_sharpe":     round(max(all_sharpes,   default=0),            3),
        "avg_win_rate":    round(sum(all_win_rates) / len(all_win_rates),  4) if all_win_rates else 0,
        "best_win_rate":   round(max(all_win_rates, default=0),            4),
    }

    # ── 读取迭代日志 ──
    iter_log = []
    if r:
        try:
            raw_logs = await r.lrange(REDIS_KEY_ITER_LOG, 0, 29)
            iter_log = [json.loads(l) for l in raw_logs]
        except Exception as e:
            logger.warning("读取迭代日志失败: %s", e)

    # ── 读取最新信号 ──
    signals = []
    if r:
        try:
            raw_sig = await r.get(REDIS_KEY_SIGNALS)
            if raw_sig:
                signals = json.loads(raw_sig)
        except Exception as e:
            logger.warning("读取信号失败: %s", e)

    dashboard = {
        "summary":      summary,
        "theme_stats":  theme_stats,
        "top_factors":  top_list,
        "iteration_log":iter_log,
        "signals":      signals,
        "generated_at": time.time(),
    }

    # ── 缓存到 Redis ──
    if r:
        try:
            await r.set(REDIS_KEY_DASH_CACHE, json.dumps(dashboard, default=str), ex=300)
        except Exception:
            pass

    return dashboard


async def run_analysis_and_update(factor_lib, r, notify_fn=None) -> dict:
    """完整的分析 + 更新流程: 分析库 → 更新主题权重 → 写迭代日志。

    Returns:
        dict: 分析摘要
    """
    async def _notify(t):
        if notify_fn:
            try: await notify_fn(t)
            except Exception: pass
        logger.info(t)

    dashboard = await analyze_library(factor_lib, r)
    summary   = dashboard["summary"]
    theme_stats = dashboard["theme_stats"]

    # ── 计算主题权重 (成功数越多 + 平均 Sharpe 越高 → 权重越高) ──
    weights = {}
    total_weight_sum = 0
    for ts in theme_stats:
        # 线性加权: count * 0.4 + avg_sharpe * 0.6 (归一化后)
        raw_w = ts["count"] * 0.4 + ts["avg_sharpe"] * 0.6
        weights[ts["theme_id"]] = max(raw_w, 0.5)  # 最低保底权重
        total_weight_sum += weights[ts["theme_id"]]

    # 归一化到 [0.5, 3.0]
    if total_weight_sum > 0:
        for tid in weights:
            weights[tid] = round(min(3.0, max(0.5, weights[tid] / total_weight_sum * len(weights) * 1.5)), 2)

    # 从未出现的主题保留基础权重
    from agents.meme_digger import MEME_THEMES
    for t in MEME_THEMES:
        if t["id"] not in weights:
            weights[t["id"]] = 1.0

    # 保存权重到 Redis
    try:
        await r.set(REDIS_KEY_THEME_WEIGHTS, json.dumps(weights), ex=86400)
    except Exception as e:
        logger.warning("保存主题权重失败: %s", e)

    # 最佳主题
    best_theme = max(theme_stats, key=lambda t: t["avg_sharpe"], default=None)
    best_theme_name = best_theme["theme_name"] if best_theme else "—"
    best_theme_sharpe = best_theme["avg_sharpe"] if best_theme else 0

    # ── 写迭代事件日志 ──
    event = {
        "ts":          time.time(),
        "ts_str":      _ts_str(),
        "type":        "analysis",
        "total_factors": summary["total_factors"],
        "avg_sharpe":  summary["avg_sharpe"],
        "best_sharpe": summary["best_sharpe"],
        "avg_win_rate":summary["avg_win_rate"],
        "theme_count": len(theme_stats),
        "best_theme":  best_theme_name,
        "best_sharpe_theme": best_theme_sharpe,
        "weights":     weights,
    }
    await log_iteration_event(r, event)

    await _notify(
        f"📊 妖股库分析完成: {summary['total_factors']} 个因子 "
        f"| 最优主题: {best_theme_name} (avg Sharpe={best_theme_sharpe:.2f}) "
        f"| 全局 avg Sharpe={summary['avg_sharpe']:.2f}"
    )
    return event


# ══════════════════════════════════════════════════════════════════════════════
# 信号生成 (用最优妖股因子跑截面筛选)
# ══════════════════════════════════════════════════════════════════════════════

async def refresh_signals(factor_lib, bridge, r, top_n: int = 3, notify_fn=None) -> list:
    """用 TOP N 妖股因子运行实盘截面筛选，缓存结果。

    Returns:
        list: 股票信号列表
    """
    async def _notify(t):
        if notify_fn:
            try: await notify_fn(t)
            except Exception: pass
        logger.info(t)

    all_factors = await factor_lib.get_all_factors(status="active")
    meme_factors = [f for f in all_factors if f.theme.startswith("meme_")]
    if not meme_factors:
        logger.info("妖股因子库为空, 跳过信号生成")
        return []

    top_factors = sorted(meme_factors, key=lambda f: f.ir, reverse=True)[:top_n]

    all_signals = []
    for i, fac in enumerate(top_factors):
        try:
            # 构造 factor_code DSL 并调用 screener
            dsl_code = _build_screener_dsl(fac.code, fac.sub_theme or fac.theme)
            resp = await bridge.run_screener(dsl_code)
            stocks = resp.get("stocks") or resp.get("results") or []
            for s in stocks[:20]:
                s["factor_id"]   = fac.id
                s["factor_theme"]= THEME_NAMES.get(fac.theme, fac.theme)
                s["factor_sharpe"]= fac.sharpe
            all_signals.extend(stocks[:20])
            await _notify(f"  🎯 {THEME_NAMES.get(fac.theme,'?')} 筛出 {len(stocks)} 只")
        except Exception as e:
            logger.warning("因子 %s 筛选失败: %s", fac.id, e)

    # 去重并按因子分 Sharpe 排序
    seen = set()
    deduped = []
    for s in sorted(all_signals, key=lambda x: x.get("factor_sharpe", 0), reverse=True):
        code = s.get("stock") or s.get("ts_code") or s.get("code")
        if code and code not in seen:
            seen.add(code)
            deduped.append(s)

    payload = {
        "stocks":       deduped[:30],
        "generated_at": time.time(),
        "ts_str":       _ts_str(),
        "based_on":     [f.id for f in top_factors],
    }
    try:
        await r.set(REDIS_KEY_SIGNALS, json.dumps(payload, default=str), ex=3600 * 8)
    except Exception:
        pass

    await _notify(f"🎯 妖股信号: {len(deduped)} 只候选 (基于 {len(top_factors)} 个因子)")
    return deduped


def _build_screener_dsl(factor_code: str, label: str) -> str:
    """将 generate_factor 代码包装为 screener factor_code DSL。"""
    return f'''# 妖股因子筛选: {label}
{factor_code}

# screener 入口
def run(matrices):
    factor = generate_factor(matrices)
    return factor
'''


# ══════════════════════════════════════════════════════════════════════════════
# 迭代日志
# ══════════════════════════════════════════════════════════════════════════════

async def log_iteration_event(r, event: dict):
    """将迭代事件追加到 Redis List (LIFO, 最近 100 条)。"""
    try:
        await r.lpush(REDIS_KEY_ITER_LOG, json.dumps(event, default=str))
        await r.ltrim(REDIS_KEY_ITER_LOG, 0, 99)
    except Exception as e:
        logger.warning("写迭代日志失败: %s", e)


async def get_iteration_log(r, limit: int = 30) -> list:
    """读取最近 N 条迭代事件。"""
    try:
        raw = await r.lrange(REDIS_KEY_ITER_LOG, 0, limit - 1)
        return [json.loads(l) for l in raw]
    except Exception:
        return []


async def get_theme_weights(r) -> dict:
    """读取当前主题权重 (用于下次挖掘参数)。"""
    try:
        raw = await r.get(REDIS_KEY_THEME_WEIGHTS)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    # 默认: 所有主题等权
    from agents.meme_digger import MEME_THEMES
    return {t["id"]: 1.0 for t in MEME_THEMES}


# ══════════════════════════════════════════════════════════════════════════════
# 智能迭代: 根据主题权重选择下次重点挖掘方向
# ══════════════════════════════════════════════════════════════════════════════

async def get_focus_theme_for_next_session(r) -> str | None:
    """根据主题权重选择下次挖掘的重点主题 (加权随机选取)。

    Returns:
        theme_id str, or None (全主题随机)
    """
    import random
    weights = await get_theme_weights(r)
    if not weights:
        return None
    # 用权重概率选取
    themes = list(weights.keys())
    w_vals = [weights[t] for t in themes]
    chosen = random.choices(themes, weights=w_vals, k=1)[0]
    return chosen


# ══════════════════════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════════════════════

def _ts_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
