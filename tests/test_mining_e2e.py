#!/usr/bin/env python3
"""
因子挖掘 端到端测试

验证:
  1. bridge_client 代码静态正确性
  2. 139 API Schema 兼容性
  3. run_factor_mining 真实调用 (不返回 422)
  4. metrics 字段标准化
  5. factor_library 入库检查可走通

用法:
  NO_PROXY=127.0.0.1,localhost \
  PYTHONPATH=/Users/zayl/openclaw-deploy \
  /Users/clawagent/openclaw/.venv/bin/python3 tests/test_mining_e2e.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

import httpx
from agents.bridge_client import BridgeClient
from agents.factor_library import ADMISSION_THRESHOLDS

PASS = FAIL = SKIP = 0

def ok(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {name}")
    else:
        FAIL += 1; print(f"  ❌ {name}: {detail}")

def skip(name: str, reason: str):
    global SKIP; SKIP += 1; print(f"  ⏭️  {name}: {reason}")

BRIDGE_URL = os.getenv("BRIDGE_BASE_URL", "http://127.0.0.1:8001/api/bridge")
SIMPLE_FACTOR = "def generate_factor(df):\n    return df['close'].pct_change(5)"


def test_static():
    print("\n═══ 1. 代码静态检查 ═══")
    src = inspect.getsource(BridgeClient.run_factor_mining)
    ok("调用 /backtest/run_mining/",     "/backtest/run_mining/" in src)
    ok("不调用 /backtest/run_alpha/",    "/backtest/run_alpha/" not in src)
    ok("使用 factor_code",               '"factor_code"' in src)
    ok("不含 alpha_code",                '"alpha_code"' not in src)
    ok("不含 mode 参数",                  '"mode"' not in src)
    ok("run_alpha 用 /run_alpha/",       "/backtest/run_alpha/" in inspect.getsource(BridgeClient.run_alpha))
    ok("_post 记录错误响应体",            "e.response" in inspect.getsource(BridgeClient._post))
    ok("_normalize_mining_metrics 存在",  hasattr(BridgeClient, "_normalize_mining_metrics"))


@pytest.mark.live
async def test_schema():
    print("\n═══ 2. API Schema 验证 ═══")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://127.0.0.1:8001/api/openapi.json")
            if r.status_code != 200:
                skip("OpenAPI", f"HTTP {r.status_code}"); return
            spec = r.json()
            paths = spec.get("paths", {})

            ok("run_mining 端点存在", "/api/bridge/backtest/run_mining/" in paths)

            if "/api/bridge/backtest/run_mining/" in paths:
                ref = (paths["/api/bridge/backtest/run_mining/"]["post"]
                       ["requestBody"]["content"]["application/json"]["schema"]["$ref"])
                schema = spec["components"]["schemas"][ref.split("/")[-1]]
                props = list(schema.get("properties", {}).keys())
                ok("含 factor_code",   "factor_code" in props, f"实际: {props}")
                ok("不含 alpha_code",   "alpha_code" not in props)
                ok("factor_code 必填",  "factor_code" in schema.get("required", []))

            if "/api/bridge/backtest/run_alpha/" in paths:
                ref = (paths["/api/bridge/backtest/run_alpha/"]["post"]
                       ["requestBody"]["content"]["application/json"]["schema"]["$ref"])
                schema = spec["components"]["schemas"][ref.split("/")[-1]]
                mp = schema["properties"].get("mode", {}).get("pattern", "")
                ok("run_alpha mode 不含 mining", "mining" not in mp, f"pattern={mp}")
    except httpx.ConnectError as e:
        skip("API Schema", f"连接失败: {e}")


@pytest.mark.live
async def test_live_call():
    print("\n═══ 3. 真实 API 调用 ═══")
    b = BridgeClient(base_url=BRIDGE_URL, secret="")
    try:
        try:
            await b.get_limitup()
            ok("GET /limitup/ 连通", True)
        except Exception as e:
            skip("全部 API 测试", f"bridge 不可达: {e}"); return

        t0 = time.time()
        try:
            resp = await b.run_factor_mining(
                factor_code=SIMPLE_FACTOR,
                start_date="2025-09-01", end_date="2026-03-01")
            elapsed = time.time() - t0
            ok("run_factor_mining 无 422", True)
            ok("返回 dict", isinstance(resp, dict))
            ok(f"status={resp.get('status')}", resp.get("status") == "success",
               f"status={resp.get('status')}, error={resp.get('error','')[:100]}")
        except httpx.HTTPStatusError as e:
            ok(f"HTTP {e.response.status_code}", False, e.response.text[:300])
            return
        except Exception as e:
            ok("run_factor_mining", False, f"{type(e).__name__}: {e}")
            return

        print(f"      耗时: {elapsed:.1f}s")
    finally:
        await b.close()
    return resp


@pytest.mark.live
async def test_field_mapping(resp: dict):
    print("\n═══ 4. 字段标准化验证 ═══")
    m = resp.get("metrics", {})

    required_fields = ["sharpe", "mean_ic", "ir", "trades", "win_rate"]
    for f in required_fields:
        val = m.get(f)
        ok(f"metrics['{f}'] 存在 (={val})", val is not None)

    ok("sharpe 为数值",     isinstance(m.get("sharpe"), (int, float)))
    ok("win_rate 为小数 (<1)", 0 < m.get("win_rate", 99) < 1,
       f"win_rate={m.get('win_rate')}")
    ok("trades 为正整数",    m.get("trades", 0) > 0)

    ok("保留原始 sharpe_ratio",  m.get("sharpe_ratio") is not None)
    ok("保留原始 total_trades",  m.get("total_trades") is not None)


@pytest.mark.live
async def test_admission(resp: dict):
    print("\n═══ 5. 入库检查模拟 ═══")
    m = resp.get("metrics", {})
    T = ADMISSION_THRESHOLDS
    print(f"      当前门槛: {T}")

    s = m.get("sharpe", 0); ic = m.get("mean_ic", 0); ir = m.get("ir", 0)
    t = m.get("trades", 0); wr = m.get("win_rate", 0)
    print(f"      因子指标: sharpe={s:.3f} ic={ic:.4f} ir={ir:.3f} trades={t} wr={wr:.3f}")

    checks = [
        ("sharpe 非 None",         m.get("sharpe") is not None),
        ("mean_ic 非 None",        m.get("mean_ic") is not None),
        ("ir 非 None",             m.get("ir") is not None),
        ("trades 非 None",         m.get("trades") is not None),
        ("win_rate 非 None",       m.get("win_rate") is not None),
        ("不会因 None 提前退出",    all(m.get(k) is not None for k in ["sharpe", "mean_ic", "ir", "trades", "win_rate"])),
    ]
    for name, cond in checks:
        ok(name, cond)

    fails = []
    if s < T["sharpe_min"]: fails.append("sharpe")
    if abs(ic) < T["ic_mean_min"]: fails.append("ic")
    if abs(ir) < T["ir_min"]: fails.append("ir")
    if t < T["trades_min"]: fails.append("trades")
    if wr < T["win_rate_min"]: fails.append("win_rate")

    if fails:
        print(f"      简单测试因子不达标: {', '.join(fails)} (正常，LLM因子质量会更高)")
    else:
        print(f"      ✅ 简单测试因子已可入库!")


async def main():
    test_static()
    await test_schema()
    resp = await test_live_call()
    if resp and resp.get("status") == "success":
        await test_field_mapping(resp)
        await test_admission(resp)

    print(f"\n{'='*55}")
    color = "🟢" if FAIL == 0 else "🔴"
    print(f"  {color} 结果: {PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过")
    print(f"{'='*55}")
    return FAIL


if __name__ == "__main__":
    failures = asyncio.run(main())
    sys.exit(1 if failures > 0 else 0)
