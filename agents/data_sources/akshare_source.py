"""
AKShare 备用数据源适配器
将 AKShare 的 A 股数据接口统一适配为与主 API 兼容的返回格式。
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("data_sources.akshare")

_ak = None


def _get_ak():
    global _ak
    if _ak is None:
        try:
            import akshare as ak
            _ak = ak
        except ImportError:
            logger.error("akshare not installed. Run: pip install akshare")
            raise
    return _ak


def get_limitup(page_size: int = 20, **kwargs) -> Optional[dict]:
    """涨停板数据 (AKShare: stock_zt_pool_em)"""
    try:
        ak = _get_ak()
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=today)
        if df is None or df.empty:
            return {"count": 0, "results": []}

        results = []
        for _, row in df.head(page_size).iterrows():
            results.append({
                "ts_code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "pct_chg": float(row.get("涨跌幅", 0)),
                "industry": str(row.get("所属行业", "")),
                "fd_amount": float(row.get("封单金额", 0)),
                "limit_times": int(row.get("连板数", 1)),
            })
        return {"count": len(df), "results": results}
    except Exception as e:
        logger.warning(f"AKShare get_limitup failed: {e}")
        return None


def get_limitstep(page_size: int = 15, **kwargs) -> Optional[dict]:
    """连板数据 (AKShare: stock_zt_pool_strong_em)"""
    try:
        ak = _get_ak()
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_strong_em(date=today)
        if df is None or df.empty:
            return {"count": 0, "results": []}

        results = []
        for _, row in df.head(page_size).iterrows():
            results.append({
                "ts_code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "pct_chg": float(row.get("涨跌幅", 0)),
                "industry": str(row.get("所属行业", "")),
                "up_stat": str(row.get("涨停统计", "")),
                "limit_times": int(row.get("连板数", 1)),
            })
        return {"count": len(df), "results": results}
    except Exception as e:
        logger.warning(f"AKShare get_limitstep failed: {e}")
        return None


def get_concept_boards(page_size: int = 20, **kwargs) -> Optional[dict]:
    """概念板块 (AKShare: stock_board_concept_name_em)"""
    try:
        ak = _get_ak()
        df = ak.stock_board_concept_name_em()
        if df is None or df.empty:
            return {"count": 0, "results": []}

        df = df.sort_values("涨跌幅", ascending=False)
        results = []
        for _, row in df.head(page_size).iterrows():
            results.append({
                "board_name": str(row.get("板块名称", "")),
                "pct_chg": float(row.get("涨跌幅", 0)),
                "up_count": int(row.get("上涨家数", 0)),
                "down_count": int(row.get("下跌家数", 0)),
                "leading_stock_name": str(row.get("领涨股票", "")),
            })
        return {"count": len(df), "results": results}
    except Exception as e:
        logger.warning(f"AKShare get_concept_boards failed: {e}")
        return None


def get_hot_stocks(page_size: int = 20, **kwargs) -> Optional[dict]:
    """热门股票 (AKShare: stock_hot_rank_em)"""
    try:
        ak = _get_ak()
        df = ak.stock_hot_rank_em()
        if df is None or df.empty:
            return {"count": 0, "results": []}

        results = []
        for _, row in df.head(page_size).iterrows():
            results.append({
                "rank": int(row.get("当前排名", 0)),
                "ts_code": str(row.get("代码", "")),
                "ts_name": str(row.get("股票名称", "")),
                "pct_change": float(row.get("涨跌幅", 0)),
            })
        return {"count": len(df), "results": results}
    except Exception as e:
        logger.warning(f"AKShare get_hot_stocks failed: {e}")
        return None


def get_news(keyword: str = "", page_size: int = 20, **kwargs) -> Optional[dict]:
    """财经新闻 (AKShare: stock_news_em)"""
    try:
        ak = _get_ak()
        df = ak.stock_news_em(symbol="")
        if df is None or df.empty:
            return {"count": 0, "results": []}

        if keyword:
            mask = df.apply(lambda r: keyword in str(r.get("新闻标题", "")), axis=1)
            df = df[mask]

        results = []
        for _, row in df.head(page_size).iterrows():
            results.append({
                "title": str(row.get("新闻标题", "")),
                "content": str(row.get("新闻内容", ""))[:500],
                "pub_date": str(row.get("发布时间", "")),
                "source": str(row.get("文章来源", "")),
            })
        return {"count": len(results), "results": results}
    except Exception as e:
        logger.warning(f"AKShare get_news failed: {e}")
        return None


AKSHARE_ENDPOINT_MAP = {
    "limitup": get_limitup,
    "limitstep": get_limitstep,
    "concept-boards": get_concept_boards,
    "ths-hot": get_hot_stocks,
    "news": get_news,
    "sentiment": get_news,
}
