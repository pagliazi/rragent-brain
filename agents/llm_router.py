"""
LLM Router — 云端多模型路由 + 速率限制处理 + 按任务类型选模型
灵感来源: awesome-openclaw-skills 中的 llm-supervisor / smart-router / model-router

架构定位:
- 本地 Ollama 仅负责记忆嵌入 (bge-m3)，不参与推理
- 所有推理分析 (分析/简报/代码) 统一走云端 API
- 首选: 百炼 Coding Plan (qwen3.5-plus / qwen3-max / qwen3-coder)
- 回退链: Bailian → SiliconFlow → DeepSeek → DashScope → OpenAI
- 按任务复杂度自动选模型
- 内置速率限制跟踪与自动切换
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

try:
    import redis
    _sync_redis = redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
except Exception:
    _sync_redis = None

LLM_PREF_KEY = "rragent:llm_preference"
LLM_TASK_PREF_KEY = "rragent:llm_task_prefs"
LLM_USAGE_KEY = "rragent:llm_usage"
LLM_USAGE_DAILY_PREFIX = "rragent:llm_usage:daily:"
LLM_USAGE_MAX_RECORDS = 5000

VALID_TASK_TYPES = frozenset(("brief", "analysis", "code", "default", "triage", "reasoning"))

logger = logging.getLogger("agent.llm_router")

MODEL_META: dict[str, dict] = {
    "qwen3.5-plus":    {"label": "Qwen3.5 Plus",     "tags": ["推荐", "图片理解", "思考"], "ctx": 1000000, "out": 65536},
    "qwen3-max":       {"label": "Qwen3 Max",        "tags": ["深度分析"],               "ctx": 262144,  "out": 32768},
    "qwen3-coder-plus":{"label": "Qwen3 Coder Plus", "tags": ["代码专精"],               "ctx": 1000000, "out": 65536},
    "qwen3-coder-next":{"label": "Qwen3 Coder Next", "tags": ["代码专精"],               "ctx": 262144,  "out": 65536},
    "glm-5":           {"label": "GLM-5",            "tags": ["推荐", "思考"],            "ctx": 202752,  "out": 16384},
    "glm-4.7":         {"label": "GLM-4.7",          "tags": ["思考"],                   "ctx": 202752,  "out": 16384},
    "kimi-k2.5":       {"label": "Kimi K2.5",        "tags": ["推荐", "图片理解", "思考"], "ctx": 262144,  "out": 32768},
    "MiniMax-M2.5":    {"label": "MiniMax M2.5",     "tags": ["推荐", "思考"],            "ctx": 196608,  "out": 24576},
    "deepseek-v3":     {"label": "DeepSeek V3",      "tags": ["高性价比"],               "ctx": 131072,  "out": 8192},
    "qwen-72b":        {"label": "Qwen2.5 72B",      "tags": [],                        "ctx": 131072,  "out": 8192},
    "deepseek-chat":   {"label": "DeepSeek Chat",    "tags": ["高性价比"],               "ctx": 131072,  "out": 8192},
    "deepseek-reasoner":{"label":"DeepSeek Reasoner", "tags": ["深度推理"],               "ctx": 131072,  "out": 8192},
    "qwen-max":        {"label": "Qwen Max",         "tags": [],                        "ctx": 131072,  "out": 8192},
    "qwen-turbo":      {"label": "Qwen Turbo",       "tags": ["高性价比"],               "ctx": 131072,  "out": 8192},
    "gpt-4o-mini":     {"label": "GPT-4o Mini",      "tags": ["高性价比"],               "ctx": 128000,  "out": 16384},
}

COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "bailian/qwen3.5-plus":       {"input": 0.003, "output": 0.012},
    "bailian/qwen3-max":          {"input": 0.004, "output": 0.016},
    "bailian/qwen3-coder-plus":   {"input": 0.003, "output": 0.012},
    "bailian/qwen3-coder-next":   {"input": 0.003, "output": 0.012},
    "bailian/glm-5":              {"input": 0.004, "output": 0.016},
    "bailian/glm-4.7":            {"input": 0.004, "output": 0.016},
    "bailian/kimi-k2.5":          {"input": 0.004, "output": 0.016},
    "bailian/MiniMax-M2.5":       {"input": 0.004, "output": 0.016},
    "siliconflow/deepseek-v3":    {"input": 0.001, "output": 0.002},
    "siliconflow/qwen-72b":       {"input": 0.004, "output": 0.004},
    "deepseek/deepseek-chat":     {"input": 0.001, "output": 0.002},
    "deepseek/deepseek-reasoner": {"input": 0.004, "output": 0.016},
    "dashscope/qwen-max":         {"input": 0.004, "output": 0.012},
    "dashscope/qwen-turbo":       {"input": 0.0008, "output": 0.002},
    "openai/gpt-4o-mini":         {"input": 0.00015, "output": 0.0006},
}

TASK_PROFILES = {
    "brief": {
        "description": "快速简报、摘要",
        "max_tokens": 512,
        "temperature": 0.5,
        "preferred_chain": [
            ("bailian", "kimi-k2.5"),
            ("bailian", "MiniMax-M2.5"),
            ("deepseek", "deepseek-chat"),
            ("bailian", "glm-5"),
            ("bailian", "glm-4.7"),
            ("siliconflow", "deepseek-v3"),
            ("bailian", "qwen3.5-plus"),
            ("dashscope", "qwen-turbo"),
            ("openai", "gpt-4o-mini"),
        ],
    },
    "analysis": {
        "description": "深度分析、研判",
        "max_tokens": 2048,
        "temperature": 0.7,
        "preferred_chain": [
            ("bailian", "qwen3-max"),
            ("bailian", "qwen3.5-plus"),
            ("bailian", "glm-5"),
            ("bailian", "MiniMax-M2.5"),
            ("siliconflow", "deepseek-v3"),
            ("deepseek", "deepseek-chat"),
            ("dashscope", "qwen-max"),
            ("openai", "gpt-4o-mini"),
        ],
    },
    "code": {
        "description": "代码生成、技术任务",
        "max_tokens": 4096,
        "temperature": 0.3,
        "preferred_chain": [
            ("bailian", "qwen3-coder-plus"),
            ("bailian", "qwen3-coder-next"),
            ("bailian", "glm-5"),
            ("bailian", "MiniMax-M2.5"),
            ("siliconflow", "deepseek-v3"),
            ("deepseek", "deepseek-chat"),
            ("dashscope", "qwen-max"),
            ("openai", "gpt-4o-mini"),
        ],
    },
    "default": {
        "description": "通用任务",
        "max_tokens": 1024,
        "temperature": 0.7,
        "preferred_chain": [
            ("bailian", "kimi-k2.5"),
            ("bailian", "MiniMax-M2.5"),
            ("deepseek", "deepseek-chat"),
            ("bailian", "qwen3.5-plus"),
            ("bailian", "glm-5"),
            ("siliconflow", "deepseek-v3"),
            ("dashscope", "qwen-turbo"),
            ("openai", "gpt-4o-mini"),
        ],
    },
    "triage": {
        "description": "L1 分诊: 极轻量意图分类",
        "max_tokens": 150,
        "temperature": 0.0,
        "preferred_chain": [
            ("bailian", "kimi-k2.5"),
            ("deepseek", "deepseek-chat"),
            ("bailian", "glm-5"),
            ("bailian", "glm-4.7"),
            ("bailian", "qwen3.5-plus"),
            ("dashscope", "qwen-turbo"),
            ("siliconflow", "deepseek-v3"),
        ],
    },
    "reasoning": {
        "description": "L2 深度推理: 复杂规划、多步思考",
        "max_tokens": 4096,
        "temperature": 0.3,
        "preferred_chain": [
            ("deepseek", "deepseek-reasoner"),
            ("bailian", "qwen3-max"),
            ("bailian", "glm-5"),
            ("bailian", "qwen3.5-plus"),
            ("bailian", "MiniMax-M2.5"),
            ("siliconflow", "deepseek-v3"),
        ],
    },
}

CLOUD_PROVIDERS = {
    "bailian": {
        "base_url": os.getenv("BAILIAN_BASE_URL",
                              "https://coding.dashscope.aliyuncs.com/v1"),
        "api_key": os.getenv("BAILIAN_API_KEY", ""),
        "models": {
            "qwen3.5-plus": "qwen3.5-plus",
            "qwen3-max": "qwen3-max-2026-01-23",
            "qwen3-coder-next": "qwen3-coder-next",
            "qwen3-coder-plus": "qwen3-coder-plus",
            "glm-5": "glm-5",
            "glm-4.7": "glm-4.7",
            "kimi-k2.5": "kimi-k2.5",
            "MiniMax-M2.5": "MiniMax-M2.5",
        },
    },
    "siliconflow": {
        "base_url": os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
        "api_key": os.getenv("SILICONFLOW_API_KEY", ""),
        "models": {
            "deepseek-v3": "deepseek-ai/DeepSeek-V3",
            "qwen-72b": "Qwen/Qwen2.5-72B-Instruct",
        },
    },
    "deepseek": {
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "api_key": os.getenv("DEEPSEEK_API_KEY", os.getenv("LLM_API_KEY", "")),
        "models": {
            "deepseek-chat": "deepseek-chat",
            "deepseek-reasoner": "deepseek-reasoner",
        },
    },
    "dashscope": {
        "base_url": os.getenv("DASHSCOPE_BASE_URL",
                              "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "models": {
            "qwen-max": "qwen-max",
            "qwen-turbo": "qwen-turbo",
        },
    },
    "openai": {
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "models": {
            "gpt-4o-mini": "gpt-4o-mini",
        },
    },
}


@dataclass
class RateLimitState:
    provider: str
    retry_after: float = 0.0
    consecutive_failures: int = 0
    last_failure: float = 0.0


@dataclass
class RouterStats:
    total_calls: int = 0
    fallback_count: int = 0
    rate_limit_hits: int = 0
    errors: int = 0
    provider_usage: dict = field(default_factory=dict)


class LLMRouter:
    """云端 LLM 路由器 — 本地 Ollama 仅用于嵌入，推理全走云端"""

    _TASK_PREF_RELOAD_INTERVAL = 30

    def __init__(self):
        self._rate_limits: dict[str, RateLimitState] = {}
        self._stats = RouterStats()
        self._pinned: Optional[tuple[str, str]] = None
        self._pinned_tasks: dict[str, tuple[str, str]] = {}
        self._task_pref_loaded_at: float = 0.0
        self._load_preference()

    def _load_preference(self):
        try:
            if _sync_redis:
                raw = _sync_redis.get(LLM_PREF_KEY)
                if raw:
                    pref = json.loads(raw)
                    p, m = pref.get("provider", ""), pref.get("model", "")
                    if p and m and p in CLOUD_PROVIDERS:
                        self._pinned = (p, m)
                        logger.info(f"Loaded LLM preference: {p}/{m}")
                raw_tasks = _sync_redis.get(LLM_TASK_PREF_KEY)
                self._load_task_prefs_from_redis()
        except Exception as e:
            logger.debug(f"Load preference failed: {e}")

    def _load_task_prefs_from_redis(self):
        try:
            if not _sync_redis:
                return
            raw_tasks = _sync_redis.get(LLM_TASK_PREF_KEY)
            new_prefs: dict[str, tuple[str, str]] = {}
            if raw_tasks:
                task_prefs = json.loads(raw_tasks)
                for tt, obj in task_prefs.items():
                    p, m = obj.get("provider", ""), obj.get("model", "")
                    if tt in VALID_TASK_TYPES and p in CLOUD_PROVIDERS and m:
                        new_prefs[tt] = (p, m)
            if new_prefs != self._pinned_tasks:
                self._pinned_tasks = new_prefs
                if new_prefs:
                    logger.info(f"Loaded task prefs: {list(new_prefs.keys())}")
            self._task_pref_loaded_at = time.time()
        except Exception as e:
            logger.debug(f"Reload task prefs failed: {e}")

    def _maybe_reload_task_prefs(self):
        if time.time() - self._task_pref_loaded_at > self._TASK_PREF_RELOAD_INTERVAL:
            self._load_task_prefs_from_redis()

    def set_preference(self, provider: str, model: str) -> bool:
        if provider not in CLOUD_PROVIDERS:
            return False
        if model not in CLOUD_PROVIDERS[provider]["models"]:
            return False
        self._pinned = (provider, model)
        try:
            if _sync_redis:
                _sync_redis.set(LLM_PREF_KEY, json.dumps({"provider": provider, "model": model}))
        except Exception:
            pass
        logger.info(f"LLM preference set: {provider}/{model}")
        return True

    def clear_preference(self):
        self._pinned = None
        try:
            if _sync_redis:
                _sync_redis.delete(LLM_PREF_KEY)
        except Exception:
            pass
        logger.info("LLM preference cleared (auto-route)")

    def get_preference(self) -> Optional[dict]:
        if self._pinned:
            return {"provider": self._pinned[0], "model": self._pinned[1]}
        return None

    def set_task_preference(self, task_type: str, provider: str, model: str) -> bool:
        if task_type not in VALID_TASK_TYPES:
            return False
        if provider not in CLOUD_PROVIDERS:
            return False
        if model not in CLOUD_PROVIDERS[provider]["models"]:
            return False
        self._pinned_tasks[task_type] = (provider, model)
        self._persist_task_prefs()
        logger.info(f"Task preference set: {task_type} -> {provider}/{model}")
        return True

    def clear_task_preference(self, task_type: str) -> bool:
        if task_type not in VALID_TASK_TYPES:
            return False
        removed = self._pinned_tasks.pop(task_type, None)
        if removed:
            self._persist_task_prefs()
            logger.info(f"Task preference cleared: {task_type}")
        return removed is not None

    def get_task_preferences(self) -> dict:
        result: dict[str, dict] = {}
        for tt in VALID_TASK_TYPES:
            profile = TASK_PROFILES[tt]
            default_p, default_m = profile["preferred_chain"][0]
            pin = self._pinned_tasks.get(tt)
            result[tt] = {
                "description": profile["description"],
                "override": {"provider": pin[0], "model": pin[1]} if pin else None,
                "default": {"provider": default_p, "model": default_m},
                "effective": {
                    "provider": pin[0] if pin else default_p,
                    "model": pin[1] if pin else default_m,
                },
            }
        return result

    def _persist_task_prefs(self):
        try:
            if _sync_redis:
                data = {tt: {"provider": p, "model": m}
                        for tt, (p, m) in self._pinned_tasks.items()}
                _sync_redis.set(LLM_TASK_PREF_KEY, json.dumps(data))
        except Exception:
            pass

    def _is_rate_limited(self, provider: str) -> bool:
        state = self._rate_limits.get(provider)
        if not state:
            return False
        if state.retry_after > time.time():
            return True
        if state.consecutive_failures >= 5:
            cooldown = min(300, 30 * (2 ** min(state.consecutive_failures, 8)))
            if time.time() - state.last_failure < cooldown:
                return True
        return False

    def _record_rate_limit(self, provider: str, retry_after_sec: float = 60):
        state = self._rate_limits.setdefault(provider, RateLimitState(provider=provider))
        state.retry_after = time.time() + retry_after_sec
        state.consecutive_failures += 1
        state.last_failure = time.time()
        self._stats.rate_limit_hits += 1

    def _record_success(self, provider: str):
        if provider in self._rate_limits:
            self._rate_limits[provider].consecutive_failures = 0

    async def chat(
        self,
        messages: list[dict],
        task_type: str = "default",
        temperature: Optional[float] = None,
    ) -> Optional[str]:
        """
        统一聊天接口：按任务类型路由到最佳可用云端模型。

        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
            task_type: "brief" | "analysis" | "code" | "default"
            temperature: 覆盖默认温度（None 则使用 profile 默认值）
        """
        self._maybe_reload_task_prefs()

        profile = TASK_PROFILES.get(task_type, TASK_PROFILES["default"])
        temp = temperature if temperature is not None else profile.get("temperature", 0.7)
        max_tokens = profile["max_tokens"]

        self._stats.total_calls += 1
        tried_first = False

        chain = list(profile["preferred_chain"])
        task_pin = self._pinned_tasks.get(task_type)
        if task_pin:
            tp, tm = task_pin
            chain = [(tp, tm)] + [(p, m) for p, m in chain if not (p == tp and m == tm)]
        elif self._pinned:
            pin_p, pin_m = self._pinned
            chain = [(pin_p, pin_m)] + [(p, m) for p, m in chain if not (p == pin_p and m == pin_m)]

        for provider_name, model_alias in chain:
            if self._is_rate_limited(provider_name):
                logger.debug(f"Skipping {provider_name}: rate limited")
                continue

            config = CLOUD_PROVIDERS.get(provider_name)
            if not config or not config.get("api_key"):
                continue

            model_id = config["models"].get(model_alias)
            if not model_id:
                continue

            if tried_first:
                self._stats.fallback_count += 1

            result = await self._call_cloud(provider_name, config, model_id,
                                            messages, temp, max_tokens,
                                            task_type=task_type)
            if result is not None:
                self._stats.provider_usage[provider_name] = \
                    self._stats.provider_usage.get(provider_name, 0) + 1
                return result

            tried_first = True

        self._stats.errors += 1
        logger.error("All cloud LLM providers exhausted")
        return None

    def _log_usage(self, provider: str, model: str, task_type: str,
                   usage: dict, latency_ms: float, success: bool,
                   caller: str = ""):
        """将每次 API 调用记录到 Redis（用于前端监控面板）。"""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        cost_key = f"{provider}/{model}"
        rates = COST_PER_1K_TOKENS.get(cost_key, {"input": 0.002, "output": 0.008})
        cost_yuan = (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1000

        if not caller:
            import inspect
            for frame_info in inspect.stack()[2:6]:
                fn = os.path.basename(frame_info.filename)
                if fn not in ("llm_router.py", "base.py", "_trackedllm.py"):
                    caller = fn.replace(".py", "")
                    break
            if not caller:
                caller = "llm_router"

        record = {
            "ts": now.isoformat(),
            "epoch": now.timestamp(),
            "provider": provider,
            "model": model,
            "task_type": task_type,
            "caller": caller,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_yuan": round(cost_yuan, 6),
            "latency_ms": round(latency_ms, 1),
            "success": success,
        }

        try:
            if _sync_redis:
                _sync_redis.lpush(LLM_USAGE_KEY, json.dumps(record))
                _sync_redis.ltrim(LLM_USAGE_KEY, 0, LLM_USAGE_MAX_RECORDS - 1)

                day_key = LLM_USAGE_DAILY_PREFIX + now.strftime("%Y-%m-%d")
                pipe = _sync_redis.pipeline()
                pipe.hincrby(day_key, "calls", 1)
                pipe.hincrbyfloat(day_key, "prompt_tokens", prompt_tokens)
                pipe.hincrbyfloat(day_key, "completion_tokens", completion_tokens)
                pipe.hincrbyfloat(day_key, "total_tokens", total_tokens)
                pipe.hincrbyfloat(day_key, "cost_yuan", cost_yuan)
                pipe.hincrbyfloat(day_key, f"calls:{provider}", 1)
                pipe.hincrbyfloat(day_key, f"tokens:{provider}", total_tokens)
                pipe.hincrbyfloat(day_key, f"cost:{provider}", cost_yuan)
                pipe.hincrbyfloat(day_key, f"calls:{task_type}", 1)
                model_key = f"{provider}/{model}"
                pipe.hincrbyfloat(day_key, f"mcalls:{model_key}", 1)
                pipe.hincrbyfloat(day_key, f"mtokens:{model_key}", total_tokens)
                pipe.hincrbyfloat(day_key, f"mcost:{model_key}", cost_yuan)
                pipe.expire(day_key, 90 * 86400)
                pipe.execute()
        except Exception as e:
            logger.debug(f"Usage log failed: {e}")

    async def _call_cloud(
        self, provider: str, config: dict, model: str,
        messages: list[dict], temperature: float, max_tokens: int,
        task_type: str = "default",
    ) -> Optional[str]:
        t0 = time.time()
        http_timeout = 30.0 if task_type in ("brief", "triage") else 90.0
        try:
            headers = {
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=http_timeout) as client:
                resp = await client.post(
                    f"{config['base_url']}/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                latency = (time.time() - t0) * 1000

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    self._record_success(provider)
                    self._log_usage(provider, model, task_type, usage, latency, True)
                    return content

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "60"))
                    self._record_rate_limit(provider, retry_after)
                    logger.warning(f"Rate limited by {provider}, retry after {retry_after}s")
                    self._log_usage(provider, model, task_type, {}, (time.time() - t0) * 1000, False)
                    return None

                logger.warning(f"Cloud {provider}/{model} returned {resp.status_code}: "
                               f"{resp.text[:200]}")
                self._log_usage(provider, model, task_type, {}, (time.time() - t0) * 1000, False)
        except Exception as e:
            logger.warning(f"Cloud call failed ({provider}/{model}): {e}")
            state = self._rate_limits.setdefault(provider, RateLimitState(provider=provider))
            state.consecutive_failures += 1
            state.last_failure = time.time()
            self._log_usage(provider, model, task_type, {}, (time.time() - t0) * 1000, False)
        return None

    def get_status(self) -> dict:
        return {
            "mode": "cloud-only (local Ollama = embedding only)",
            "pinned": {"provider": self._pinned[0], "model": self._pinned[1]} if self._pinned else None,
            "task_preferences": self.get_task_preferences(),
            "stats": {
                "total_calls": self._stats.total_calls,
                "fallback_count": self._stats.fallback_count,
                "rate_limit_hits": self._stats.rate_limit_hits,
                "errors": self._stats.errors,
                "provider_usage": self._stats.provider_usage,
            },
            "rate_limits": {
                k: {
                    "retry_after": max(0, round(v.retry_after - time.time(), 1)),
                    "consecutive_failures": v.consecutive_failures,
                }
                for k, v in self._rate_limits.items()
            },
            "available_providers": [
                name for name, cfg in CLOUD_PROVIDERS.items() if cfg.get("api_key")
            ],
            "providers": {
                name: {
                    "has_key": bool(cfg.get("api_key")),
                    "base_url": cfg.get("base_url", ""),
                    "models": list(cfg.get("models", {}).keys()),
                }
                for name, cfg in CLOUD_PROVIDERS.items()
            },
        }


    def get_usage_data(self, days: int = 30, recent_limit: int = 100) -> dict:
        """获取使用数据，供前端监控面板。"""
        from datetime import datetime, timedelta, timezone
        result = {
            "daily": [],
            "recent_calls": [],
            "totals": {"calls": 0, "tokens": 0, "cost_yuan": 0.0},
            "by_provider": {},
            "by_model": {},
            "by_task_type": {},
        }
        try:
            if not _sync_redis:
                return result

            today = datetime.now(timezone.utc).date()
            for i in range(days):
                d = today - timedelta(days=i)
                day_key = LLM_USAGE_DAILY_PREFIX + d.isoformat()
                data = _sync_redis.hgetall(day_key)
                if data:
                    day_entry = {
                        "date": d.isoformat(),
                        "calls": int(float(data.get("calls", 0))),
                        "prompt_tokens": int(float(data.get("prompt_tokens", 0))),
                        "completion_tokens": int(float(data.get("completion_tokens", 0))),
                        "total_tokens": int(float(data.get("total_tokens", 0))),
                        "cost_yuan": round(float(data.get("cost_yuan", 0)), 4),
                        "providers": {},
                        "task_types": {},
                    }
                    for k, v in data.items():
                        if k.startswith("mcalls:"):
                            model_name = k[7:]
                            agg = result["by_model"].setdefault(model_name, {"calls": 0, "tokens": 0, "cost_yuan": 0.0})
                            agg["calls"] += int(float(v))
                        elif k.startswith("mtokens:"):
                            model_name = k[8:]
                            result["by_model"].setdefault(model_name, {"calls": 0, "tokens": 0, "cost_yuan": 0.0})["tokens"] += int(float(v))
                        elif k.startswith("mcost:"):
                            model_name = k[6:]
                            result["by_model"].setdefault(model_name, {"calls": 0, "tokens": 0, "cost_yuan": 0.0})["cost_yuan"] += float(v)
                        elif k.startswith("calls:"):
                            name = k[6:]
                            if name in CLOUD_PROVIDERS:
                                day_entry["providers"].setdefault(name, {})["calls"] = int(float(v))
                            else:
                                day_entry["task_types"][name] = int(float(v))
                        elif k.startswith("tokens:"):
                            name = k[7:]
                            day_entry["providers"].setdefault(name, {})["tokens"] = int(float(v))
                        elif k.startswith("cost:"):
                            name = k[5:]
                            day_entry["providers"].setdefault(name, {})["cost_yuan"] = round(float(v), 4)

                    result["daily"].append(day_entry)
                    result["totals"]["calls"] += day_entry["calls"]
                    result["totals"]["tokens"] += day_entry["total_tokens"]
                    result["totals"]["cost_yuan"] += day_entry["cost_yuan"]
                    for prov, pdata in day_entry["providers"].items():
                        agg = result["by_provider"].setdefault(prov, {"calls": 0, "tokens": 0, "cost_yuan": 0.0})
                        agg["calls"] += pdata.get("calls", 0)
                        agg["tokens"] += pdata.get("tokens", 0)
                        agg["cost_yuan"] += pdata.get("cost_yuan", 0.0)
                    for tt, cnt in day_entry["task_types"].items():
                        result["by_task_type"][tt] = result["by_task_type"].get(tt, 0) + cnt

            result["totals"]["cost_yuan"] = round(result["totals"]["cost_yuan"], 4)
            for v in result["by_provider"].values():
                v["cost_yuan"] = round(v["cost_yuan"], 4)

            raw_records = _sync_redis.lrange(LLM_USAGE_KEY, 0, recent_limit - 1)
            result["recent_calls"] = [json.loads(r) for r in raw_records]

            if not result["by_model"]:
                for rec in result["recent_calls"]:
                    mk = f"{rec.get('provider','')}/{rec.get('model','')}"
                    agg = result["by_model"].setdefault(mk, {"calls": 0, "tokens": 0, "cost_yuan": 0.0})
                    agg["calls"] += 1
                    agg["tokens"] += rec.get("total_tokens", 0)
                    agg["cost_yuan"] += rec.get("cost_yuan", 0.0)

            for v in result["by_model"].values():
                v["cost_yuan"] = round(v["cost_yuan"], 4)
        except Exception as e:
            logger.debug(f"get_usage_data failed: {e}")

        return result


_router: Optional[LLMRouter] = None


def get_llm_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
