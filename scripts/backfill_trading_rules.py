"""
回填 suggested_trading_rules：为库中所有旧因子（无规则的）补充买卖规则配置。
同时打印收益期望分析报告。

运行方式:
  cd /Users/zayl/OpenClaw-Universe/openclaw-brain
  .venv/bin/python3 scripts/backfill_trading_rules.py
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from agents.factor_library import FactorLibrary, REDIS_KEY_FACTORS
    from agents.alpha_digger import get_trading_rules_for_theme

    lib = FactorLibrary()
    r = await lib._get_redis()
    raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)

    updated = 0
    skipped = 0
    theme_stats: dict[str, list] = {}

    print(f"库中共 {len(raw_list)} 条因子记录，开始回填...\n")

    new_list = []
    for item in raw_list:
        try:
            d = json.loads(item)
        except (json.JSONDecodeError, TypeError):
            new_list.append(item)
            skipped += 1
            continue

        theme = d.get("theme", "unknown")
        existing_rules = d.get("suggested_trading_rules", {})

        if existing_rules:
            # 已有规则，跳过
            new_list.append(item)
            skipped += 1
            continue

        rules = get_trading_rules_for_theme(theme)
        d["suggested_trading_rules"] = rules
        new_list.append(json.dumps(d, ensure_ascii=False, default=str))
        updated += 1

        # 收集统计数据
        win_rate = d.get("win_rate", 0)
        win_rate = win_rate * 100 if win_rate <= 1 else win_rate
        stop = rules.get("stop_loss_pct", 0) * 100
        tp = rules.get("take_profit_pct")
        tp_val = tp * 100 if tp else None

        if theme not in theme_stats:
            theme_stats[theme] = []
        theme_stats[theme].append({
            "win_rate": win_rate,
            "stop": stop,
            "tp": tp_val,
        })

    # 原子性替换整个 list
    pipe = r.pipeline()
    pipe.delete(REDIS_KEY_FACTORS)
    for item in new_list:
        pipe.rpush(REDIS_KEY_FACTORS, item)
    await pipe.execute()

    print(f"回填完成: 更新 {updated} 个, 跳过 {skipped} 个\n")

    # ── 期望收益分析报告 ──
    print("=" * 72)
    print(f"{'主题':<28} {'数量':>5} {'胜率avg':>8} {'止损':>6} {'止盈':>6} {'P/L比':>6} {'EV/笔':>7}")
    print("-" * 72)

    for theme, records in sorted(theme_stats.items(), key=lambda x: -len(x[1])):
        if not records:
            continue
        n = len(records)
        avg_wr = sum(r["win_rate"] for r in records) / n
        stop = records[0]["stop"]
        tp = records[0]["tp"]

        if tp and stop:
            pl_ratio = tp / stop
            wr_dec = avg_wr / 100
            ev = wr_dec * tp - (1 - wr_dec) * stop
            ev_str = f"{ev:+.2f}%"
            ratio_str = f"{pl_ratio:.1f}x"
            tp_str = f"{tp:.0f}%"
        else:
            ev_str = "trailing"
            ratio_str = "trail"
            tp_str = "trail"

        print(f"{theme[:28]:<28} {n:>5} {avg_wr:>7.1f}% {stop:>5.0f}% {tp_str:>6} {ratio_str:>6} {ev_str:>7}")

    print("=" * 72)
    print("\n注: EV = 胜率×止盈 - 败率×止损 (每笔交易的期望净收益)")
    print("    A股日线策略: 止损<6%容易被日内噪声触发，已统一调整为最低6%")

    await lib.close()


if __name__ == "__main__":
    asyncio.run(main())
