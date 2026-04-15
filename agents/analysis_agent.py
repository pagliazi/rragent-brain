"""
AnalysisAgent — AI 深度分析
职责: 基于行情数据做深度分析、板块轮动研判、趋势分析
数据源: 从 MarketAgent 获取数据，DeepSeek 分析
记忆: 三层拓扑记忆（向量+图谱+时序），跨 Agent recall 舆情
"""

import logging
import os
from datetime import date

from agents.base import BaseAgent, AgentMessage, run_agent
from agents.memory.memory_mixin import MemoryMixin

logger = logging.getLogger("agent.analysis")


def _build_data_text(raw: dict) -> str:
    parts = []

    zt = raw.get("limitup")
    if zt and zt.get("results"):
        lines = [f"涨停板（{zt.get('count',0)}只）:"]
        for r in zt["results"]:
            t = f" {r.get('limit_times',1)}连板" if (r.get('limit_times') or 1) > 1 else ""
            lines.append(
                f"  {r.get('name','')} {r.get('ts_code','')} {r.get('pct_chg','')}% "
                f"行业:{r.get('industry','')}{t} 封单:{r.get('fd_amount','')}"
            )
        parts.append("\n".join(lines))

    lb = raw.get("limitstep")
    if lb and lb.get("results"):
        lines = ["连板股:"]
        for r in lb["results"]:
            lines.append(
                f"  {r.get('name','')} {r.get('ts_code','')} {r.get('up_stat','')} "
                f"行业:{r.get('industry','')} 涨幅:{r.get('pct_chg','')}%"
            )
        parts.append("\n".join(lines))

    bk = raw.get("concepts")
    if bk and bk.get("results"):
        lines = ["概念板块涨幅榜:"]
        for r in bk["results"]:
            lines.append(
                f"  {r.get('board_name','')} {r.get('pct_chg','')}% "
                f"涨:{r.get('up_count',0)} 跌:{r.get('down_count',0)} "
                f"领涨:{r.get('leading_stock_name','')}"
            )
        parts.append("\n".join(lines))

    hot = raw.get("hot")
    if hot and hot.get("results"):
        import json as _json
        lines = ["同花顺热股:"]
        for r in hot["results"]:
            pct = r.get("pct_change")
            concept = r.get("concept", "")
            if concept and concept != "null":
                try:
                    tags = _json.loads(concept)
                    concept = " ".join(tags[:2]) if isinstance(tags, list) else ""
                except Exception:
                    concept = ""
            lines.append(
                f"  #{r.get('rank','')} {r.get('ts_name','')} ({r.get('ts_code','')}) "
                f"涨幅:{pct}% {concept}"
            )
        parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else "无数据"


def _extract_sectors(data_text: str) -> list[str]:
    """从数据文本提取主要板块关键词"""
    sectors = []
    for line in data_text.split("\n"):
        if "行业:" in line:
            industry = line.split("行业:")[1].split()[0].strip()
            if industry and industry not in sectors:
                sectors.append(industry)
        if "板块" in line or "概念" in line:
            parts = line.strip().split()
            if parts:
                name = parts[0].strip()
                if len(name) > 1 and name not in sectors:
                    sectors.append(name)
    return sectors[:5]


