"""
NewsAgent — 舆情监控 + 深度研究
职责: 新闻/公告/舆情抓取、AI摘要、网络搜索、深度专题研究
数据源:
  - 192.168.1.138 sentiment API (主) + AKShare (备用) + DeepSeek
  - DDG Lite 网搜 (零 API Key)
  - academic-deep-research skill (学术级深度研究, 无外部依赖)
  - agent-deep-research skill (Gemini 驱动, 需 GOOGLE_API_KEY)
记忆: 三层拓扑记忆（向量+图谱+时序），temporal_next 连相邻天，same_topic 连同事件
支持跨Agent记忆提醒 (remind) 注入
"""
from __future__ import annotations


import asyncio
import logging
import os
import re as _re
import subprocess
import urllib.parse
from datetime import date

import httpx

from agents.base import BaseAgent, AgentMessage, create_llm, build_soul_prompt, run_agent
from agents.memory.memory_mixin import MemoryMixin
from agents.data_sources import api_get_with_fallback


# ── DDG Lite 搜索 ──────────────────────────────────────

async def _ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo Lite 直连 — 零 API Key"""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={encoded}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    text = resp.text
    results = []
    links = _re.findall(r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, _re.DOTALL)
    snippets = _re.findall(r'<td\s+class="result-snippet">(.*?)</td>', text, _re.DOTALL)
    for i, (href, title) in enumerate(links[:max_results]):
        if "duckduckgo.com" in href:
            continue
        title_clean = _re.sub(r'<[^>]+>', '', title).strip()
        snippet = _re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
        results.append({"title": title_clean, "url": href, "snippet": snippet})
    return results


# ── OpenClaw skill 调用工具 ─────────────────────────────

async def _run_openclaw_skill(skill_name: str, prompt: str, timeout: int = 120) -> str:
    """调用已安装的 OpenClaw skill (通过 openclaw run)"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw", "run", skill_name, "--", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace")[:30000]
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"Skill {skill_name} timeout ({timeout}s)"
    except Exception as e:
        return f"Skill {skill_name} error: {e}"

logger = logging.getLogger("agent.news")

STOCK_API = os.getenv("STOCK_API_BASE", "http://192.168.1.138/api")


def api_get(endpoint: str, params: dict | None = None) -> dict | None:
    return api_get_with_fallback(endpoint, params)


def _extract_news_items(data: dict) -> list:
    """兼容 Bridge ({"news": [...]}) 和旧版 ({"results": [...]}) 两种格式"""
    return data.get("results") or data.get("news") or []


def _news_date(item: dict) -> str:
    """兼容 pub_date / publish_time / datetime 字段"""
    raw = item.get("pub_date") or item.get("publish_time") or item.get("datetime") or ""
    return str(raw)[:10]


