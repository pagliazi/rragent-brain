"""
agents.data_sources — 数据源切换引擎
主数据源: 192.168.1.138 本地 API
备用数据源: AKShare (A股公开数据)
自动降级: 主 API 连续失败 N 次后切换到 AKShare，定时回切。
"""

from agents.data_sources.source_router import DataSourceRouter, api_get_with_fallback

__all__ = ["DataSourceRouter", "api_get_with_fallback"]
