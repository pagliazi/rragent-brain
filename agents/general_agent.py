"""
GeneralAgent — 通用智能助手
职责: 知识问答、推理分析、翻译、摘要、写作辅助、代码解释、数学计算
数据源: LLM 云端推理 + 可选联网搜索(委托 BrowserAgent)
记忆: 三层拓扑记忆（向量+图谱+时序）
"""

import logging
import re as _re
import urllib.parse
from datetime import date

import httpx

from agents.base import BaseAgent, AgentMessage, run_agent
from agents.memory.memory_mixin import MemoryMixin

logger = logging.getLogger("agent.general")


async def _ddg_search(query: str, max_results: int = 8) -> str:
    """DuckDuckGo Lite 直连搜索 — 零 API Key, 灵感来自 ddg-web-search skill"""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={encoded}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    text = resp.text
    # 解析 DDG Lite HTML 结果
    results = []
    # 匹配每个结果块: 链接 + 摘要
    links = _re.findall(r'<a[^>]+href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>', text, _re.DOTALL)
    snippets = _re.findall(r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>', text, _re.DOTALL)
    if not links:
        # 备用解析: DDG Lite 有时结构不同
        links = _re.findall(r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, _re.DOTALL)
        snippets = _re.findall(r'<td\s+class="result-snippet">(.*?)</td>', text, _re.DOTALL)
    for i, (href, title) in enumerate(links[:max_results]):
        title_clean = _re.sub(r'<[^>]+>', '', title).strip()
        snippet = _re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
        if "duckduckgo.com" in href:
            continue
        results.append(f"{i+1}. {title_clean}\n   {href}\n   {snippet}")
    if not results:
        # 最简解析: 直接提取所有 http 链接
        all_links = _re.findall(r'https?://(?!duckduckgo)[^\s"<>]+', text)
        for i, link in enumerate(all_links[:max_results]):
            results.append(f"{i+1}. {link}")
    return "\n\n".join(results) if results else ""

TASK_TYPE_MAP = {
    "ask": "analysis",
    "translate": "default",
    "summarize": "brief",
    "write": "analysis",
    "explain_code": "code",
    "calculate": "analysis",
}

SYSTEM_PROMPTS = {
    "translate": (
        "你是一位专业的多语言翻译专家。\n"
        "翻译要求：忠实原文、表达自然、保持专业术语准确。\n"
        "如果目标语言是 auto，则根据原文语言自动选择：中文→英文，其他→中文。\n"
        "只输出翻译结果，不需要解释。"
    ),
    "summarize": (
        "你是一位专业的文本分析师。\n"
        "请提取核心要点，用简洁清晰的语言总结以下内容。\n"
        "使用列表或分层结构组织要点。"
    ),
    "write": (
        "你是一位优秀的写作助手。\n"
        "根据用户需求，产出高质量的文本内容。\n"
        "注意语气和风格要符合要求。"
    ),
    "explain_code": (
        "你是一位资深软件工程师。\n"
        "请解释以下代码的逻辑、核心思路，并指出潜在问题。\n"
        "用清晰的中文解释，对关键部分加注释说明。"
    ),
    "calculate": (
        "你是一位数学和统计学专家。\n"
        "请解答以下数学问题，展示推导过程。\n"
        "如果是数值计算，请给出精确结果。"
    ),
}


class GeneralAgent(BaseAgent, MemoryMixin):
    name = "general"

    def __init__(self):
        super().__init__()
        self._init_memory()

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        handler = {
            "ask": self._handle_ask,
            "translate": self._handle_translate,
            "summarize": self._handle_summarize,
            "write": self._handle_write,
            "explain_code": self._handle_explain_code,
            "calculate": self._handle_calculate,
            "search": self._handle_search,
        }.get(action)

        if handler:
            await handler(msg, params)
        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    async def _handle_ask(self, msg: AgentMessage, params: dict):
        question = params.get("question", "")
        context = params.get("context", "")
        depth = params.get("depth", "default")

        if not question:
            await self.reply(msg, error="缺少问题内容")
            return

        history_context = ""
        # 始终查记忆，不只是 analysis depth
        try:
            top_k = 5 if depth == "analysis" else 3
            cross = depth == "analysis"
            memories = await self.recall(question, top_k=top_k, cross_agent=cross, time_range_days=60)
            reminds = await self.get_reminds(max_items=2) if depth == "analysis" else []
            if memories or reminds:
                history_context = self.format_recall_context(memories, reminds=reminds) + "\n\n"
        except Exception:
            pass

        user_prompt = f"{history_context}"
        if context:
            user_prompt += f"补充上下文:\n{context}\n\n"
        user_prompt += f"问题: {question}\n\n请给出准确、结构化的回答。"

        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        now_bj = _dt.now(_tz(_td(hours=8))).strftime("%Y-%m-%d %H:%M (北京时间)")
        base_prompt = self.soul or "你是 RRClaw 系统的通用 AI 助手，知识渊博、逻辑清晰。用中文回答，先给结论再展开。"
        base_prompt += (
            f"\n当前时间: {now_bj}\n所有涉及时间的回复，必须使用北京时间 (UTC+8)。\n"
            "你具备持久化记忆能力（向量语义检索+知识图谱+时序），能记住历史对话。"
            "如果下方有'历史记忆参考'或'补充上下文'，请积极利用这些信息回答。\n"
        )

        task_type = "analysis" if depth == "analysis" else "default"
        reply_text = await self._llm_chat(
            system_prompt=base_prompt,
            user_prompt=user_prompt,
            task_type=task_type,
        )

        if reply_text is None:
            await self.reply(msg, error="所有 LLM 提供商均不可用")
            return

        await self.remember(
            content=reply_text,
            metadata={"type": "general_qa", "question": question[:200], "date": date.today().isoformat()},
        )
        await self.reply(msg, result={"text": reply_text})

    async def _handle_translate(self, msg: AgentMessage, params: dict):
        text = params.get("text", "")
        target_lang = params.get("target_lang", "auto")

        if not text:
            await self.reply(msg, error="缺少要翻译的文本")
            return

        user_prompt = f"目标语言: {target_lang}\n\n原文:\n{text}"
        reply_text = await self._llm_chat(
            system_prompt=SYSTEM_PROMPTS["translate"],
            user_prompt=user_prompt,
            task_type="default",
        )

        if reply_text is None:
            await self.reply(msg, error="所有 LLM 提供商均不可用")
            return
        await self.reply(msg, result={"text": reply_text})

    async def _handle_summarize(self, msg: AgentMessage, params: dict):
        text = params.get("text", "")
        max_length = params.get("max_length", 300)

        if not text:
            await self.reply(msg, error="缺少要总结的文本")
            return

        user_prompt = f"要求: 摘要不超过 {max_length} 字\n\n原文:\n{text}"
        reply_text = await self._llm_chat(
            system_prompt=SYSTEM_PROMPTS["summarize"],
            user_prompt=user_prompt,
            task_type="brief",
        )

        if reply_text is None:
            await self.reply(msg, error="所有 LLM 提供商均不可用")
            return
        await self.reply(msg, result={"text": reply_text})

    async def _handle_write(self, msg: AgentMessage, params: dict):
        task = params.get("task", "")
        style = params.get("style", "professional")

        if not task:
            await self.reply(msg, error="缺少写作任务描述")
            return

        user_prompt = f"风格: {style}\n\n任务: {task}"
        reply_text = await self._llm_chat(
            system_prompt=SYSTEM_PROMPTS["write"],
            user_prompt=user_prompt,
            task_type="analysis",
        )

        if reply_text is None:
            await self.reply(msg, error="所有 LLM 提供商均不可用")
            return
        await self.reply(msg, result={"text": reply_text})

    async def _handle_explain_code(self, msg: AgentMessage, params: dict):
        code = params.get("code", "")
        language = params.get("language", "auto")

        if not code:
            await self.reply(msg, error="缺少代码内容")
            return

        user_prompt = f"编程语言: {language}\n\n```\n{code}\n```"
        reply_text = await self._llm_chat(
            system_prompt=SYSTEM_PROMPTS["explain_code"],
            user_prompt=user_prompt,
            task_type="code",
        )

        if reply_text is None:
            await self.reply(msg, error="所有 LLM 提供商均不可用")
            return
        await self.reply(msg, result={"text": reply_text})

    async def _handle_calculate(self, msg: AgentMessage, params: dict):
        expression = params.get("expression", "")

        if not expression:
            await self.reply(msg, error="缺少数学表达式或问题")
            return

        user_prompt = f"问题:\n{expression}"
        reply_text = await self._llm_chat(
            system_prompt=SYSTEM_PROMPTS["calculate"],
            user_prompt=user_prompt,
            task_type="analysis",
        )

        if reply_text is None:
            await self.reply(msg, error="所有 LLM 提供商均不可用")
            return
        await self.reply(msg, result={"text": reply_text})

    async def _handle_search(self, msg: AgentMessage, params: dict):
        query = params.get("query", "")

        if not query:
            await self.reply(msg, error="缺少搜索关键词")
            return

        search_text = ""
        source = ""

        # ── Layer 1: DDG Lite 直连 (零依赖, 无 API Key) ──
        try:
            search_text = await _ddg_search(query)
            source = "ddg"
        except Exception as e:
            logger.warning(f"DDG search failed: {e}")

        # ── Layer 2: 委托 browser agent 兜底 ──
        if not search_text:
            try:
                resp = await self.send("browser", "task", {"task": f"搜索: {query}"})
                if not resp.error:
                    result = resp.result
                    search_text = result.get("text", str(result)) if isinstance(result, dict) else str(result)
                    source = "browser"
            except Exception as e:
                logger.warning(f"Browser search fallback failed: {e}")

        if not search_text:
            await self.reply(msg, error="所有搜索源均不可用")
            return

        summary = await self._llm_chat(
            system_prompt="你是一位信息整理专家。请根据搜索结果，用简洁的中文总结关键信息。",
            user_prompt=f"搜索关键词: {query}\n\n搜索结果:\n{search_text}",
            task_type="brief",
        )
        await self.reply(msg, result={"text": summary or search_text, "source": source})

    async def _llm_chat(self, system_prompt: str, user_prompt: str, task_type: str = "default") -> str | None:
        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            return await router.chat(messages, task_type=task_type)
        except Exception as e:
            logger.error(f"LLM chat error: {e}", exc_info=True)
            return None


if __name__ == "__main__":
    run_agent(GeneralAgent())
