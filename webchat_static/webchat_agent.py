"""
OpenClaw Web Chat Agent — Chainlit 对话式 AI Agent (Multi-Agent 版)
功能:
  - 多轮对话 Chat（流式输出）
  - 模型动态切换（百炼/Ollama / OpenAI / Claude / DeepSeek / Gemini）
  - 通过 Redis → Orchestrator 路由所有命令到专项 Agent
  - /task /url /screen /sh /zt /lb /bk /hot /ask /dev /news 等

启动:
  .venv/bin/chainlit run webchat_agent.py --host 0.0.0.0 --port 7789 -h
"""

import os
import subprocess
import time
import json
import logging
import asyncio
import uuid

for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "*"

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webchat.env"))
load_dotenv()

import chainlit as cl
from chainlit.input_widget import Select, TextInput

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("webchat")

SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "30"))
WEBCHAT_AUTH_USER = os.getenv("WEBCHAT_AUTH_USER", "")
WEBCHAT_AUTH_PASS = os.getenv("WEBCHAT_AUTH_PASS", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REPLY_TIMEOUT = int(os.getenv("REPLY_TIMEOUT", "120"))
DANGEROUS_PATTERNS = ["rm -rf /", "mkfs", "dd if=/dev", ":(){", "fork bomb", "> /dev/sda"]

PROVIDER_MODELS = {
    "bailian": ["qwen-max-latest", "qwen-plus-latest", "qwen-turbo-latest", "qwen3-235b-a22b", "qwen2.5-72b-instruct"],
    "ollama": ["qwen2.5-coder:14b", "deepseek-r1:14b"],
    "openai": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
    "claude": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "gemini": ["gemini-2.0-flash", "gemini-2.5-pro"],
}

BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _patch_provider(llm, provider_name: str):
    """browser-use 0.11+ 内部访问 llm.provider，Pydantic 模型不允许直接赋值，需 object.__setattr__ 绕过。"""
    try:
        object.__setattr__(llm, 'provider', provider_name)
    except Exception:
        try:
            llm.__dict__['provider'] = provider_name
        except Exception:
            pass
    return llm

ROUTED_COMMANDS = {
    "zt", "lb", "bk", "hot", "summary",
    "ask", "dev", "ssh",
    "task", "url",
    "screen", "shell", "app", "type", "key", "click", "windows",
    "news",
}

SYSTEM_PROMPT = (
    "你是 OpenClaw Agent，运行在 Mac Mini M4 上的 AI 助手。"
    "你可以帮助用户完成各种任务，包括回答问题、分析信息、编程辅助等。"
    "如果用户需要操控浏览器，建议使用 /task 命令；需要执行系统命令，使用 /sh 命令。"
    "请用中文回答。回答要简洁有用。"
)


_redis_pool = None


async def get_redis():
    global _redis_pool
    if _redis_pool is None:
        import redis.asyncio as aioredis
        _redis_pool = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis_pool


async def send_to_orchestrator(command: str, args: str) -> str:
    """向 Orchestrator 发送命令并等待回复"""
    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"

    r = await get_redis()
    msg = json.dumps({
        "id": msg_id,
        "sender": "webchat",
        "target": "orchestrator",
        "action": "route",
        "params": {"command": command, "args": args, "reply_channel": reply_channel},
        "timestamp": time.time(),
    })
    await r.publish("rragent:orchestrator", msg)

    pubsub = r.pubsub()
    await pubsub.subscribe(reply_channel)
    try:
        deadline = asyncio.get_event_loop().time() + REPLY_TIMEOUT
        async for raw in pubsub.listen():
            if raw["type"] != "message":
                continue
            data = json.loads(raw["data"])
            return data.get("text", json.dumps(data, ensure_ascii=False, indent=2))
    except asyncio.TimeoutError:
        return "⏱️ 超时，Agent 未在规定时间内回复"
    finally:
        await pubsub.unsubscribe(reply_channel)


def is_safe_command(cmd: str) -> bool:
    cmd_lower = cmd.lower().strip()
    return not any(p in cmd_lower for p in DANGEROUS_PATTERNS)


def create_llm_for_agent(provider: str, model: str, api_key: str, base_url: str):
    """browser-use Agent 专用 — 使用 langchain 标准接口"""
    provider = provider.lower()
    if provider in ("bailian", "tongyi", "qwen", "dashscope"):
        from langchain_openai import ChatOpenAI
        _key = api_key or os.getenv("BAILIAN_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
        llm = ChatOpenAI(model=model, api_key=_key, base_url=base_url or BAILIAN_BASE_URL)
        return _patch_provider(llm, "openai")
    elif provider == "ollama":
        from langchain_openai import ChatOpenAI as _ChatOpenAI
        llm = _ChatOpenAI(model=model, base_url="http://127.0.0.1:11434/v1", api_key="ollama")
        return _patch_provider(llm, "ollama")
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        kwargs = {"model": model, "api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return _patch_provider(ChatOpenAI(**kwargs), "openai")
    elif provider in ("claude", "anthropic"):
        from langchain_anthropic import ChatAnthropic
        kwargs = {"model": model, "api_key": api_key}
        if base_url:
            kwargs["anthropic_api_url"] = base_url
        return _patch_provider(ChatAnthropic(**kwargs), "anthropic")
    elif provider == "deepseek":
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url or "https://api.deepseek.com/v1")
        return _patch_provider(llm, "openai")
    elif provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(model=model, google_api_key=api_key)
        return _patch_provider(llm, "google")
    else:
        from langchain_openai import ChatOpenAI
        kwargs = {"model": model, "api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return _patch_provider(ChatOpenAI(**kwargs), "openai")


def create_llm_for_chat(provider: str, model: str, api_key: str, base_url: str):
    """普通对话用 — 使用 LangChain 类（支持 astream 流式输出）"""
    provider = provider.lower()
    if provider in ("bailian", "tongyi", "qwen", "dashscope"):
        from langchain_openai import ChatOpenAI
        _key = api_key or os.getenv("BAILIAN_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
        return ChatOpenAI(model=model, api_key=_key, base_url=base_url or BAILIAN_BASE_URL, streaming=True)
    elif provider == "ollama":
        from langchain_openai import ChatOpenAI as _ChatOpenAI
        return _ChatOpenAI(model=model, base_url="http://127.0.0.1:11434/v1", api_key="ollama", streaming=True)
    elif provider in ("openai", "deepseek"):
        from langchain_openai import ChatOpenAI
        kwargs = {"model": model, "api_key": api_key, "streaming": True}
        if provider == "deepseek":
            kwargs["base_url"] = base_url or "https://api.deepseek.com/v1"
        elif base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)
    elif provider in ("claude", "anthropic"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, api_key=api_key, streaming=True)
    elif provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, streaming=True)
    else:
        from langchain_openai import ChatOpenAI
        kwargs = {"model": model, "api_key": api_key, "streaming": True}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)


def get_session_config():
    provider = cl.user_session.get("provider") or os.getenv("LLM_PROVIDER", "ollama")
    model = cl.user_session.get("model") or os.getenv("LLM_MODEL", "qwen2.5-coder:14b")
    api_key = cl.user_session.get("api_key") or os.getenv("LLM_API_KEY", "")
    base_url = cl.user_session.get("base_url") or os.getenv("LLM_BASE_URL", "")
    return provider, model, api_key, base_url


def run_shell(cmd: str) -> str:
    if not is_safe_command(cmd):
        return "⚠️ 危险命令已拦截"
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=SHELL_TIMEOUT, cwd=os.path.expanduser("~"),
            env={**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        )
        output = (proc.stdout + proc.stderr).strip() or "(无输出)"
        if proc.returncode != 0:
            output += f"\n[退出码: {proc.returncode}]"
        return output
    except subprocess.TimeoutExpired:
        return f"⏰ 命令超时 ({SHELL_TIMEOUT}s)"
    except Exception as e:
        return f"执行失败: {e}"


def take_screenshot() -> str | None:
    tmp = "/tmp/openclaw_webchat_screen.jpg"
    try:
        proc = subprocess.run(["screencapture", "-x", "-C", "-t", "jpg", tmp], timeout=10, capture_output=True)
        if proc.returncode == 0 and os.path.exists(tmp):
            file_size = os.path.getsize(tmp)
            if file_size > 5 * 1024 * 1024:
                subprocess.run(["sips", "--resampleWidth", "1920", tmp, "--out", tmp], timeout=10, capture_output=True)
            return tmp
    except Exception as e:
        logger.error(f"Screenshot error: {e}")
    return None




if WEBCHAT_AUTH_USER and WEBCHAT_AUTH_PASS:
    @cl.password_auth_callback
    def auth_callback(username: str, password: str):
        if username == WEBCHAT_AUTH_USER and password == WEBCHAT_AUTH_PASS:
            return cl.User(identifier=username, metadata={"role": "admin"})
        return None


@cl.on_chat_start
async def on_chat_start():
    default_provider = os.getenv("LLM_PROVIDER", "ollama")
    default_model = os.getenv("LLM_MODEL", "qwen2.5-coder:14b")

    all_model_values = []
    for prov, models in PROVIDER_MODELS.items():
        for m in models:
            all_model_values.append(f"{prov}/{m}")

    current_val = f"{default_provider}/{default_model}"
    if current_val not in all_model_values:
        all_model_values.insert(0, current_val)

    initial_idx = all_model_values.index(current_val) if current_val in all_model_values else 0

    settings = await cl.ChatSettings([
        Select(
            id="model_combo",
            label="AI 模型",
            values=all_model_values,
            initial_index=initial_idx,
        ),
        TextInput(
            id="api_key",
            label="API Key（ollama 不需要）",
            initial=os.getenv("LLM_API_KEY", ""),
            placeholder="sk-... / sk-ant-... / AIza...",
        ),
        TextInput(
            id="base_url",
            label="自定义 API 端点（可选）",
            initial=os.getenv("LLM_BASE_URL", ""),
            placeholder="留空用官方地址",
        ),
    ]).send()

    parts = settings["model_combo"].split("/", 1)
    cl.user_session.set("provider", parts[0])
    cl.user_session.set("model", parts[1] if len(parts) > 1 else parts[0])
    cl.user_session.set("api_key", settings.get("api_key", ""))
    cl.user_session.set("base_url", settings.get("base_url", ""))
    cl.user_session.set("history", [])

    await cl.Message(
        content=(
            f"**OpenClaw Agent 就绪** — `{settings['model_combo']}`\n\n"
            "直接输入文字与 AI 对话，或使用快捷命令：\n"
            "- `/task <任务>` — 浏览器自动化\n"
            "- `/sh <命令>` — 执行 Shell\n"
            "- `/screen` — 截取桌面\n\n"
            "点击左下角 ⚙️ 可切换模型。"
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings):
    parts = settings["model_combo"].split("/", 1)
    cl.user_session.set("provider", parts[0])
    cl.user_session.set("model", parts[1] if len(parts) > 1 else parts[0])
    cl.user_session.set("api_key", settings.get("api_key", ""))
    cl.user_session.set("base_url", settings.get("base_url", ""))
    provider = parts[0]
    model = parts[1] if len(parts) > 1 else parts[0]
    await cl.Message(content=f"✅ 模型已切换: `{provider}/{model}`").send()


@cl.on_message
async def on_message(message: cl.Message):
    msg_text = message.content.strip()
    provider, model, api_key, base_url = get_session_config()

    # 路由命令到 Orchestrator
    if msg_text.startswith("/"):
        parts = msg_text[1:].split(None, 1)
        cmd_name = parts[0].lower() if parts else ""
        cmd_args = parts[1] if len(parts) > 1 else ""

        if cmd_name in ROUTED_COMMANDS:
            resp = cl.Message(content=f"⏳ 正在处理 `/{cmd_name}` ...")
            await resp.send()
            try:
                result = await send_to_orchestrator(cmd_name, cmd_args)
                await cl.Message(content=result[:4000]).send()
            except Exception as e:
                logger.error(f"Route error: {e}", exc_info=True)
                await cl.Message(content=f"❌ 错误: {e}").send()
            return

        if cmd_name == "sh":
            cmd = cmd_args.strip()
            if not cmd:
                await cl.Message(content="用法: `/sh <命令>`").send()
                return
            resp = cl.Message(content=f"⚙️ 执行: `{cmd}`")
            await resp.send()
            output = run_shell(cmd)
            await cl.Message(content=f"```\n{output}\n```").send()
            return

    # 普通对话 — 流式输出
    history = cl.user_session.get("history") or []

    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for h in history:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))
    messages.append(HumanMessage(content=msg_text))

    resp = cl.Message(content="")

    try:
        llm = create_llm_for_chat(provider, model, api_key, base_url)
        full_reply = ""
        async for chunk in llm.astream(messages):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            if token:
                full_reply += token
                await resp.stream_token(token)
        await resp.send()

        history.append({"role": "user", "content": msg_text})
        history.append({"role": "assistant", "content": full_reply})
        if len(history) > 40:
            history = history[-40:]
        cl.user_session.set("history", history)
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        resp.content = f"❌ 错误: {e}"
        await resp.send()
