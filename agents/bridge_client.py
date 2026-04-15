"""
OpenClaw Bridge Client — 调用 ReachRich Bridge API。

双模认证：
  Mode 1 (内部): BRIDGE_SECRET HMAC 签名 — 服务端专用
  Mode 2 (用户): REACHRICH_TOKEN 用户令牌 — RRCLAW 终端用户
    Token 格式: "{user_id}:{hmac_hex}"，从 ReachRich 设置页获取

环境变量:
  BRIDGE_BASE_URL — API 地址（默认 192.168.1.139:8001）
  BRIDGE_SECRET   — 内部 HMAC 密钥（服务端用）
  REACHRICH_TOKEN — 用户令牌（RRCLAW 用户用，优先级高于 BRIDGE_SECRET）
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import uuid
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

BRIDGE_BASE_URL = os.getenv("BRIDGE_BASE_URL", "http://192.168.1.139:8001/api/bridge")
BRIDGE_SECRET = os.getenv("BRIDGE_SECRET", "")
REACHRICH_TOKEN = os.getenv("REACHRICH_TOKEN", "")
if not BRIDGE_SECRET and not REACHRICH_TOKEN:
    logger.warning("Neither BRIDGE_SECRET nor REACHRICH_TOKEN is set — Bridge API auth will fail")
REQUEST_TIMEOUT_GET = 10.0
REQUEST_TIMEOUT_POST = 620.0
MAX_RETRIES = 3
RETRY_BACKOFF = [1.0, 2.0, 4.0]


class BridgeClient:
    """ReachRich Bridge API 客户端，含双模认证、重试、降级。

    认证优先级: token > secret
      - token: 用户令牌 (X-Bridge-Token header)，RRCLAW 终端用户使用
      - secret: 服务端 HMAC 密钥 (X-Bridge-Timestamp + X-Bridge-Key)，内部服务使用
    """

    def __init__(self, base_url: str = BRIDGE_BASE_URL,
                 secret: str = BRIDGE_SECRET,
                 token: str = REACHRICH_TOKEN):
        self.base_url = base_url.rstrip("/")
        self.secret = secret.encode()
        self.token = token  # "{user_id}:{hmac_hex}" composite token
        self._client: Optional[httpx.AsyncClient] = None
        self._last_snapshot: Optional[dict] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            transport = httpx.AsyncHTTPTransport(proxy=None)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(REQUEST_TIMEOUT_GET, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                transport=transport,
            )
        return self._client

    def _auth_headers(self, path: str, method: str = "GET") -> dict[str, str]:
        """Build auth headers. Token auth takes priority over HMAC secret."""
        if self.token:
            if self.token.startswith("rk_"):
                # API Key auth — standard Bearer
                return {"Authorization": f"Bearer {self.token}"}
            else:
                # Legacy user token
                return {"X-Bridge-Token": self.token}

        # Legacy HMAC signing — internal server-to-server
        ts = str(int(time.time()))
        message = f"{ts}{path}".encode()
        sig = hmac.new(self.secret, message, hashlib.sha256).hexdigest()
        headers = {
            "X-Bridge-Timestamp": ts,
            "X-Bridge-Key": sig,
        }
        if method == "POST":
            headers["X-Bridge-Nonce"] = uuid.uuid4().hex
        return headers

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        client = await self._get_client()
        headers = self._auth_headers(path, "GET")

        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get(path, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                data_hash = resp.headers.get("X-Data-Hash", "")
                if data_hash:
                    import orjson
                    expected = hashlib.sha256(orjson.dumps(data)).hexdigest()
                    if expected != data_hash:
                        logger.warning("Data hash mismatch on %s", path)

                return data

            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF[attempt]
                    logger.warning("Bridge %s failed (attempt %d): %s, retrying in %.1fs",
                                   path, attempt + 1, e, wait)
                    import asyncio
                    await asyncio.sleep(wait)
                else:
                    logger.error("Bridge %s failed after %d attempts: %s", path, MAX_RETRIES, e)
                    raise

    async def _post(self, path: str, json_body: dict) -> dict:
        client = await self._get_client()

        for attempt in range(MAX_RETRIES):
            headers = self._auth_headers(path, "POST")
            try:
                resp = await client.post(
                    path, json=json_body, headers=headers,
                    timeout=REQUEST_TIMEOUT_POST,
                )
                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError as e:
                logger.error("Bridge POST %s HTTP %d: %s",
                             path, e.response.status_code, e.response.text[:500])
                raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF[attempt]
                    logger.warning("Bridge POST %s failed (attempt %d): %s, retrying in %.1fs",
                                   path, attempt + 1, e, wait)
                    import asyncio
                    await asyncio.sleep(wait)
                else:
                    logger.error("Bridge POST %s failed after %d attempts: %s", path, MAX_RETRIES, e)
                    raise

    # ── Market Agent Methods ──

    async def get_snapshot(self) -> dict:
        data = await self._get("/snapshot/")
        self._last_snapshot = data
        return data

    async def get_limitup(self, trade_date: str = "") -> dict:
        params = {"trade_date": trade_date} if trade_date else {}
        return await self._get("/limitup/", params)

    async def get_concepts(self, limit: int = 50) -> dict:
        return await self._get("/concepts/", {"limit": limit})

    async def get_sentiment(self, limit: int = 20) -> dict:
        return await self._get("/sentiment/", {"limit": limit})

    async def get_dragon_tiger(self, trade_date: str = "") -> dict:
        params = {"trade_date": trade_date} if trade_date else {}
        return await self._get("/dragon-tiger/", params)

    # ── Quant Agent Methods ──

    async def get_kline(self, ts_code: str, period: str = "daily",
                        start_date: str = "", end_date: str = "",
                        limit: int = 250, fmt: str = "json") -> dict:
        return await self._get("/kline/", {
            "ts_code": ts_code, "period": period,
            "start_date": start_date, "end_date": end_date,
            "limit": limit, "fmt": fmt,
        })

    async def get_indicators(self, ts_code: str, limit: int = 60) -> dict:
        return await self._get("/indicators/", {"ts_code": ts_code, "limit": limit})

    async def get_presets(self) -> dict:
        return await self._get("/presets/")

    async def run_screener(self, payload: dict, limit: int = 50) -> dict:
        return await self._post("/screener/", {"payload": payload, "limit": limit})

    async def run_backtest(self, code: str, stock: str = "",
                           start_date: str = "20240101", end_date: str = "20241231",
                           timeout: int = 600, mode: str = "backtrader",
                           strategy_params: dict | None = None) -> dict:
        body: dict[str, Any] = {
            "strategy_code": code,
            "start_date": start_date, "end_date": end_date,
            "timeout": timeout, "mode": mode,
        }
        if stock:
            body["stock"] = stock
        if strategy_params:
            body["strategy_params"] = strategy_params
        return await self._post("/backtest/run/", body)

    async def run_alpha(self, alpha_code: str,
                        start_date: str = "2025-09-01",
                        end_date: str = "2026-03-01",
                        mode: str = "technical",
                        factors: list[str] | None = None) -> dict:
        """调用 core_engine 固化引擎执行 alpha 回测。"""
        body: dict[str, Any] = {
            "alpha_code": alpha_code,
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode,
        }
        if factors:
            body["factors"] = factors
        return await self._post("/backtest/run_alpha/", body)

    async def run_factor_mining(self, factor_code: str,
                                start_date: str = "", end_date: str = "",
                                timeout: int = 600) -> dict:
        """因子挖掘：将 generate_factor 代码提交 /backtest/run_mining/ 端点评估。

        返回值的 metrics 会被标准化为 alpha_digger / factor_library 期望的字段名。
        """
        if not start_date:
            from datetime import date, timedelta
            start_date = (date.today() - timedelta(days=180)).strftime("%Y-%m-%d")
        if not end_date:
            from datetime import date
            end_date = date.today().strftime("%Y-%m-%d")
        resp = await self._post("/backtest/run_mining/", {
            "factor_code": factor_code,
            "start_date": start_date,
            "end_date": end_date,
        })
        metrics = resp.get("metrics")
        if metrics and isinstance(metrics, dict):
            resp["metrics"] = self._normalize_mining_metrics(metrics)
        return resp

    @staticmethod
    def _normalize_mining_metrics(m: dict) -> dict:
        """将 139 run_mining 返回的 metrics 字段映射为 factor_library 期望的格式。

        API 返回                  ->  内部使用
        sharpe_ratio              ->  sharpe
        factor_ic                 ->  mean_ic
        sortino_ratio             ->  ir  (Information Ratio 近似)
        total_trades              ->  trades
        win_rate_pct (百分比)      ->  win_rate (小数)
        max_drawdown_pct          ->  max_drawdown
        annualized_volatility_pct ->  turnover (近似)
        """
        norm = dict(m)
        norm.setdefault("sharpe", m.get("sharpe_ratio", 0))
        norm.setdefault("mean_ic", m.get("factor_ic", 0))
        norm.setdefault("ir", m.get("sortino_ratio", 0))
        norm.setdefault("trades", m.get("total_trades", 0))
        wp = m.get("win_rate_pct", 0)
        norm.setdefault("win_rate", wp / 100.0 if wp > 1 else wp)
        norm.setdefault("max_drawdown", abs(m.get("max_drawdown_pct", 0)) / 100.0)
        norm.setdefault("turnover", m.get("annualized_volatility_pct", 0) / 100.0)
        return norm

    async def run_factor_mining_cscv(self, factor_code: str,
                                     total_days: int = 360,
                                     k: int = 4,
                                     timeout: int = 900) -> dict:
        """CSCV 多窗口回测: 将 total_days 均分为 k 段分别回测，计算 PBO 过拟合概率.

        只在初筛通过后调用 (额外 k-1 次 API 请求)。

        Returns:
            dict with keys:
              - metrics: 最末窗口指标 + pbo_score + cscv_windows
              - window_sharpes: list of k Sharpe values
              - pbo_score: float ∈ [0,1]
        """
        from datetime import date, timedelta
        from agents.factor_quality import compute_pbo_cscv

        end_dt = date.today()
        start_dt = end_dt - timedelta(days=total_days)
        window_size = total_days // k

        windows = []
        for i in range(k):
            ws = start_dt + timedelta(days=i * window_size)
            we = ws + timedelta(days=window_size - 1)
            try:
                resp = await self._post("/backtest/run_mining/", {
                    "factor_code": factor_code,
                    "start_date": ws.strftime("%Y-%m-%d"),
                    "end_date": we.strftime("%Y-%m-%d"),
                })
            except Exception as e:
                logger.warning("CSCV 窗口 %d 回测失败: %s", i + 1, e)
                windows.append({"window": i + 1, "sharpe": 0.0, "metrics": {}})
                continue

            raw = resp.get("metrics") or {}
            norm = self._normalize_mining_metrics(raw)
            windows.append({
                "window": i + 1,
                "start": ws.strftime("%Y-%m-%d"),
                "end": we.strftime("%Y-%m-%d"),
                "sharpe": norm.get("sharpe", 0.0),
                "metrics": norm,
            })

        window_sharpes = [w["sharpe"] for w in windows]
        pbo_score = compute_pbo_cscv(window_sharpes)

        # 主 metrics: 用全期回测补充, 注入 pbo_score
        main_metrics = windows[-1]["metrics"].copy() if windows else {}
        main_metrics["pbo_score"] = pbo_score
        main_metrics["cscv_windows"] = windows

        logger.info("CSCV k=%d  Sharpes=%s  PBO=%.3f", k, window_sharpes, pbo_score)
        return {
            "status": "ok",
            "metrics": main_metrics,
            "window_sharpes": window_sharpes,
            "pbo_score": pbo_score,
        }

    async def run_intraday_scan(self, code: str, stock_pool: list[str] | None = None,
                                timeout: int = 30) -> dict:
        return await self._post("/intraday/scan/", {
            "strategy_code": code,
            "stock_pool": stock_pool or [],
            "timeout": timeout,
        })

    async def save_strategy(self, title: str, strategy_code: str = "",
                            backtest_metrics: dict = None, decision_report: str = "",
                            risk_review: str = "", model_used: str = "",
                            status: str = "PENDING", topic: str = "",
                            attempts: int = 1,
                            rounds_data: list[dict] | None = None) -> dict:
        body = {
            "title": title, "topic": topic, "status": status,
            "attempts": attempts, "strategy_code": strategy_code,
            "backtest_metrics": backtest_metrics or {},
            "risk_review": risk_review, "decision_report": decision_report,
            "model_used": model_used,
        }
        if rounds_data is not None:
            body["rounds_data"] = rounds_data
        return await self._post("/strategy/save/", body)

    async def get_ledger(self, status: str = "", page: int = 1) -> dict:
        params = {"page": page}
        if status:
            params["status"] = status
        return await self._get("/ledger/", params)

    async def get_ledger_detail(self, ledger_id: int) -> dict:
        return await self._get(f"/ledger/{ledger_id}/")

    async def get_system_schema(self) -> dict:
        """从 139 获取实时 System Schema（ClickHouse/DolphinDB 表结构 + API 列表）。

        对应 139 端点: GET /api/bridge/system/schema
        返回示例:
        {
            "clickhouse": {"daily_kline": ["trade_date", "ts_code", ...]},
            "dolphindb": {"stock_realtime": ["update_time", "ts_code", ...]}
        }
        """
        return await self._get("/system/schema")

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_bridge_client: Optional[BridgeClient] = None


def get_bridge_client() -> BridgeClient:
    global _bridge_client
    if _bridge_client is None:
        _bridge_client = BridgeClient()
    return _bridge_client
