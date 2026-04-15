#!/usr/bin/env python3
"""Re-register all factor presets with corrected payload format.

The register-preset endpoint now auto-wraps raw code into factor_code DSL.
This script re-POSTs each strategy to trigger that auto-wrap.
"""
import asyncio, json, os, sys, time
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

    updated = 0
    failed = 0
    skipped = 0

    for key, val in all_strats.items():
        strat = json.loads(val)
        slug = strat.get("preset_slug")
        code = strat.get("code", "")

        if not slug or not code:
            skipped += 1
            continue

        theme = strat.get("factor_theme", strat.get("title", "unknown"))
        sharpe = strat.get("factor_sharpe", strat.get("metrics", {}).get("sharpe_ratio", 0))
        metrics = strat.get("metrics", {})
        params = strat.get("params", {})

        try:
            resp = await bridge._post("/strategy/register-preset/", {
                "slug": slug,
                "name": f"[因子] {theme} Sharpe={sharpe:.2f}",
                "description": f"因子策略选股: {theme} (Sharpe={sharpe:.2f})",
                "category": "factor",
                "payload": {
                    "code": code,
                    "metrics": metrics,
                    "params": params,
                },
            })
            updated += 1
            if updated % 20 == 0:
                print(f"  ... {updated} updated")
            await asyncio.sleep(0.5)

        except Exception as e:
            failed += 1
            print(f"  ✗ {slug}: {e}")

    print(f"\nDone: updated={updated}, failed={failed}, skipped={skipped}")
    await rd.aclose()


if __name__ == "__main__":
    asyncio.run(main())
