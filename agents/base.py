"""
BaseAgent — OpenClaw Multi-Agent 基类
所有专项 Agent 继承此类，获得:
  - Redis 消息收发 (Pub/Sub + Stream)
  - 心跳上报
  - 统一日志
  - SOUL.md 身份加载
  - skills YAML 技能注册
  - LLM 工厂（带 SOUL 注入）
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import yaml

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

# Support both old and new prefix for backward compat
CHANNEL_PREFIX = os.getenv("AGENT_CHANNEL_PREFIX", "rragent:")
HEARTBEAT_KEY = "rragent:heartbeats"
HEARTBEAT_INTERVAL = 10
MSG_TIMEOUT = 180

SOULS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "souls")
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


@dataclass
class AgentMessage:
    id: str
    sender: str
    target: str
    action: str
    params: dict = field(default_factory=dict)
    reply_to: str = ""
    timestamp: float = 0.0
    result: Any = None
    error: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "AgentMessage":
        if isinstance(raw, bytes):
            raw = raw.decode()
        d = json.loads(raw)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def create(cls, sender: str, target: str, action: str, **kwargs) -> "AgentMessage":
        return cls(
            id=uuid.uuid4().hex[:12],
            sender=sender,
            target=target,
            action=action,
            timestamp=time.time(),
            **kwargs,
        )


def _load_soul(agent_name: str) -> str:
    path = os.path.join(SOULS_DIR, f"{agent_name}.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _load_skills(agent_name: str) -> dict:
    path = os.path.join(SKILLS_DIR, f"{agent_name}_skills.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


class BaseAgent:
    name: str = "base"

    def __init__(self):
        self.logger = logging.getLogger(f"agent.{self.name}")
        self._redis: aioredis.Redis | None = None
        self._running = False
        self._pending: dict[str, asyncio.Future] = {}
        self._progress_callbacks: dict[str, callable] = {}
        self.soul: str = _load_soul(self.name)
        self.skills: dict = _load_skills(self.name)
        if self.soul:
            self.logger.info(f"SOUL loaded ({len(self.soul)} chars)")
        if self.skills:
            skill_names = [s.get("name", "?") for s in self.skills.get("skills", [])]
            self.logger.info(f"Skills loaded: {skill_names}")

    async def get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    async def publish(self, msg: AgentMessage):
        r = await self.get_redis()
        channel = f"{CHANNEL_PREFIX}{msg.target}"
        await r.publish(channel, msg.to_json())

    async def send(self, target: str, action: str, params: dict | None = None, timeout: float = 0) -> AgentMessage:
        msg = AgentMessage.create(self.name, target, action, params=params or {})
        return await self.request(msg, timeout=timeout or MSG_TIMEOUT)

    async def request(self, msg: AgentMessage, timeout: float = MSG_TIMEOUT) -> AgentMessage:
        """发消息并等待回复"""
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg.id] = future
        await self.publish(msg)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg.id, None)
            return AgentMessage.create(
                self.name, msg.sender, "error",
                result=None, error=f"Timeout waiting for {msg.target}:{msg.action}",
            )

    async def reply(self, original: AgentMessage, result: Any = None, error: str = ""):
        resp = AgentMessage.create(self.name, original.sender, f"{original.action}:response")
        resp.id = original.id
        resp.result = result
        resp.error = error
        resp.reply_to = original.id
        r = await self.get_redis()
        channel = f"{CHANNEL_PREFIX}{original.sender}"
        await r.publish(channel, resp.to_json())

    async def handle(self, msg: AgentMessage):
        """子类实现：处理收到的消息"""
        self.logger.warning(f"Unhandled message: {msg.action}")

    async def _listen(self):
        r = await self.get_redis()
        pubsub = r.pubsub()
        channel = f"{CHANNEL_PREFIX}{self.name}"
        await pubsub.subscribe(channel)
        self.logger.info(f"Subscribed to {channel}")
        async for raw_msg in pubsub.listen():
            if raw_msg["type"] != "message":
                continue
            try:
                msg = AgentMessage.from_json(raw_msg["data"])
            except Exception as e:
                self.logger.error(f"Bad message: {e}")
                continue
            if msg.id in self._pending:
                # 跳过进度消息，保持 future 等待最终结果
                is_progress = isinstance(msg.result, dict) and msg.result.get("_progress")
                if is_progress:
                    # 转发进度到 _progress_callbacks (orchestrator 用)
                    cb = self._progress_callbacks.get(msg.id)
                    if cb:
                        try:
                            await cb(msg.result.get("text", ""))
                        except Exception:
                            pass
                    continue
                future = self._pending.pop(msg.id)
                if not future.done():
                    future.set_result(msg)
                continue
            asyncio.create_task(self._safe_handle(msg))

    async def _safe_handle(self, msg: AgentMessage):
        try:
            if msg.action == "webchat_ask":
                await self._handle_webchat_ask(msg)
            else:
                await self.handle(msg)
        except Exception as e:
            self.logger.error(f"Handle error [{msg.action}]: {e}", exc_info=True)
            await self.reply(msg, error=str(e))

    async def _handle_webchat_ask(self, msg: AgentMessage):
        """通用对话 action: 用户直接与 Agent 自然语言对话，使用 SOUL 身份回复"""
        user_input = msg.params.get("question", "") or msg.params.get("args", "")
        reply_channel = msg.params.get("reply_channel", "")

        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()

            if self.soul:
                system_prompt = self.soul.split("\n## 错误处理")[0]
            else:
                system_prompt = f"你是 RRAgent 系统的 {self.name} Agent。"
            system_prompt += "\n\n用户正在直接与你对话，请根据你的专业能力简洁回复。"

            reply_text = await router.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ], task_type="brief", temperature=0.5)

            text = reply_text or f"[{self.name}] 暂时无法回复"
        except Exception as e:
            self.logger.error(f"webchat_ask LLM error: {e}")
            text = f"[{self.name}] 回复失败: {e}"

        if reply_channel:
            r = await self.get_redis()
            payload = json.dumps({
                "text": text, "in_reply_to": msg.id,
                "timestamp": time.time(), "source": self.name,
            }, ensure_ascii=False, default=str)
            await r.publish(reply_channel, payload)

        await self.reply(msg, result={"text": text})

    async def _heartbeat(self):
        r = await self.get_redis()
        skill_names = [s.get("name", "") for s in self.skills.get("skills", [])]
        while self._running:
            await r.hset(HEARTBEAT_KEY, self.name, json.dumps({
                "ts": time.time(),
                "pid": os.getpid(),
                "skills": skill_names,
                "has_soul": bool(self.soul),
            }))
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def start(self):
        self._running = True
        self.logger.info(f"Agent [{self.name}] starting (pid={os.getpid()})")
        await asyncio.gather(
            self._listen(),
            self._heartbeat(),
        )

    async def stop(self):
        self._running = False
        if self._redis:
            await self._redis.aclose()
            self._redis = None
        self.logger.info(f"Agent [{self.name}] stopped")


def _log_create_llm_usage(provider: str, model: str, task_type: str,
                          latency_ms: float, success: bool,
                          prompt_tokens: int = 0, completion_tokens: int = 0,
                          caller: str = ""):
    """将 create_llm 路径的 LLM 调用记录到 Redis（与 llm_router 共享同一套 key）。"""
    try:
        from agents.llm_router import (LLM_USAGE_KEY, LLM_USAGE_DAILY_PREFIX,
                                       LLM_USAGE_MAX_RECORDS, COST_PER_1K_TOKENS)
        import redis as _redis
        from datetime import datetime, timezone
        import inspect
        if not caller:
            for frame_info in inspect.stack()[2:6]:
                fn = os.path.basename(frame_info.filename)
                if fn not in ("base.py", "llm_router.py"):
                    caller = fn.replace(".py", "")
                    break
            if not caller:
                caller = "create_llm"
        _r = _redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
        now = datetime.now(timezone.utc)
        total_tokens = prompt_tokens + completion_tokens
        cost_key = f"{provider}/{model}"
        rates = COST_PER_1K_TOKENS.get(cost_key, {"input": 0.002, "output": 0.008})
        cost_yuan = (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1000
        record = {
            "ts": now.isoformat(), "epoch": now.timestamp(),
            "provider": provider, "model": model, "task_type": task_type,
            "caller": caller,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "total_tokens": total_tokens, "cost_yuan": round(cost_yuan, 6),
            "latency_ms": round(latency_ms, 1), "success": success,
        }
        _r.lpush(LLM_USAGE_KEY, json.dumps(record))
        _r.ltrim(LLM_USAGE_KEY, 0, LLM_USAGE_MAX_RECORDS - 1)
        day_key = LLM_USAGE_DAILY_PREFIX + now.strftime("%Y-%m-%d")
        pipe = _r.pipeline()
        pipe.hincrby(day_key, "calls", 1)
        pipe.hincrbyfloat(day_key, "prompt_tokens", prompt_tokens)
        pipe.hincrbyfloat(day_key, "completion_tokens", completion_tokens)
        pipe.hincrbyfloat(day_key, "total_tokens", total_tokens)
        pipe.hincrbyfloat(day_key, "cost_yuan", cost_yuan)
        pipe.hincrbyfloat(day_key, f"calls:{provider}", 1)
        pipe.hincrbyfloat(day_key, f"tokens:{provider}", total_tokens)
        pipe.hincrbyfloat(day_key, f"cost:{provider}", cost_yuan)
        pipe.hincrbyfloat(day_key, f"calls:{task_type}", 1)
        mk = f"{provider}/{model}"
        pipe.hincrbyfloat(day_key, f"mcalls:{mk}", 1)
        pipe.hincrbyfloat(day_key, f"mtokens:{mk}", total_tokens)
        pipe.hincrbyfloat(day_key, f"mcost:{mk}", cost_yuan)
        pipe.expire(day_key, 90 * 86400)
        pipe.execute()
    except Exception:
        pass


class _TrackedLLM:
    """Wrapper that tracks ainvoke calls from create_llm() path."""

    def __init__(self, inner, provider: str, model: str):
        self._inner = inner
        self._provider = provider
        self._model = model

    async def ainvoke(self, messages, *args, **kwargs):
        t0 = time.time()
        try:
            result = await self._inner.ainvoke(messages, *args, **kwargs)
            latency = (time.time() - t0) * 1000
            prompt_est = sum(len(str(getattr(m, 'content', m))) for m in messages) // 4
            comp_est = len(str(getattr(result, 'completion', result))) // 4
            _log_create_llm_usage(self._provider, self._model, "create_llm",
                                  latency, True, prompt_est, comp_est)
            return result
        except Exception as e:
            latency = (time.time() - t0) * 1000
            _log_create_llm_usage(self._provider, self._model, "create_llm",
                                  latency, False)
            raise

    def __getattr__(self, name):
        return getattr(self._inner, name)


def create_llm(provider: str = "", model: str = "", api_key: str = "", base_url: str = ""):
    """
    LLM 工厂 — 使用 langchain 标准接口
    注意: 本地 Ollama 仅用于记忆嵌入 (bge-m3)，推理分析统一走云端 API
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()
    model = model or os.getenv("LLM_MODEL", "qwen3.5-plus")
    api_key = api_key or os.getenv("LLM_API_KEY", os.getenv("BAILIAN_API_KEY", ""))
    base_url = base_url or os.getenv("LLM_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")

    if provider == "ollama":
        from langchain_openai import ChatOpenAI as _ChatOpenAI
        return _ChatOpenAI(model=model, base_url="http://127.0.0.1:11434/v1", api_key="ollama")
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        kw = {"model": model, "api_key": api_key}
        if base_url:
            kw["base_url"] = base_url
        inner = ChatOpenAI(**kw)
    elif provider in ("claude", "anthropic"):
        from langchain_anthropic import ChatAnthropic
        kw = {"model": model, "api_key": api_key}
        if base_url:
            kw["anthropic_api_url"] = base_url
        inner = ChatAnthropic(**kw)
    elif provider == "deepseek":
        from langchain_openai import ChatOpenAI
        inner = ChatOpenAI(model=model, api_key=api_key, base_url=base_url or "https://api.deepseek.com/v1")
    elif provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        inner = ChatGoogleGenerativeAI(model=model, google_api_key=api_key)
    else:
        from langchain_openai import ChatOpenAI
        kw = {"model": model, "api_key": api_key}
        if base_url:
            kw["base_url"] = base_url
        inner = ChatOpenAI(**kw)

    tracked_provider = provider
    if base_url and "dashscope" in base_url:
        tracked_provider = "bailian"
    elif base_url and "siliconflow" in base_url:
        tracked_provider = "siliconflow"

    return _TrackedLLM(inner, tracked_provider, model)


def build_soul_prompt(agent: "BaseAgent", user_prompt: str) -> str:
    """将 SOUL 身份 + 用户 prompt 组合为完整提示词"""
    if not agent.soul:
        return user_prompt
    soul_header = agent.soul.split("\n## 错误处理")[0]
    return f"{soul_header}\n\n---\n\n{user_prompt}"


def run_agent(agent: BaseAgent):
    """入口函数：加载 .env 并启动 Agent 事件循环"""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram.env"))
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{agent.name}] %(levelname)s: %(message)s",
    )
    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        pass
