"""
StrategistAgent — 策略分析师
职责: 每日收盘复盘、板块研判、风险监测、策略问答、状态维护
数据源: 综合 MarketAgent + NewsAgent + AnalysisAgent 记忆
记忆: 三层拓扑记忆 + 跨 Agent recall
"""

import asyncio
import logging
import os
from datetime import date, datetime
from pathlib import Path

import yaml

from agents.base import BaseAgent, AgentMessage, run_agent
from agents.memory.memory_mixin import MemoryMixin

logger = logging.getLogger("agent.strategist")

STATE_PATH = Path(__file__).parent / "memory" / "strategy_state.yaml"


def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return yaml.safe_load(STATE_PATH.read_text()) or {}
    except Exception as e:
        logger.warning(f"Failed to load strategy state: {e}")
    return {}


def _save_state(state: dict):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(yaml.dump(state, allow_unicode=True, default_flow_style=False))
    except Exception as e:
        logger.error(f"Failed to save strategy state: {e}")


class StrategistAgent(BaseAgent, MemoryMixin):
    name = "strategist"

    def __init__(self):
        super().__init__()
        self._init_memory()

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "daily_review":
            await self._handle_daily_review(msg)
        elif action == "sector_thesis":
            await self._handle_sector_thesis(msg, params)
        elif action == "risk_alert":
            await self._handle_risk_alert(msg)
        elif action == "ask_strategy":
            await self._handle_ask_strategy(msg, params)
        elif action == "update_state":
            await self._handle_update_state(msg)
        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    async def _get_market_snapshot(self) -> dict:
        """并行采集行情 + 新闻数据"""
        market_task = self.send("market", "get_all_raw")
        news_task = self.send("news", "get_news", {"keyword": ""})
        market_resp, news_resp = await asyncio.gather(market_task, news_task)

        snapshot = {}
        if not market_resp.error:
            snapshot["market"] = market_resp.result
        if not news_resp.error:
            snapshot["news"] = news_resp.result
        return snapshot

    def _format_market_text(self, raw: dict) -> str:
        """将原始市场数据格式化为文本"""
        parts = []

        zt = raw.get("limitup")
        if zt and zt.get("results"):
            lines = [f"涨停板（{zt.get('count', 0)}只）:"]
            for r in zt["results"][:15]:
                t = f" {r.get('limit_times', 1)}连板" if (r.get('limit_times') or 1) > 1 else ""
                lines.append(
                    f"  {r.get('name', '')} {r.get('pct_chg', '')}% "
                    f"行业:{r.get('industry', '')}{t}"
                )
            parts.append("\n".join(lines))

        lb = raw.get("limitstep")
        if lb and lb.get("results"):
            lines = ["连板股:"]
            for r in lb["results"][:10]:
                lines.append(
                    f"  {r.get('name', '')} {r.get('up_stat', '')} "
                    f"行业:{r.get('industry', '')} 涨幅:{r.get('pct_chg', '')}%"
                )
            parts.append("\n".join(lines))

        bk = raw.get("concepts")
        if bk and bk.get("results"):
            lines = ["概念板块涨幅榜:"]
            for r in bk["results"][:10]:
                lines.append(
                    f"  {r.get('board_name', '')} {r.get('pct_chg', '')}% "
                    f"涨:{r.get('up_count', 0)} 跌:{r.get('down_count', 0)}"
                )
            parts.append("\n".join(lines))

        return "\n\n".join(parts) if parts else "暂无行情数据"

    async def _handle_daily_review(self, msg: AgentMessage):
        """收盘复盘: 并行采集数据 → 拉取历史记忆 → LLM 综合分析 → 更新状态"""
        snapshot = await self._get_market_snapshot()

        market_data = snapshot.get("market", {})
        market_text = self._format_market_text(market_data) if isinstance(market_data, dict) else str(market_data)
        news_text = ""
        news = snapshot.get("news")
        if isinstance(news, dict):
            news_text = news.get("text", str(news))[:500]
        elif news:
            news_text = str(news)[:500]

        history_context = ""
        memories = await self.recall("策略复盘 板块轮动 市场情绪", top_k=5, cross_agent=True, time_range_days=7)
        reminds = await self.get_reminds(max_items=3)
        if memories or reminds:
            history_context = self.format_recall_context(memories, max_items=5, reminds=reminds) + "\n\n"

        state = _load_state()
        state_text = yaml.dump(state, allow_unicode=True, default_flow_style=False) if state else "暂无历史状态"

        from agents.market_time import get_analysis_context, format_time_context_block
        time_ctx = get_analysis_context()
        time_block = format_time_context_block(time_ctx)

        prompt = (
            f"{time_block}\n\n"
            f"{history_context}"
            f"## 当前策略状态\n{state_text}\n\n"
            f"## {'今日' if time_ctx['is_trading_day'] else '最近交易日'}行情数据（{time_ctx['freshness_label']}）\n{market_text}\n\n"
            f"## 新闻摘要\n{news_text}\n\n"
            f"请以策略分析师角色进行{'收盘复盘' if time_ctx['phase'] == 'post_close' else '策略研判'}:\n"
            f"1. 判定当前市场阶段 (震荡筑底/修复上行/主升浪/高位震荡/退潮调整)\n"
            f"2. 识别今日主线板块及持续性评估\n"
            f"3. 评估情绪信号和赚钱效应\n"
            f"4. 更新板块多空观点\n"
            f"5. 提出风险提示和明日关注方向\n\n"
            f"同时，输出一段 JSON 用于更新策略状态（用 ```json ... ``` 包裹）:\n"
            f'{{"market_phase":"...","main_themes":["..."],"risk_level":"low/medium/high",'
            f'"watchlist":[{{"sector":"...","stance":"偏多/偏空/中性","note":"..."}}]}}\n\n'
            f"⚠️ 以上为策略分析，不构成投资建议"
        )

        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            messages = [
                {"role": "system", "content": self.soul or "你是一位专业的A股策略分析师。"},
                {"role": "user", "content": prompt},
            ]
            reply_text = await router.chat(messages, task_type="analysis")
            if not reply_text:
                await self.reply(msg, error="所有 LLM 提供商均不可用")
                return

            self._try_update_state(reply_text, state)

            await self.remember(
                content=reply_text,
                metadata={
                    "type": "daily_review",
                    "date": date.today().isoformat(),
                    "market_phase": state.get("market_phase", ""),
                },
            )

            await self.reply(msg, result={
                "text": f"📈 策略复盘:\n\n{reply_text}",
                "state_updated": True,
            })
        except Exception as e:
            logger.error(f"Daily review failed: {e}", exc_info=True)
            await self.reply(msg, error=f"复盘失败: {e}")

    def _try_update_state(self, llm_reply: str, current_state: dict):
        """尝试从 LLM 回复中提取 JSON 并更新策略状态"""
        import json
        import re

        json_match = re.search(r"```json\s*(.*?)\s*```", llm_reply, re.DOTALL)
        if not json_match:
            json_match = re.search(r"\{[^{}]*\"market_phase\"[^{}]*\}", llm_reply, re.DOTALL)
        if not json_match:
            return

        try:
            raw = json_match.group(1) if json_match.lastindex else json_match.group(0)
            new_state = json.loads(raw)
            current_state.update({
                "date": date.today().isoformat(),
                "market_phase": new_state.get("market_phase", current_state.get("market_phase", "")),
                "main_themes": new_state.get("main_themes", current_state.get("main_themes", [])),
                "risk_level": new_state.get("risk_level", current_state.get("risk_level", "medium")),
                "watchlist": new_state.get("watchlist", current_state.get("watchlist", [])),
            })

            recent_calls = current_state.get("recent_calls", [])
            recent_calls.insert(0, {
                "date": date.today().isoformat(),
                "call": new_state.get("market_phase", ""),
                "result": "pending",
            })
            current_state["recent_calls"] = recent_calls[:10]

            _save_state(current_state)
            logger.info("Strategy state updated")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse state from LLM reply: {e}")

    async def _handle_sector_thesis(self, msg: AgentMessage, params: dict):
        """板块研判"""
        sector = params.get("sector", "")
        snapshot = await self._get_market_snapshot()
        market_data = snapshot.get("market", {})
        market_text = self._format_market_text(market_data) if isinstance(market_data, dict) else "暂无数据"

        query = f"板块研判 {sector}" if sector else "板块轮动 主线研判"
        memories = await self.recall(query, top_k=5, cross_agent=True, time_range_days=14)
        history = self.format_recall_context(memories, max_items=5) if memories else ""

        state = _load_state()
        watchlist_text = yaml.dump(state.get("watchlist", []), allow_unicode=True) if state.get("watchlist") else "暂无"

        from agents.market_time import get_analysis_context, format_time_context_block
        time_ctx = get_analysis_context()
        time_block = format_time_context_block(time_ctx)

        focus = f"重点分析 [{sector}] 板块" if sector else "全盘板块轮动分析"
        prompt = (
            f"{time_block}\n\n"
            f"{history}\n\n"
            f"## 当前板块观点\n{watchlist_text}\n\n"
            f"## 行情数据（{time_ctx['freshness_label']}）\n{market_text}\n\n"
            f"请以策略分析师角色进行{focus}:\n"
            f"1. 板块资金流向和强弱排序\n"
            f"2. 主线持续性评估\n"
            f"3. 更新板块多空观点\n"
            f"4. 潜在轮动方向\n\n"
            f"⚠️ 以上为策略分析，不构成投资建议"
        )

        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            messages = [
                {"role": "system", "content": self.soul or "你是一位专业的A股策略分析师。"},
                {"role": "user", "content": prompt},
            ]
            reply_text = await router.chat(messages, task_type="analysis")
            if not reply_text:
                await self.reply(msg, error="LLM 不可用")
                return

            await self.remember(
                content=reply_text,
                metadata={"type": "sector_thesis", "sector": sector, "date": date.today().isoformat()},
            )
            await self.reply(msg, result={"text": f"📊 板块研判:\n\n{reply_text}"})
        except Exception as e:
            await self.reply(msg, error=str(e))

    async def _handle_risk_alert(self, msg: AgentMessage):
        """风险提示: 检测异常信号（盘中更敏感）"""
        from agents.market_time import is_trading_hours

        snapshot = await self._get_market_snapshot()
        market_data = snapshot.get("market", {})
        in_session = is_trading_hours()

        alerts = []
        alert_level = "none"

        if isinstance(market_data, dict):
            lb = market_data.get("limitstep", {})
            if lb and lb.get("results"):
                max_height = max(
                    (r.get("limit_times", 1) for r in lb["results"]),
                    default=0,
                )
                if max_height <= 2:
                    alerts.append(f"⚠️ 连板梯队断裂: 最高仅{max_height}板，赚钱效应衰减")
                    alert_level = "high"

            zt = market_data.get("limitup", {})
            zt_count = zt.get("count", 0) if zt else 0
            zt_threshold = 20 if in_session else 15
            if zt_count < zt_threshold:
                label = "盘中涨停偏少" if in_session else "涨停低迷"
                alerts.append(f"⚠️ {label}: 仅{zt_count}只涨停{'，注意盘中风控' if in_session else '，市场冰点'}")
                alert_level = max(alert_level, "medium", key=lambda x: {"none": 0, "low": 1, "medium": 2, "high": 3}[x])

        if not alerts:
            await self.reply(msg, result={"text": "", "alert_level": "none"})
            return

        text = "🚨 风险监测:\n" + "\n".join(alerts)
        await self.reply(msg, result={"text": text, "alert_level": alert_level})

    async def _handle_ask_strategy(self, msg: AgentMessage, params: dict):
        """策略问答: 综合历史记忆 + 当前数据回答"""
        question = params.get("question", "")
        if not question:
            await self.reply(msg, error="请提供策略问题")
            return

        snapshot = await self._get_market_snapshot()
        market_data = snapshot.get("market", {})
        market_text = self._format_market_text(market_data) if isinstance(market_data, dict) else "暂无数据"

        memories = await self.recall(question, top_k=5, cross_agent=True, time_range_days=30)
        reminds = await self.get_reminds(max_items=3)
        history = ""
        if memories or reminds:
            history = self.format_recall_context(memories, max_items=5, reminds=reminds) + "\n\n"

        state = _load_state()
        state_text = yaml.dump(state, allow_unicode=True, default_flow_style=False) if state else "暂无"

        from agents.market_time import get_analysis_context, format_time_context_block
        time_ctx = get_analysis_context()
        time_block = format_time_context_block(time_ctx)

        prompt = (
            f"{time_block}\n\n"
            f"{history}"
            f"## 当前策略状态\n{state_text}\n\n"
            f"## 行情摘要（{time_ctx['freshness_label']}）\n{market_text[:800]}\n\n"
            f"用户策略问题: {question}\n\n"
            f"请基于时间上下文、策略状态、历史记忆和当前数据回答。"
            f"{'盘中问题侧重实时信号和短线操作层面。' if time_ctx['is_realtime'] else '非盘中问题侧重趋势判断和中期策略。'}\n"
            f"⚠️ 以上为策略分析，不构成投资建议"
        )

        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            messages = [
                {"role": "system", "content": self.soul or "你是一位专业的A股策略分析师。"},
                {"role": "user", "content": prompt},
            ]
            reply_text = await router.chat(messages, task_type="analysis")
            if not reply_text:
                await self.reply(msg, error="LLM 不可用")
                return

            await self.remember(
                content=reply_text,
                metadata={"type": "ask_strategy", "question": question[:200], "date": date.today().isoformat()},
            )
            await self.reply(msg, result={"text": f"📈 策略分析:\n\n{reply_text}"})
        except Exception as e:
            await self.reply(msg, error=str(e))

    async def _handle_update_state(self, msg: AgentMessage):
        """手动触发状态更新"""
        state = _load_state()
        if not state or state.get("market_phase") == "待初始化":
            await self.reply(msg, result={"text": "策略状态尚未初始化，请先运行 /strategy 或等待收盘自动复盘"})
            return

        text = (
            f"📈 当前策略状态:\n"
            f"日期: {state.get('date', 'N/A')}\n"
            f"阶段: {state.get('market_phase', 'N/A')}\n"
            f"主题: {', '.join(state.get('main_themes', []))}\n"
            f"风险: {state.get('risk_level', 'N/A')}\n"
        )
        watchlist = state.get("watchlist", [])
        if watchlist:
            text += "板块观点:\n"
            for w in watchlist:
                text += f"  - {w.get('sector', '')}: {w.get('stance', '')} ({w.get('note', '')})\n"

        await self.reply(msg, result={"text": text})


if __name__ == "__main__":
    run_agent(StrategistAgent())
