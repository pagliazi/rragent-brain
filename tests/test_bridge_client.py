"""
bridge_client 本地集成测试。
通过 bridge_relay (127.0.0.1:8001 -> 192.168.1.139:8001) 验证真实 API。
用法:
  PYTHONPATH=/Users/zayl/openclaw-deploy \
  /Users/clawagent/openclaw/.venv/bin/python3 -m pytest tests/test_bridge_client.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.bridge_client import BridgeClient

BRIDGE_BASE = os.getenv("BRIDGE_BASE_URL", "http://127.0.0.1:8001/api/bridge")

SIMPLE_FACTOR_CODE = """
def generate_factor(df):
    return df['close'].pct_change(5)
"""

SIMPLE_ALPHA_CODE = """
def generate_signals(df):
    df['signal'] = 0
    df.loc[df['close'].pct_change(5) > 0.03, 'signal'] = 1
    return df
"""


@pytest.fixture
def bridge():
    client = BridgeClient(base_url=BRIDGE_BASE, secret="")
    yield client
    asyncio.get_event_loop().run_until_complete(client.close())


# ─── API Schema 验证 ───

class TestAPISchema:
    """验证 139 的 OpenAPI schema 是否符合预期。"""

    @pytest.mark.asyncio
    async def test_openapi_has_run_mining(self):
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://127.0.0.1:8001/api/openapi.json")
            assert r.status_code == 200, f"OpenAPI 不可用: {r.status_code}"
            spec = r.json()
            paths = spec.get("paths", {})
            assert "/api/bridge/backtest/run_mining/" in paths, \
                f"run_mining 端点不存在。可用 bridge 端点: {[p for p in paths if 'bridge' in p]}"

    @pytest.mark.asyncio
    async def test_run_mining_schema(self):
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://127.0.0.1:8001/api/openapi.json")
            spec = r.json()
            mining_spec = spec["paths"]["/api/bridge/backtest/run_mining/"]["post"]
            schema_ref = mining_spec["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema_name = schema_ref.split("/")[-1]
            schema = spec["components"]["schemas"][schema_name]
            props = schema.get("properties", {})
            assert "factor_code" in props, f"缺少 factor_code 参数。实际参数: {list(props.keys())}"
            assert "alpha_code" not in props, "run_mining 不应有 alpha_code 参数"

    @pytest.mark.asyncio
    async def test_run_alpha_mode_regex(self):
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://127.0.0.1:8001/api/openapi.json")
            spec = r.json()
            alpha_spec = spec["paths"]["/api/bridge/backtest/run_alpha/"]["post"]
            schema_ref = alpha_spec["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema_name = schema_ref.split("/")[-1]
            schema = spec["components"]["schemas"][schema_name]
            mode_prop = schema["properties"]["mode"]
            pattern = mode_prop.get("pattern", "")
            assert "mining" not in pattern, \
                f"run_alpha mode 不支持 mining (pattern: {pattern})"


# ─── BridgeClient 方法验证 ───

class TestBridgeClientMethods:
    """验证 BridgeClient 方法签名和参数映射正确。"""

    def test_run_factor_mining_calls_correct_endpoint(self):
        import inspect
        src = inspect.getsource(BridgeClient.run_factor_mining)
        assert "/backtest/run_mining/" in src, \
            "run_factor_mining 应调用 /backtest/run_mining/"
        assert "/backtest/run_alpha/" not in src, \
            "run_factor_mining 不应调用 /backtest/run_alpha/"

    def test_run_factor_mining_uses_factor_code_param(self):
        import inspect
        src = inspect.getsource(BridgeClient.run_factor_mining)
        assert '"factor_code"' in src, \
            "应使用 factor_code 参数名"
        assert '"alpha_code"' not in src, \
            "不应使用 alpha_code 参数名"
        assert '"mode"' not in src, \
            "run_mining 端点不需要 mode 参数"

    def test_run_alpha_uses_alpha_code_param(self):
        import inspect
        src = inspect.getsource(BridgeClient.run_alpha)
        assert '"alpha_code"' in src
        assert "/backtest/run_alpha/" in src

    def test_post_logs_error_body(self):
        import inspect
        src = inspect.getsource(BridgeClient._post)
        assert "e.response" in src, \
            "_post 应在 HTTPStatusError 时记录响应体"


# ─── 真实 API 调用测试 ───

class TestLiveAPI:
    """通过 bridge_relay 调用真实 139 API (需要网络连通)。"""

    @pytest.mark.asyncio
    async def test_limitup_reachable(self, bridge: BridgeClient):
        try:
            data = await bridge.get_limitup()
            assert isinstance(data, dict)
            assert "trade_date" in data or "limit_up" in data or "error" not in data
        except httpx.ConnectError:
            pytest.skip("bridge relay 不可达")

    @pytest.mark.asyncio
    async def test_run_factor_mining_no_422(self, bridge: BridgeClient):
        """核心测试: run_factor_mining 不应再返回 422。"""
        try:
            resp = await bridge.run_factor_mining(
                factor_code=SIMPLE_FACTOR_CODE,
                start_date="2025-09-01",
                end_date="2026-03-01",
            )
            assert isinstance(resp, dict), f"返回类型异常: {type(resp)}"
            print(f"\n  run_factor_mining 响应: {json.dumps(resp, ensure_ascii=False)[:300]}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                pytest.fail(
                    f"仍然返回 422! 响应: {e.response.text[:500]}"
                )
            raise

    @pytest.mark.asyncio
    async def test_run_alpha_technical(self, bridge: BridgeClient):
        """验证 run_alpha 的 technical 模式正常工作。"""
        try:
            resp = await bridge.run_alpha(
                alpha_code=SIMPLE_ALPHA_CODE,
                start_date="2025-09-01",
                end_date="2026-03-01",
                mode="technical",
            )
            assert isinstance(resp, dict)
            print(f"\n  run_alpha 响应: {json.dumps(resp, ensure_ascii=False)[:300]}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                pytest.fail(f"run_alpha technical 返回 422: {e.response.text[:500]}")
            raise


# ─── 独立运行 ───

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])
