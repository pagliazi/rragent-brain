"""
数据源路由器 — 三级回退
优先级: Bridge API (139) → 旧版 API (138) → AKShare

Bridge API 端点映射:
  limitup       → /bridge/limitup/
  limitstep     → /bridge/limitup/?type=step
  concept-boards → /bridge/concepts/
  ths-hot       → /bridge/snapshot/
  snapshot      → /bridge/snapshot/
  kline         → /bridge/kline/
  dragon-tiger  → /bridge/dragon-tiger/
  sentiment     → /bridge/sentiment/
  indicators    → /bridge/indicators/

用法:
    from agents.data_sources import api_get_with_fallback
    data = api_get_with_fallback("limitup", params={"page_size": 20})
"""

import logging
import os
import time
from typing import Optional

import httpx

from agents.memory.config import PRIMARY_API_BASE, AKSHARE_CONFIG

logger = logging.getLogger("data_sources.router")

BRIDGE_ENDPOINT_MAP = {
    "limitup": "limitup",
    "limitstep": "limitup",
    "concept-boards": "concepts",
    "ths-hot": "snapshot",
    "snapshot": "snapshot",
    "kline": "kline",
    "dragon-tiger": "dragon-tiger",
    "sentiment": "sentiment",
    "indicators": "indicators",
    "concepts": "concepts",
    "presets": "presets",
    "news": "sentiment",
}


class DataSourceRouter:
    """
    数据源三级回退路由器
    状态机: BRIDGE → (失败 N 次) → PRIMARY → (失败 N 次) → AKSHARE → (定时回切)
    """

    BRIDGE = "bridge"
    PRIMARY = "primary"
    AKSHARE = "akshare"
    YAHOO = "yahoo"

    def __init__(self):
        self._current = self.BRIDGE
        self._fail_count = 0
        self._failover_ts: float = 0
        self._bridge_threshold = 1
        self._primary_threshold = AKSHARE_CONFIG.get("failover_threshold", 3)
        self._retry_after = AKSHARE_CONFIG.get("retry_primary_after_seconds", 300)

    @property
    def current_source(self) -> str:
        if self._current in (self.PRIMARY, self.AKSHARE):
            if time.time() - self._failover_ts > self._retry_after:
                logger.info("Retry Bridge API after failover cooldown")
                self._current = self.BRIDGE
                self._fail_count = 0
        return self._current

    def report_success(self, source: str = ""):
        self._fail_count = 0
        if source and source != self._current:
            return
        if self._current != self.BRIDGE:
            logger.info("Bridge API recovered, switching back")
            self._current = self.BRIDGE

    def report_failure(self):
        self._fail_count += 1
        if self._current == self.BRIDGE and self._fail_count >= self._bridge_threshold:
            logger.warning("Bridge API failed %d times, switching to Primary API", self._fail_count)
            self._current = self.PRIMARY
            self._fail_count = 0
            self._failover_ts = time.time()
        elif self._current == self.PRIMARY and self._fail_count >= self._primary_threshold:
            logger.warning("Primary API failed %d times, switching to AKShare", self._fail_count)
            self._current = self.AKSHARE
            self._failover_ts = time.time()

    def get_status(self) -> dict:
        return {
            "current_source": self._current,
            "fail_count": self._fail_count,
            "failover_ts": self._failover_ts,
            "akshare_enabled": AKSHARE_CONFIG.get("enabled", True),
            "yahoo_enabled": True,
        }


_router = DataSourceRouter()


def _normalize_bridge_response(endpoint: str, raw: dict) -> dict:
    """将 Bridge API 响应归一化为 Agent 期望的 {count, results} 格式"""
    if "results" in raw:
        return raw

    if endpoint in ("limitup", "limitstep"):
        key = "limit_up" if endpoint == "limitup" else "limit_up"
        items = raw.get(key, [])
        if endpoint == "limitstep":
            items = [i for i in items if i.get("up_stat", "1/1").split("/")[0] not in ("0", "1")]
            if not items:
                items = raw.get(key, [])
        for item in items:
            if "fd_amount" not in item and "limit_amount" in item:
                item["fd_amount"] = item["limit_amount"]
            if "limit_times" not in item:
                stat = item.get("up_stat", "")
                if "/" in str(stat):
                    try:
                        item["limit_times"] = int(str(stat).split("/")[0])
                    except ValueError:
                        item["limit_times"] = 1
        return {"count": len(items), "results": items}

    if endpoint in ("concept-boards", "concepts"):
        items = raw.get("concepts", [])
        for item in items:
            if "board_name" not in item and "name" in item:
                item["board_name"] = item["name"]
            if "leading_stock_name" not in item and "leading_stock" in item:
                item["leading_stock_name"] = item["leading_stock"]
        return {"count": raw.get("count", len(items)), "results": items}

    if endpoint in ("ths-hot", "snapshot"):
        items = raw.get("hot_stocks", raw.get("stocks", []))
        if not items and isinstance(raw.get("sentiment"), dict):
            items = []
        if items:
            for item in items:
                if "ts_name" not in item and "name" in item:
                    item["ts_name"] = item["name"]
        return {"count": len(items), "results": items, "summary": raw}

    if endpoint in ("news", "sentiment"):
        items = raw.get("news", raw.get("results", []))
        return {"count": len(items), "results": items}

    return raw