class NewsAgent(BaseAgent, MemoryMixin):
    name = "news"

    def __init__(self):
        super().__init__()
        self._init_memory()

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "get_news":
            await self._handle_get_news(msg, params)
        elif action == "summarize_news":
            await self._handle_summarize(msg, params)
        elif action == "web_search":
            await self._handle_web_search(msg, params)
        elif action == "deep_research":
            await self._handle_deep_research(msg, params)
        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    async def _handle_get_news(self, msg: AgentMessage, params: dict):
        keyword = params.get("keyword", "")
        page_size = params.get("page_size", 10)
        q_params = {"page_size": page_size}
        if keyword:
            q_params["search"] = keyword

        data = api_get("news", q_params)
        if data is None:
            data = api_get("sentiment", q_params)
        if data is None:
            await self.reply(msg, error="新闻 API 无响应")
            return

        results = _extract_news_items(data)
        if not results:
            await self.reply(msg, result={"text": "暂无相关新闻"})
            return

        lines = [f"📰 新闻资讯 ({len(results)}条)\n"]
        for i, r in enumerate(results[:10], 1):
            title = r.get("title", "")
            src = r.get("source", "")
            dt = _news_date(r)
            lines.append(f"{i}. [{dt}] {title} ({src})")
        await self.reply(msg, result={"text": "\n".join(lines), "raw": data})

    async def _handle_summarize(self, msg: AgentMessage, params: dict):
        keyword = params.get("keyword", "")
        q_params = {"page_size": 20}
        if keyword:
            q_params["search"] = keyword

        data = api_get("news", q_params) or api_get("sentiment", q_params)
        results = _extract_news_items(data) if data else []
        if not results:
            await self.reply(msg, error="无新闻数据可摘要")
            return

        news_text = "\n".join(
            f"- [{_news_date(r)}] {r.get('title', '')}：{r.get('content', r.get('summary', ''))[:200]}"
            for r in results[:15]
        )

        history_context = ""
        memories = await self.recall(keyword or news_text[:200], top_k=2, time_range_days=7)
        reminds = await self.get_reminds(max_items=2)
        if memories or reminds:
            history_context = self.format_recall_context(memories, max_items=2, reminds=reminds) + "\n\n"

        raw_prompt = (
            f"{history_context}"
            f"以下是最新的相关新闻:\n\n"
            f"{news_text}\n\n"
            f"请用3-5个要点简要总结当前市场舆情方向和关键信息。如有历史记忆参考，指出舆情演变趋势。"
        )
        prompt = build_soul_prompt(self, raw_prompt)
        try:
            llm = create_llm()
            from langchain_core.messages import HumanMessage
            result = await llm.ainvoke([HumanMessage(content=prompt)])
            text = result.completion if hasattr(result, "completion") else str(result)

            relations = []
            if memories:
                for mem in memories:
                    if mem.source_agent == self.name:
                        relations.append((mem.id, "same_topic"))

            await self.remember(
                content=text,
                metadata={
                    "type": "summarize_news",
                    "keyword": keyword,
                    "date": date.today().isoformat(),
                    "news_count": len(results),
                },
                relations=relations,
            )

            await self.reply(msg, result={"text": f"📰 舆情摘要:\n\n{text}"})
        except Exception as e:
            logger.error(f"LLM error: {e}", exc_info=True)
            await self.reply(msg, error=str(e))


    # ── 新能力: 网络搜索 (DDG + API 结合) ──────────────

    async def _handle_web_search(self, msg: AgentMessage, params: dict):
        """网络搜索: DDG 搜索 + API 新闻 联合，LLM 融合摘要"""
        query = params.get("query", params.get("keyword", ""))
        if not query:
            await self.reply(msg, error="缺少搜索关键词")
            return

        # 并行: DDG 搜索 + 138 API 新闻
        ddg_task = asyncio.create_task(_ddg_search(query, max_results=8))
        api_data = api_get("news", {"search": query, "page_size": 5})

        ddg_results = []
        try:
            ddg_results = await ddg_task
        except Exception as e:
            logger.warning(f"DDG search failed: {e}")

        api_items = _extract_news_items(api_data) if api_data else []

        # 融合结果
        parts = []
        if ddg_results:
            parts.append("📡 网络搜索结果:")
            for i, r in enumerate(ddg_results, 1):
                parts.append(f"  {i}. {r['title']}\n     {r['url']}\n     {r['snippet']}")
        if api_items:
            parts.append("\n📰 API 新闻:")
            for i, r in enumerate(api_items[:5], 1):
                parts.append(f"  {i}. [{_news_date(r)}] {r.get('title', '')} ({r.get('source', '')})")

        combined = "\n".join(parts)
        if not combined:
            await self.reply(msg, error="搜索无结果")
            return

        # LLM 融合摘要
        history_context = ""
        memories = await self.recall(query, top_k=2, time_range_days=7)
        reminds = await self.get_reminds(max_items=2)
        if memories or reminds:
            history_context = self.format_recall_context(memories, max_items=2, reminds=reminds) + "\n\n"

        raw_prompt = (
            f"{history_context}"
            f"搜索关键词: {query}\n\n{combined}\n\n"
            f"请综合以上所有信息源，用中文给出简洁但全面的分析摘要。如有历史记忆参考，指出变化趋势。"
        )
        prompt = build_soul_prompt(self, raw_prompt)
        try:
            llm = create_llm()
            from langchain_core.messages import HumanMessage
            result = await llm.ainvoke([HumanMessage(content=prompt)])
            text = result.completion if hasattr(result, "completion") else str(result)

            await self.remember(
                content=text,
                metadata={"type": "web_search", "keyword": query, "date": date.today().isoformat(),
                          "ddg_count": len(ddg_results), "api_count": len(api_items)},
            )
            await self.reply(msg, result={"text": f"🔍 综合搜索结果:\n\n{text}", "raw_ddg": ddg_results})
        except Exception as e:
            # LLM 失败也返回原始结果
            await self.reply(msg, result={"text": combined})

    # ── 新能力: 深度研究 (academic-deep-research / agent-deep-research) ──

    async def _handle_deep_research(self, msg: AgentMessage, params: dict):
        """深度专题研究 — 调用社区 skill 进行学术级/AI级深度研究"""
        topic = params.get("topic", params.get("query", ""))
        if not topic:
            await self.reply(msg, error="缺少研究主题")
            return
        mode = params.get("mode", "academic")  # "academic" | "gemini"
        timeout = params.get("timeout", 120)

        await self.reply(msg, result={"text": f"🔬 正在启动 {mode} 深度研究: {topic}...\n请稍候，这可能需要数分钟。"})

        # 先尝试召回相关历史记忆作为研究上下文
        memories = await self.recall(topic, top_k=3, time_range_days=30)
        context = ""
        if memories:
            context = "\n历史研究参考:\n" + "\n".join(
                f"- [{m.metadata.get('date', '?')}] {m.content[:200]}" for m in memories
            )

        research_prompt = f"请对以下主题进行深度研究分析:\n\n主题: {topic}\n{context}"

        result_text = ""

        if mode == "gemini":
            # agent-deep-research (Gemini 驱动)
            result_text = await _run_openclaw_skill("agent-deep-research", research_prompt, timeout=timeout)
        if not result_text or mode == "academic" or "error" in result_text.lower()[:50]:
            # academic-deep-research (无外部依赖，可靠兜底)
            result_text = await _run_openclaw_skill("academic-deep-research", research_prompt, timeout=timeout)

        if not result_text or len(result_text.strip()) < 50:
            # 最终兜底: 用 LLM + DDG 搜索组合
            ddg_results = []
            try:
                ddg_results = await _ddg_search(topic, max_results=10)
            except Exception:
                pass
            ddg_text = "\n".join(f"- {r['title']}: {r['snippet']}" for r in ddg_results) if ddg_results else ""
            prompt = build_soul_prompt(self, (
                f"请作为深度研究分析师，对以下主题进行全面深入的分析。\n\n"
                f"主题: {topic}\n{context}\n\n"
                f"网络参考资料:\n{ddg_text}\n\n"
                f"要求: 1) 现状分析 2) 关键因素 3) 趋势判断 4) 风险提示 5) 建议"
            ))
            try:
                llm = create_llm()
                from langchain_core.messages import HumanMessage
                r = await llm.ainvoke([HumanMessage(content=prompt)])
                result_text = r.completion if hasattr(r, "completion") else str(r)
            except Exception as e:
                await self.reply(msg, error=f"深度研究全部失败: {e}")
                return

        # 存储研究结果到记忆
        relations = []
        if memories:
            for mem in memories:
                relations.append((mem.id, "same_topic"))
        await self.remember(
            content=result_text[:5000],
            metadata={"type": "deep_research", "topic": topic, "mode": mode,
                      "date": date.today().isoformat()},
            relations=relations,
        )
        await self.reply(msg, result={"text": f"🔬 深度研究报告 [{mode}]:\n\n{result_text}"})


if __name__ == "__main__":
    run_agent(NewsAgent())
