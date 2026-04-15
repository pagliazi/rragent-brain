"""
PyAgent Redis Bridge — native tool calls to 12 Python agents via Redis Pub/Sub.

Replaces the old bridge translation layer. RRAgent now directly controls
routing to PyAgent commands through Redis, with proper timeout handling
and error containment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

import redis.asyncio as aioredis

from rragent.tools.base import Tool, ToolSpec, ToolResult, ToolUse

logger = logging.getLogger("rragent.tools.pyagent")


class PyAgentBridge:
    """
    Manages Redis connections to the 12 Python agents.

    Message protocol matches AgentMessage from rragent-brain/agents/base.py:
    - Publish to `rragent:{agent}` channel
    - Wait for response on `rragent:{agent}` with matching reply_to
    """

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379/0"):
        self.redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._redis_raw: aioredis.Redis | None = None  # For Pub/Sub (binary)
        self._connected = False

    async def connect(self):
        self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        # Separate connection for Pub/Sub (binary mode, needed for raw message parsing)
        self._redis_raw = aioredis.from_url(self.redis_url, decode_responses=False)
        await self._redis.ping()
        self._connected = True
        logger.info(f"PyAgent bridge connected: {self.redis_url}")

    @property
    def redis(self) -> aioredis.Redis:
        """Text-mode Redis client (for agent commands)."""
        return self._redis

    @property
    def redis_raw(self) -> aioredis.Redis:
        """Binary-mode Redis client (for Pub/Sub stream consumption)."""
        return self._redis_raw

    async def close(self):
        if self._redis:
            await self._redis.aclose()
        if self._redis_raw:
            await self._redis_raw.aclose()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _try_reconnect(self) -> bool:
        """Attempt to reconnect to Redis. Returns True on success."""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"Redis reconnect attempt {attempt}/{max_attempts}...")
                # Close stale connections
                if self._redis:
                    try:
                        await self._redis.aclose()
                    except Exception:
                        pass
                if self._redis_raw:
                    try:
                        await self._redis_raw.aclose()
                    except Exception:
                        pass

                self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
                self._redis_raw = aioredis.from_url(self.redis_url, decode_responses=False)
                await self._redis.ping()
                self._connected = True
                logger.info("Redis reconnected successfully")
                return True
            except Exception as e:
                logger.warning(f"Redis reconnect attempt {attempt} failed: {e}")
                await asyncio.sleep(min(2 ** attempt, 10))

        self._connected = False
        return False

    async def call_agent(
        self,
        agent: str,
        action: str,
        params: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """
        Send a command to a PyAgent and wait for response.

        Uses the AgentMessage format:
        {id, sender, target, action, params, reply_to, timestamp}

        Auto-reconnects if Redis is unavailable. Returns a tool error
        (not crash) if reconnection fails.
        """
        if not self._redis or not self._connected:
            if not await self._try_reconnect():
                return {"error": "Redis unavailable — cannot reach PyAgent. Will retry on next call."}

        msg_id = uuid.uuid4().hex[:12]
        message = {
            "id": msg_id,
            "sender": "rragent",
            "target": agent,
            "action": action,
            "params": params,
            "reply_to": "",
            "timestamp": time.time(),
        }

        channel = f"rragent:{agent}"
        reply_channel = f"rragent:rragent"

        # Subscribe to reply channel before publishing
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(reply_channel)

        try:
            # Publish command
            await self._redis.publish(channel, json.dumps(message, ensure_ascii=False))

            # Wait for reply
            deadline = time.time() + timeout
            async for raw in pubsub.listen():
                if time.time() > deadline:
                    return {"error": f"Timeout ({timeout}s) waiting for {agent}.{action}"}

                if raw["type"] != "message":
                    continue

                try:
                    data = json.loads(raw["data"])
                    # Match by reply_to or action response pattern
                    if (
                        data.get("reply_to") == msg_id
                        or data.get("action") == f"{action}:response"
                    ):
                        if data.get("error"):
                            return {"error": data["error"]}
                        return data.get("result", data)
                except (json.JSONDecodeError, Exception):
                    continue

            return {"error": "Reply channel closed unexpectedly"}

        except (ConnectionError, OSError, aioredis.ConnectionError) as e:
            logger.warning(f"Redis connection lost during call_agent: {e}")
            self._connected = False
            # Attempt reconnect for next call
            asyncio.create_task(self._try_reconnect())
            return {"error": f"Redis connection lost: {e}. Reconnecting..."}

        finally:
            try:
                await pubsub.unsubscribe(reply_channel)
                await pubsub.aclose()
            except Exception:
                pass  # Connection already lost


class PyAgentTool(Tool):
    """
    A tool that routes to a specific PyAgent command.

    Created dynamically from command_registry entries.
    """

    def __init__(
        self,
        command: str,
        agent: str,
        action: str,
        description: str,
        timeout: float,
        bridge: PyAgentBridge,
        input_schema: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
        keywords: list[str] | None = None,
        is_concurrent_safe: bool = True,
    ):
        self.bridge = bridge
        self.agent_name = agent
        self.action_name = action

        self.spec = ToolSpec(
            name=f"pyagent_{command}",
            description=description,
            input_schema=input_schema or {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": f"Arguments for {command} command",
                    }
                },
            },
            is_tier0=False,
            should_defer=True,
            is_concurrent_safe=is_concurrent_safe,
            timeout=timeout if timeout > 0 else 30.0,
            category=self._infer_category(agent),
            agent=agent,
            keywords=keywords or [command] + (aliases or []),
            aliases=aliases or [],
        )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        try:
            result = await self.bridge.call_agent(
                agent=self.agent_name,
                action=self.action_name,
                params=input,
                timeout=self.spec.timeout,
            )

            if isinstance(result, dict) and result.get("error"):
                return ToolResult.error(str(result["error"]))

            # Format result
            if isinstance(result, dict):
                content = json.dumps(result, ensure_ascii=False, indent=2)
            else:
                content = str(result)

            return ToolResult.success(content)

        except ConnectionError:
            return ToolResult.error(
                "PyAgent Redis connection lost. "
                "The system will attempt to reconnect."
            )
        except Exception as e:
            return ToolResult.error(f"PyAgent error ({self.agent_name}.{self.action_name}): {e}")

    @staticmethod
    def _infer_category(agent: str) -> str:
        categories = {
            "market": "market",
            "analysis": "market",
            "backtest": "quant",
            "strategist": "quant",
            "dev": "development",
            "browser": "automation",
            "desktop": "automation",
            "news": "information",
            "general": "general",
            "apple": "system",
            "monitor": "ops",
            "orchestrator": "meta",
        }
        return categories.get(agent, "general")


# ── Command registry for auto-generating PyAgent tools ──

PYAGENT_COMMANDS = [
    # Market
    {"command": "zt", "agent": "market", "action": "get_limitup", "timeout": 30, "description": "查看涨停板", "keywords": ["涨停", "涨停板", "limitup", "zt"]},
    {"command": "lb", "agent": "market", "action": "get_limitstep", "timeout": 30, "description": "查看连板股", "keywords": ["连板", "连续涨停", "lb"]},
    {"command": "bk", "agent": "market", "action": "get_concepts", "timeout": 30, "description": "查看板块概念", "keywords": ["板块", "概念", "concepts", "bk"]},
    {"command": "hot", "agent": "market", "action": "get_hot", "timeout": 30, "description": "查看热门股", "keywords": ["热门", "热股", "hot"]},
    {"command": "summary", "agent": "market", "action": "get_summary", "timeout": 120, "description": "市场总结", "keywords": ["总结", "大盘", "summary", "市场"]},
    # Analysis
    {"command": "ask", "agent": "analysis", "action": "ask", "timeout": 180, "description": "提问分析", "keywords": ["分析", "问", "ask"], "concurrent": False},
    # Dev
    {"command": "dev", "agent": "dev", "action": "ai_dev", "timeout": 180, "description": "AI 开发指令", "keywords": ["开发", "dev"], "concurrent": False},
    {"command": "claude", "agent": "dev", "action": "claude_code", "timeout": 360, "description": "Claude Code 编程", "aliases": ["cc"], "keywords": ["claude", "编程", "code"], "concurrent": False},
    {"command": "claude_continue", "agent": "dev", "action": "claude_continue", "timeout": 360, "description": "继续 Claude 对话", "aliases": ["ccr"], "keywords": ["继续", "continue"], "concurrent": False},
    {"command": "deploy", "agent": "dev", "action": "deploy_frontend", "timeout": 300, "description": "部署前端", "keywords": ["部署", "deploy"], "concurrent": False},
    {"command": "ssh", "agent": "dev", "action": "ssh_exec", "timeout": 120, "description": "SSH 远程执行", "keywords": ["ssh", "远程"], "concurrent": False},
    {"command": "local", "agent": "dev", "action": "local_exec", "timeout": 120, "description": "本地命令执行", "keywords": ["local", "本地", "命令"], "concurrent": False},
    {"command": "code_review", "agent": "dev", "action": "code_review", "timeout": 300, "description": "代码审查", "aliases": ["cr", "review"], "keywords": ["审查", "review", "cr"], "concurrent": False},
    {"command": "refactor", "agent": "dev", "action": "refactor", "timeout": 300, "description": "代码重构", "keywords": ["重构", "refactor"], "concurrent": False},
    {"command": "fix", "agent": "dev", "action": "fix_bug", "timeout": 300, "description": "修复 Bug", "keywords": ["修复", "fix", "bug"], "concurrent": False},
    {"command": "test", "agent": "dev", "action": "gen_test", "timeout": 300, "description": "生成测试", "keywords": ["测试", "test"], "concurrent": False},
    {"command": "explain", "agent": "dev", "action": "explain", "timeout": 180, "description": "解释代码", "keywords": ["解释", "explain"], "concurrent": False},
    {"command": "git_status", "agent": "dev", "action": "git_status", "timeout": 30, "description": "Git 状态", "keywords": ["git", "status"]},
    {"command": "git_pull", "agent": "dev", "action": "git_pull", "timeout": 30, "description": "Git 拉取", "keywords": ["git", "pull"]},
    {"command": "git_log", "agent": "dev", "action": "git_log", "timeout": 30, "description": "Git 日志", "keywords": ["git", "log"]},
    {"command": "git_diff", "agent": "dev", "action": "git_diff", "timeout": 30, "description": "Git 差异", "keywords": ["git", "diff"]},
    {"command": "git_sync", "agent": "dev", "action": "git_sync", "timeout": 120, "description": "Git 同步", "keywords": ["git", "sync"], "concurrent": False},
    {"command": "host_list", "agent": "dev", "action": "host_list", "timeout": 30, "description": "主机列表", "keywords": ["主机", "host"]},
    {"command": "host_test", "agent": "dev", "action": "host_test", "timeout": 60, "description": "主机测试", "keywords": ["主机", "测试"]},
    # Browser
    {"command": "browse", "agent": "browser", "action": "smart_task", "timeout": 180, "description": "浏览器智能任务", "aliases": ["task"], "keywords": ["浏览器", "browse", "网页"], "concurrent": False},
    {"command": "url", "agent": "browser", "action": "open_url", "timeout": 30, "description": "打开 URL", "keywords": ["url", "链接"]},
    {"command": "snapshot", "agent": "browser", "action": "snapshot", "timeout": 30, "description": "网页快照", "keywords": ["快照", "snapshot"]},
    # Desktop
    {"command": "screen", "agent": "desktop", "action": "screenshot", "timeout": 30, "description": "截屏", "keywords": ["截屏", "screen"]},
    {"command": "do", "agent": "desktop", "action": "smart_exec", "timeout": 120, "description": "桌面智能操作", "keywords": ["桌面", "操作"], "concurrent": False},
    {"command": "shell", "agent": "desktop", "action": "shell", "timeout": 30, "description": "桌面 Shell", "keywords": ["shell"]},
    {"command": "app", "agent": "desktop", "action": "open_app", "timeout": 30, "description": "打开应用", "keywords": ["应用", "app"]},
    {"command": "type", "agent": "desktop", "action": "type_text", "timeout": 30, "description": "输入文字", "keywords": ["输入", "type"]},
    {"command": "key", "agent": "desktop", "action": "key_press", "timeout": 30, "description": "按键", "keywords": ["按键", "key"]},
    {"command": "click", "agent": "desktop", "action": "click", "timeout": 30, "description": "点击", "keywords": ["点击", "click"]},
    {"command": "windows", "agent": "desktop", "action": "list_windows", "timeout": 30, "description": "列出窗口", "keywords": ["窗口", "windows"]},
    # News
    {"command": "news", "agent": "news", "action": "get_news", "timeout": 120, "description": "查看新闻", "keywords": ["新闻", "news", "资讯"]},
    {"command": "web_search", "agent": "news", "action": "web_search", "timeout": 60, "description": "网页搜索", "aliases": ["ws"], "keywords": ["搜索", "search", "google"]},
    {"command": "research", "agent": "news", "action": "deep_research", "timeout": 300, "description": "深度研究", "aliases": ["deep"], "keywords": ["研究", "research", "深度"], "concurrent": False},
    # Strategist
    {"command": "strategy", "agent": "strategist", "action": "ask_strategy", "timeout": 180, "description": "策略咨询", "keywords": ["策略", "strategy"], "concurrent": False},
    # Backtest
    {"command": "backtest", "agent": "backtest", "action": "run_backtest", "timeout": 300, "description": "运行回测", "keywords": ["回测", "backtest", "夏普", "收益"], "concurrent": False},
    {"command": "bt_cache", "agent": "backtest", "action": "list_cache", "timeout": 30, "description": "回测缓存列表", "keywords": ["缓存", "cache"]},
    {"command": "ledger", "agent": "backtest", "action": "list_ledger", "timeout": 30, "description": "回测账本", "keywords": ["账本", "ledger"]},
    {"command": "strategy_list", "agent": "backtest", "action": "list_strategies", "timeout": 30, "description": "策略列表", "keywords": ["策略列表", "strategy_list"]},
    {"command": "strategy_detail", "agent": "backtest", "action": "get_strategy", "timeout": 30, "description": "策略详情", "keywords": ["策略详情", "strategy_detail"]},
    {"command": "qv", "agent": "backtest", "action": "quant_validate", "timeout": 120, "description": "量化验证", "aliases": ["quant_validate"], "keywords": ["量化", "验证", "qv"], "concurrent": False},
    # General
    {"command": "q", "agent": "general", "action": "ask", "timeout": 30, "description": "通用提问", "keywords": ["问", "q"]},
    {"command": "translate", "agent": "general", "action": "translate", "timeout": 30, "description": "翻译", "keywords": ["翻译", "translate"]},
    {"command": "summarize", "agent": "general", "action": "summarize", "timeout": 180, "description": "总结", "keywords": ["总结", "summarize"], "concurrent": False},
    {"command": "write", "agent": "general", "action": "write", "timeout": 180, "description": "写作", "keywords": ["写作", "write"], "concurrent": False},
    {"command": "code", "agent": "general", "action": "explain_code", "timeout": 180, "description": "解释代码(通用)", "keywords": ["代码", "code"], "concurrent": False},
    {"command": "calc", "agent": "general", "action": "calculate", "timeout": 30, "description": "计算", "keywords": ["计算", "calc"]},
    {"command": "websearch", "agent": "general", "action": "search", "timeout": 120, "description": "网页搜索(通用)", "keywords": ["搜索", "websearch"]},
    # Apple
    {"command": "calendar", "agent": "apple", "action": "calendar_today", "timeout": 30, "description": "查看日历", "keywords": ["日历", "calendar"]},
    {"command": "cal_add", "agent": "apple", "action": "calendar_create", "timeout": 30, "description": "创建日历事件", "keywords": ["日历", "创建", "事件"]},
    {"command": "remind", "agent": "apple", "action": "remind_create", "timeout": 30, "description": "创建提醒", "keywords": ["提醒", "remind"]},
    {"command": "remind_list", "agent": "apple", "action": "remind_list", "timeout": 30, "description": "查看提醒列表", "keywords": ["提醒", "列表"]},
    {"command": "note", "agent": "apple", "action": "note_create", "timeout": 30, "description": "创建备忘录", "keywords": ["备忘录", "note"]},
    {"command": "note_search", "agent": "apple", "action": "note_search", "timeout": 30, "description": "搜索备忘录", "keywords": ["备忘录", "搜索"]},
    {"command": "contact", "agent": "apple", "action": "contact_search", "timeout": 30, "description": "搜索联系人", "keywords": ["联系人", "contact"]},
    {"command": "mail", "agent": "apple", "action": "mail_send", "timeout": 30, "description": "发送邮件", "keywords": ["邮件", "mail"]},
    {"command": "notify", "agent": "apple", "action": "notify", "timeout": 30, "description": "发送通知", "keywords": ["通知", "notify"]},
    {"command": "search", "agent": "apple", "action": "spotlight", "timeout": 120, "description": "Spotlight 搜索", "keywords": ["spotlight", "搜索"]},
    {"command": "music", "agent": "apple", "action": "music_control", "timeout": 30, "description": "音乐控制", "keywords": ["音乐", "music"]},
    {"command": "shortcut", "agent": "apple", "action": "shortcut_run", "timeout": 30, "description": "运行快捷指令", "keywords": ["快捷指令", "shortcut"]},
    {"command": "sysinfo", "agent": "apple", "action": "system_info", "timeout": 30, "description": "系统信息", "keywords": ["系统", "sysinfo"]},
    {"command": "clip", "agent": "apple", "action": "clipboard_read", "timeout": 30, "description": "读取剪贴板", "keywords": ["剪贴板", "clipboard"]},
    {"command": "finder", "agent": "apple", "action": "finder_open", "timeout": 30, "description": "打开 Finder", "keywords": ["finder"]},
    {"command": "volume", "agent": "apple", "action": "volume_control", "timeout": 30, "description": "音量控制", "keywords": ["音量", "volume"]},
    {"command": "dnd", "agent": "apple", "action": "do_not_disturb", "timeout": 30, "description": "勿扰模式", "keywords": ["勿扰", "dnd"]},
    {"command": "alarm", "agent": "apple", "action": "alarm_set", "timeout": 30, "description": "设置闹钟", "keywords": ["闹钟", "alarm"]},
    {"command": "timer", "agent": "apple", "action": "timer_set", "timeout": 30, "description": "设置定时器", "keywords": ["定时器", "timer"]},
    # Monitor
    {"command": "alerts", "agent": "monitor", "action": "check_alerts", "timeout": 120, "description": "查看告警", "keywords": ["告警", "alert", "监控"]},
    {"command": "targets", "agent": "monitor", "action": "check_targets", "timeout": 120, "description": "查看监控目标", "keywords": ["目标", "target", "监控"]},
    {"command": "alert_history", "agent": "monitor", "action": "alert_history", "timeout": 30, "description": "告警历史", "keywords": ["告警", "历史"]},
    {"command": "patrol", "agent": "monitor", "action": "summary", "timeout": 120, "description": "巡检汇总", "keywords": ["巡检", "patrol"]},
    {"command": "cert", "agent": "monitor", "action": "check_cert", "timeout": 30, "description": "检查证书", "aliases": ["ssl"], "keywords": ["证书", "ssl", "cert"]},
    {"command": "query", "agent": "monitor", "action": "query", "timeout": 30, "description": "PromQL 查询", "aliases": ["promql"], "keywords": ["promql", "query", "指标"]},
    {"command": "metrics", "agent": "monitor", "action": "metrics", "timeout": 30, "description": "指标搜索", "keywords": ["指标", "metrics"]},
    {"command": "host", "agent": "monitor", "action": "host_health", "timeout": 120, "description": "主机健康检查", "keywords": ["主机", "健康"]},
    {"command": "grafana_dash", "agent": "monitor", "action": "grafana_dash", "timeout": 30, "description": "Grafana 面板", "keywords": ["grafana", "面板"]},
    # Orchestrator
    {"command": "reflect", "agent": "orchestrator", "action": "reflect_insight", "timeout": 30, "description": "反思洞察", "keywords": ["反思", "reflect"]},
    {"command": "reflect_weekly", "agent": "orchestrator", "action": "reflect_weekly", "timeout": 60, "description": "每周反思", "keywords": ["周报", "weekly"]},
    {"command": "audit", "agent": "orchestrator", "action": "security_audit", "timeout": 60, "description": "安全审计", "aliases": ["security"], "keywords": ["审计", "安全"]},
    {"command": "multi_research", "agent": "orchestrator", "action": "multi_research", "timeout": 300, "description": "多 Agent 协作研究", "aliases": ["mr"], "keywords": ["多agent", "协作", "research"], "concurrent": False},
    {"command": "skills", "agent": "orchestrator", "action": "list_skills", "timeout": 30, "description": "列出技能", "keywords": ["技能", "skills"]},
    {"command": "agents", "agent": "orchestrator", "action": "list_agents", "timeout": 30, "description": "列出 Agent", "keywords": ["agent", "列出"]},
    {"command": "factor_list", "agent": "orchestrator", "action": "factor_list", "timeout": 30, "description": "因子列表", "keywords": ["因子", "factor"]},
    {"command": "factor_detail", "agent": "orchestrator", "action": "factor_detail", "timeout": 30, "description": "因子详情", "keywords": ["因子", "详情"]},
    {"command": "tier", "agent": "orchestrator", "action": "memory_tier", "timeout": 30, "description": "记忆分层", "aliases": ["memory_tier"], "keywords": ["记忆", "分层", "tier"]},
    {"command": "tier_status", "agent": "orchestrator", "action": "memory_tier_status", "timeout": 30, "description": "记忆分层状态", "keywords": ["记忆", "状态"]},
    {"command": "learnings", "agent": "orchestrator", "action": "learnings", "timeout": 30, "description": "查看教训", "keywords": ["教训", "learnings"]},
    {"command": "learn", "agent": "orchestrator", "action": "log_learning", "timeout": 30, "description": "记录教训", "keywords": ["记录", "教训"]},
]


def register_pyagent_tools(
    registry: "GlobalToolRegistry",
    bridge: PyAgentBridge,
) -> int:
    """Register all PyAgent commands as deferred tools in the registry."""
    from rragent.tools.registry import ToolIndex

    count = 0
    for cmd in PYAGENT_COMMANDS:
        tool = PyAgentTool(
            command=cmd["command"],
            agent=cmd["agent"],
            action=cmd["action"],
            description=cmd["description"],
            timeout=cmd.get("timeout", 30),
            bridge=bridge,
            aliases=cmd.get("aliases", []),
            keywords=cmd.get("keywords", []),
            is_concurrent_safe=cmd.get("concurrent", True),
        )
        index = ToolIndex(
            name=tool.spec.name,
            description=cmd["description"],
            keywords=cmd.get("keywords", [cmd["command"]]),
            agent=cmd["agent"],
            category=tool.spec.category,
            timeout=cmd.get("timeout", 30),
            is_concurrent_safe=cmd.get("concurrent", True),
        )
        registry.register_tier1(tool, index)
        count += 1

    logger.info(f"Registered {count} PyAgent tools")
    return count