class AnalysisAgent(BaseAgent, MemoryMixin):
    name = "analysis"

    def __init__(self):
        super().__init__()
        self._init_memory()

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "ask":
            await self._handle_ask(msg, params)
        elif action == "brief_analysis":
            await self._handle_brief(msg, params)
        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    async def _handle_ask(self, msg: AgentMessage, params: dict):
        question = params.get("question", "")
        market_data = params.get("market_data")

        if market_data is None:
            resp = await self.send("market", "get_all_raw")
            if resp.error:
                await self.reply(msg, error=f"获取行情数据失败: {resp.error}")
                return
            market_data = resp.result

        data_text = _build_data_text(market_data) if isinstance(market_data, dict) else str(market_data)

        from agents.market_time import get_analysis_context, format_time_context_block
        time_ctx = (market_data.get("_time_context") if isinstance(market_data, dict) else None) or get_analysis_context()
        time_block = format_time_context_block(time_ctx)

        recall_days = 7 if time_ctx["is_realtime"] else 30
        history_context = ""
        recall_query = question or data_text[:200]
        memories = await self.recall(recall_query, top_k=3, cross_agent=True, time_range_days=recall_days)
        reminds = await self.get_reminds(max_items=2)
        if memories or reminds:
            history_context = self.format_recall_context(memories, reminds=reminds) + "\n\n"

        raw_prompt = (
            f"{time_block}\n\n"
            f"{history_context}"
            f"以下是行情数据（{time_ctx['freshness_label']}）：\n\n"
            f"{data_text}\n\n"
            f"用户问题: {question}\n\n"
            f"请基于以上数据、时间上下文和历史记忆进行专业分析。"
            f"{'盘中分析应侧重实时动态变化和短线信号。' if time_ctx['is_realtime'] else '非盘中分析应侧重趋势研判和中期展望。'}"
            f"\n注意：这不构成投资建议。"
        )
        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            messages = [
                {"role": "system", "content": self.soul or "你是一位专业的A股市场分析师。"},
                {"role": "user", "content": raw_prompt},
            ]
            reply_text = await router.chat(messages, task_type="analysis")
            if not reply_text:
                await self.reply(msg, error="所有 LLM 提供商均不可用")
                return

            sectors = _extract_sectors(data_text)
            limitup_count = 0
            if isinstance(market_data, dict):
                zt = market_data.get("limitup")
                if zt:
                    limitup_count = zt.get("count", 0)

            relations = []
            if memories:
                for mem in memories[:2]:
                    if mem.source_agent == "news":
                        relations.append((mem.id, "references"))

            mem_id = await self.remember(
                content=reply_text,
                metadata={
                    "type": "ask",
                    "question": question[:200],
                    "date": date.today().isoformat(),
                    "limitup_count": limitup_count,
                    "main_sectors": ",".join(sectors),
                },
                relations=relations,
            )

            if mem_id and sectors:
                existing = await self.recall(" ".join(sectors), top_k=3, time_range_days=30)
                for old_mem in existing:
                    if old_mem.id != mem_id and old_mem.source_agent == self.name:
                        await self.connect(mem_id, old_mem.id, "same_topic")

            await self.reply(msg, result={"text": f"📊 分析结果:\n\n{reply_text}"})
        except Exception as e:
            logger.error(f"LLM error: {e}", exc_info=True)
            await self.reply(msg, error=f"分析失败: {e}")

    async def _handle_brief(self, msg: AgentMessage, params: dict):
        market_data = params.get("market_data")
        if market_data is None:
            resp = await self.send("market", "get_all_raw")
            if resp.error:
                await self.reply(msg, error=resp.error)
                return
            market_data = resp.result

        data_text = _build_data_text(market_data) if isinstance(market_data, dict) else str(market_data)

        from agents.market_time import get_analysis_context, format_time_context_block
        time_ctx = (market_data.get("_time_context") if isinstance(market_data, dict) else None) or get_analysis_context()
        time_block = format_time_context_block(time_ctx)

        history_context = ""
        memories = await self.recall(data_text[:200], top_k=2, time_range_days=7)
        reminds = await self.get_reminds(max_items=2)
        if memories or reminds:
            history_context = self.format_recall_context(memories, max_items=2, reminds=reminds) + "\n\n"

        phase_instruction = {
            "morning": "盘中简报: 重点描述当前盘面动态、主攻方向和资金态度。",
            "afternoon": "盘中简报: 聚焦尾盘走势变化、封板强度和收盘预判。",
            "lunch_break": "午间简报: 总结上午走势特征，预判下午方向。",
            "post_close": "收盘简报: 全天总结，评估情绪周期和明日展望。",
        }.get(time_ctx["phase"], "简要总结市场特征和主要热点。")

        raw_prompt = (
            f"{time_block}\n\n"
            f"{history_context}"
            f"以下是行情数据（{time_ctx['freshness_label']}）:\n\n{data_text}\n\n"
            f"{phase_instruction}\n如有历史记忆参考，对比趋势变化。请用3-5句话概括。"
        )
        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            messages = [
                {"role": "system", "content": self.soul or "你是一位专业的A股市场分析师。"},
                {"role": "user", "content": raw_prompt},
            ]
            reply_text = await router.chat(messages, task_type="brief")
            if not reply_text:
                await self.reply(msg, error="所有 LLM 提供商均不可用")
                return

            await self.remember(
                content=reply_text,
                metadata={"type": "brief_analysis", "date": date.today().isoformat()},
            )

            await self.reply(msg, result={"text": reply_text})
        except Exception as e:
            await self.reply(msg, error=str(e))


if __name__ == "__main__":
    run_agent(AnalysisAgent())
