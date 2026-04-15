"""
Yahoo Finance 备用数据源 (第4级回退)

适用场景: Bridge API + 旧版 API + AKShare 全部不可用时的最后兜底。
数据范围: A股主要指数（上证、深证、沪深300、创业板指）+ 个股基本行情。
数据延迟: Yahoo Finance 有 15-20分钟延迟，仅用于「有总比没有好」的降级场景。

使用 httpx 直接调用 Yahoo Finance Query API，无需安装 yfinance 包。
支持的端点映射:
  snapshot  → 主要指数快照（上证、深证、沪深300）
  limitup   → 无真实数据，返回 None（涨停板数据 Yahoo 不提供）
  limitstep → 无真实数据，返回 None
"""

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("data_sources.yahoo_finance")

# A股主要指数的 Yahoo 代码
_INDEX_SYMBOLS = {
    "sh": "000001.SS",    # 上证综指
    "sz": "399001.SZ",    # 深证成指
    "csi300": "000300.SS", # 沪深300
    "gem": "399006.SZ",   # 创业板指
}

_YF_QUERY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}
_TIMEOUT = 8.0

# 简单内存缓存（15分钟 TTL，避免重复请求）
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 900  # 15分钟


def _yf_quote(symbol: str) -> Optional[dict]:
    """获取单个 Yahoo Finance 标的行情"""
    now = time.time()
    if symbol in _cache:
        ts, data = _cache[symbol]
        if now - ts < _CACHE_TTL:
            return data

    try:
        transport = httpx.HTTPTransport(proxy=None)
        with httpx.Client(timeout=_TIMEOUT, transport=transport) as client:
            resp = client.get(
                _YF_QUERY_URL.format(symbol=symbol),
                headers=_HEADERS,
                params={"interval": "1d", "range": "1d"},
            )
        resp.raise_for_status()
        raw = resp.json()
        result = raw.get("chart", {}).get("result", [])
        if not result:
            return None
        r = result[0]
        meta = r.get("meta", {})
        data = {
            "symbol": symbol,
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "price": meta.get("regularMarketPrice"),
            "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "pct_chg": None,
            "ts": int(meta.get("regularMarketTime", now)),
        }
        if data["price"] and data["prev_close"]:
            data["pct_chg"] = round(
                (data["price"] - data["prev_close"]) / data["prev_close"] * 100, 2
            )
        _cache[symbol] = (now, data)
        return data
    except Exception as e:
        logger.debug("Yahoo Finance fetch failed [%s]: %s", symbol, e)
        return None


def _get_indices_snapshot() -> Optional[dict]:
    """获取A股主要指数快照，格式化为 source_router 期望的格式"""
    results = []
    for label, symbol in _INDEX_SYMBOLS.items():
        q = _yf_quote(symbol)
        if q:
            pct = q.get("pct_chg")
            results.append({
                "ts_code": symbol,
                "ts_name": q.get("name", label),
                "pct_change": pct,
                "rank": len(results) + 1,
                "concept": label,
                "_source": "yahoo_finance",
            })

    if not results:
        return None

    lines = ["📉 [Yahoo Finance 降级数据 — 约15分钟延迟]"]
    for r in results:
        pct = r.get("pct_change")
        pct_str = f"{pct:+.2f}%" if pct is not None else "N/A"
        lines.append(f"  {r['ts_name']}: {pct_str}")

    return {
        "count": len(results),
        "results": results,
        "summary": "\n".join(lines),
        "_degraded": True,
        "_source": "yahoo_finance",
    }


# ── 端点映射 ──────────────────────────────────────────

YAHOO_ENDPOINT_MAP: dict[str, callable] = {
    "snapshot": lambda **kw: _get_indices_snapshot(),
    "ths-hot": lambda **kw: _get_indices_snapshot(),
    # 涨停板、连板、板块数据 Yahoo 不提供，返回 None 触发降级提示
    "limitup": lambda **kw: None,
    "limitstep": lambda **kw: None,
    "concept-boards": lambda **kw: None,
    "concepts": lambda **kw: None,
}


def yahoo_get(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """统一调用接口，与 source_router 其他 source 保持一致的签名"""
    func = YAHOO_ENDPOINT_MAP.get(endpoint)
    if func is None:
        return None
    try:
        return func(**(params or {}))
    except Exception as e:
        logger.debug("Yahoo Finance endpoint %s error: %s", endpoint, e)
        return None
