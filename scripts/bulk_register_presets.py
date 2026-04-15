#!/usr/bin/env python3
"""一次性脚本: 将 Redis 中已有的因子策略批量注册为 139 presets.

用法: cd ~/openclaw && source .venv/bin/activate && python scripts/bulk_register_presets.py
"""
import asyncio, json, os, time, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import redis.asyncio as aioredis
from agents.bridge_client import get_bridge_client

STRATEGY_REDIS_KEY = "rragent:strategies"


async def main():
    rd = aioredis.Redis()
    bridge = get_bridge_client()
    all_strats = await rd.hgetall(STRATEGY_REDIS_KEY)
    print(f"Total strategies: {len(all_strats)}")

    registered = 0
    failed = 0
    skipped = 0

    for key, val in all_strats.items():
        strat = json.loads(val)
        strat_id = strat.get("id", key.decode() if isinstance(key, bytes) else key)

        # Skip if already has preset_slug
        if strat.get("preset_slug"):
            skipped += 1
            continue

        source = strat.get("source", "unknown")
        theme = strat.get("factor_theme", strat.get("title", "unknown"))
        sharpe = strat.get("factor_sharpe", strat.get("metrics", {}).get("sharpe_ratio", 0))
        code = strat.get("code", "")
        metrics = strat.get("metrics", {})
        factor_id = strat.get("factor_id", "")

        if not code:
            skipped += 1
            continue

        try:
            # Step 1: save to ledger if no ledger_id
            ledger_id = strat.get("ledger_id")
            if not ledger_id:
                ledger_resp = await bridge._post("/strategy/save/", {
                    "title": f"[因子] {theme} Sharpe={sharpe:.2f}",
                    "strategy_code": code,
                    "backtest_metrics": metrics,
                    "status": "APPROVE",
                    "topic": theme,
                    "model_used": "rrclaw-factor-pipeline",
                })
                ledger_id = ledger_resp.get("id")

            # Step 2: register preset
            slug = f"{source}_{factor_id or strat_id}_{int(strat.get('created_at', time.time()))}"
            resp = await bridge._post("/strategy/register-preset/", {
                "slug": slug,
                "name": f"[因子] {theme} Sharpe={sharpe:.2f}",
                "description": f"因子策略选股: {theme} (Sharpe={sharpe:.2f})",
                "category": "factor",
                "payload": {
                    "code": code,
                    "metrics": metrics,
                    "params": strat.get("params", {}),
                },
                "ledger_id": ledger_id,
            })

            # Update Redis record
            strat["ledger_id"] = ledger_id
            strat["preset_slug"] = resp.get("slug", slug)
            strat["synced_to_139"] = True
            strat["status"] = "synced"
            k = key if isinstance(key, str) else key.decode()
            await rd.hset(STRATEGY_REDIS_KEY, k, json.dumps(strat, ensure_ascii=False, default=str))

            registered += 1
            print(f"  ✓ {strat_id}: {theme} (Sharpe={sharpe:.2f}) → preset={slug}")
            await asyncio.sleep(3)  # Bridge rate limit: 30 req/60s

        except Exception as e:
            failed += 1
            print(f"  ✗ {strat_id}: {e}")

    print(f"\nDone: registered={registered}, failed={failed}, skipped={skipped}")
    await rd.aclose()


if __name__ == "__main__":
    asyncio.run(main())
