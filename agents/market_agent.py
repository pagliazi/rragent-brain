"""
MarketAgent — 行情监控
职责: 涨停/连板/板块/热股查询、定时推送、异常检测
数据源: Bridge API (139, 主) → 旧版 API (138) → AKShare (三级回退)
不需要 LLM
"""
from __future__ import annotations


import asyncio
import logging
import os
import time

import httpx

from agents.base import BaseAgent, AgentMessage, run_agent
from agents.data_sources import api_get_with_fallback

logger = logging.getLogger("agent.market")


def api_get(endpoint: str, params: dict | None = None) -> dict | None:
    return api_get_with_fallback(endpoint, params)


def fmt_limitup(data: dict) -> str:
    results = data.get("results", [])
    if not results:
        return "暂无涨停数据"
    lines = [f"🔴 涨停板 ({data.get('count', 0)} 只)\n"]
    for i, r in enumerate(results[:20], 1):
        times = r.get("limit_times", 1)
        tag = f" [{times}连板]" if times and times > 1 else ""
        fd = int(float(r.get("fd_amount", 0) or 0) / 10000)
        lines.append(
            f"{i}. {r.get('name','')} ({r.get('ts_code','')}) "
            f"{r.get('pct_chg','')}% 封单:{fd}万 {r.get('industry','')}{tag}"
        )
    return "\n".join(lines)


def fmt_limitstep(data: dict) -> str:
    results = data.get("results", [])
    if not results:
        return "暂无连板数据"
    lines = ["🔥 连板股\n"]
    for i, r in enumerate(results[:15], 1):
        lines.append(
            f"{i}. {r.get('name','')} ({r.get('ts_code','')}) "
            f"{r.get('pct_chg','')}% {r.get('up_stat','')} {r.get('industry','')}"
        )
    return "\n".join(lines)


def fmt_concepts(data: dict) -> str:
    results = data.get("results", [])
    if not results:
        return "暂无板块数据"
    lines = ["📊 概念板块涨幅榜\n"]
    for i, r in enumerate(results[:15], 1):
        lines.append(
            f"{i}. {r.get('board_name','')} {r.get('pct_chg','')}% "
            f"涨:{r.get('up_count',0)} 跌:{r.get('down_count',0)} "
            f"领涨:{r.get('leading_stock_name','')}"
        )
    return "\n".join(lines)


def fmt_hot(data: dict) -> str:
    import json as _json
    results = data.get("results", [])
    if not results:
        return "暂无热股数据"
    lines = ["🔥 同花顺热股\n"]
    for i, r in enumerate(results[:15], 1):
        pct = r.get("pct_change")
        pct_str = f"{pct}%" if pct is not None else ""
        concept = r.get("concept", "")
        if concept and concept != "null":
            try:
                tags = _json.loads(concept)
                concept = " ".join(tags[:2]) if isinstance(tags, list) else ""
            except Exception:
                concept = ""
        lines.append(
            f"{i}. #{r.get('rank','')} {r.get('ts_name','')} ({r.get('ts_code','')}) "
            f"{pct_str} {concept}"
        )
    return "\n".join(lines)


def get_summary() -> str:
    """汇总行情摘要，包含数据时间状态"""
    from agents.market_time import get_analysis_context
    ctx = get_analysis_context()
    parts = [f"⏰ {ctx['freshness_label']} ({ctx['timestamp']} {ctx['phase_cn']})\n"]
    zt = api_get("limitup", {"ordering": "-pct_chg", "page_size": 10})
    if zt and zt.get("results"):
        parts.append(f"🔴 涨停 {zt.get('count',0)} 只:")
        for r in zt["results"][:10]:
            t = f" [{r.get('limit_times',1)}连板]" if (r.get('limit_times') or 1) > 1 else ""
            parts.append(f"  {r.get('name','')} {r.get('pct_chg','')}% {r.get('industry','')}{t}")
        parts.append("")

    bk = api_get("concept-boards", {"ordering": "-pct_chg", "page_size": 5})
    if bk and bk.get("results"):
        parts.append("📊 板块 TOP5:")
        for r in bk["results"][:5]:
            parts.append(f"  {r.get('board_name','')} {r.get('pct_chg','')}% 领涨:{r.get('leading_stock_name','')}")
        parts.append("")

    lb = api_get("limitstep", {"ordering": "-limit_times", "page_size": 5})
    if lb and lb.get("results"):
        parts.append("🔥 连板:")
        for r in lb["results"][:5]:
            parts.append(f"  {r.get('name','')} {r.get('up_stat','')} {r.get('industry','')}")

    return "\n".join(parts) if parts else "暂无数据"


def get_all_raw() -> dict:
    """获取全部原始数据供 AnalysisAgent 使用，附带时间上下文"""
    from agents.market_time import get_analysis_context
    ctx = get_analysis_context()
    return {
        "limitup": api_get("limitup", {"ordering": "-pct_chg", "page_size": 30}),
        "limitstep": api_get("limitstep", {"ordering": "-limit_times", "page_size": 15}),
        "concepts": api_get("concept-boards", {"ordering": "-pct_chg", "page_size": 20}),
        "hot": api_get("ths-hot", {"market": "热股", "ordering": "rank", "page_size": 20}),
        "_time_context": ctx,
    }


class MarketAgent(BaseAgent):
    name = "market"

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "get_limitup":
            data = api_get("limitup", {"ordering": "-pct_chg", "page_size": params.get("page_size", 20)})
            if data is None:
                await self.reply(msg, error="API 无响应")
            else:
                await self.reply(msg, result={"text": fmt_limitup(data), "raw": data})

        elif action == "get_limitstep":
            data = api_get("limitstep", {"ordering": "-limit_times", "page_size": params.get("page_size", 15)})
            if data is None:
                await self.reply(msg, error="API 无响应")
            else:
                await self.reply(msg, result={"text": fmt_limitstep(data), "raw": data})

        elif action == "get_concepts":
            data = api_get("concept-boards", {"ordering": "-pct_chg", "page_size": params.get("page_size", 15)})
            if data is None:
                await self.reply(msg, error="API 无响应")
            else:
                await self.reply(msg, result={"text": fmt_concepts(data), "raw": data})

        elif action == "get_hot":
            data = api_get("ths-hot", {"market": "热股", "ordering": "rank", "page_size": params.get("page_size", 15)})
            if data is None:
                await self.reply(msg, error="API 无响应")
            else:
                await self.reply(msg, result={"text": fmt_hot(data), "raw": data})

        elif action == "get_summary":
            await self.reply(msg, result={"text": get_summary()})

        elif action == "get_all_raw":
            await self.reply(msg, result=get_all_raw())

        else:
            await self.reply(msg, error=f"Unknown action: {action}")


if __name__ == "__main__":
    run_agent(MarketAgent())