def _bridge_api_get(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """调用 Bridge API (192.168.1.139) — 同步签名 + httpx.get"""
    import hashlib, hmac, time as _time
    bridge_secret = os.getenv("BRIDGE_SECRET", "")
    bridge_base = os.getenv("BRIDGE_BASE_URL", "http://192.168.1.139:8001/api/bridge")
    if not bridge_secret:
        return None
    try:
        bridge_endpoint = BRIDGE_ENDPOINT_MAP.get(endpoint, endpoint)
        bridge_params = dict(params or {})
        if endpoint == "limitstep":
            bridge_params["type"] = "step"

        path = f"/{bridge_endpoint.strip('/')}/"
        ts = str(int(_time.time()))
        sig = hmac.new(bridge_secret.encode(), f"{ts}{path}".encode(), hashlib.sha256).hexdigest()
        headers = {"X-Bridge-Timestamp": ts, "X-Bridge-Key": sig}

        url = f"{bridge_base.rstrip('/')}{path}"
        transport = httpx.HTTPTransport(proxy=None)
        with httpx.Client(timeout=10, transport=transport) as client:
            resp = client.get(url, params=bridge_params, headers=headers)
        resp.raise_for_status()
        raw = resp.json()
        return _normalize_bridge_response(endpoint, raw)
    except ImportError:
        logger.warning("bridge_client not available, skipping Bridge API")
        return None
    except Exception as e:
        logger.warning("Bridge API error [%s]: %s", endpoint, e)
        return None


def _primary_api_get(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """调用旧版 API (192.168.1.138)"""
    try:
        url = f"{PRIMARY_API_BASE}/{endpoint.strip('/')}/"
        transport = httpx.HTTPTransport(proxy=None)
        with httpx.Client(timeout=10, transport=transport) as client:
            resp = client.get(url, params=params or {})
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Primary API error [%s]: %s", endpoint, e)
        return None


def _akshare_get(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """调用 AKShare 备用数据源"""
    if not AKSHARE_CONFIG.get("enabled"):
        return None
    try:
        from agents.data_sources.akshare_source import AKSHARE_ENDPOINT_MAP
        func = AKSHARE_ENDPOINT_MAP.get(endpoint)
        if func is None:
            logger.debug("No AKShare mapping for endpoint: %s", endpoint)
            return None
        return func(**(params or {}))
    except ImportError:
        logger.warning("akshare not installed, backup source unavailable")
        return None
    except Exception as e:
        logger.warning("AKShare error [%s]: %s", endpoint, e)
        return None


def _yahoo_get(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """调用 Yahoo Finance 第4级备用数据源（仅用于全部本地源不可用时）"""
    try:
        from agents.data_sources.yahoo_finance import yahoo_get
        return yahoo_get(endpoint, params)
    except Exception as e:
        logger.debug("Yahoo Finance error [%s]: %s", endpoint, e)
        return None


def api_get_with_fallback(
    endpoint: str,
    params: Optional[dict] = None,
    force_source: Optional[str] = None,
) -> Optional[dict]:
    """
    带三级降级的数据获取: Bridge → Primary → AKShare
    force_source: "bridge" | "primary" | "akshare" | None(自动)
    """
    source = force_source or _router.current_source
    fetchers = []

    if source == DataSourceRouter.BRIDGE:
        fetchers = [
            (DataSourceRouter.BRIDGE, _bridge_api_get),
            (DataSourceRouter.PRIMARY, _primary_api_get),
            (DataSourceRouter.AKSHARE, _akshare_get),
            (DataSourceRouter.YAHOO, _yahoo_get),
        ]
    elif source == DataSourceRouter.PRIMARY:
        fetchers = [
            (DataSourceRouter.PRIMARY, _primary_api_get),
            (DataSourceRouter.AKSHARE, _akshare_get),
            (DataSourceRouter.YAHOO, _yahoo_get),
        ]
    else:
        fetchers = [
            (DataSourceRouter.AKSHARE, _akshare_get),
            (DataSourceRouter.PRIMARY, _primary_api_get),
            (DataSourceRouter.YAHOO, _yahoo_get),
        ]

    for src_name, fetcher in fetchers:
        data = fetcher(endpoint, params)
        if data is not None:
            results = data.get("results") if isinstance(data, dict) else None
            if results is not None and len(results) == 0 and src_name == DataSourceRouter.BRIDGE:
                logger.debug("Bridge returned empty results for %s, trying next source", endpoint)
                continue
            if src_name in (DataSourceRouter.BRIDGE, DataSourceRouter.PRIMARY):
                _router.report_success(src_name)
            return data
        if src_name == _router._current:
            _router.report_failure()

    return None


def get_router() -> DataSourceRouter:
    return _router
