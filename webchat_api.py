"""
OpenClaw Dashboard — FastAPI 后端
SSE 流式响应 + REST API + 静态文件服务
"""

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webchat.env"))
load_dotenv()

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as aioredis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [API] %(levelname)s: %(message)s")
logger = logging.getLogger("api")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REPLY_TIMEOUT = int(os.getenv("REPLY_TIMEOUT", "60"))
WEBCHAT_PORT = int(os.getenv("WEBCHAT_PORT", "7789"))
WEBCHAT_HOST = os.getenv("WEBCHAT_HOST", "0.0.0.0")
AUTH_USER = os.getenv("WEBCHAT_AUTH_USER", "")
AUTH_PASS = os.getenv("WEBCHAT_AUTH_PASS", "")
JWT_SECRET = os.getenv("JWT_SECRET", AUTH_PASS or "openclaw-secret-key-change-me")
N8N_SERVICE_TOKEN = os.getenv("N8N_SERVICE_TOKEN", "openclaw-n8n-2026")
JWT_EXPIRE = int(os.getenv("JWT_EXPIRE", "86400"))

USERS_KEY = "rragent:users"
AVATARS = ["🦀","🐙","🦊","🐯","🦁","🐺","🦅","🐋","🐬","🦈","🐉","🦄","🐝","🦋","🌟","⚡"]

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


# ── JWT Token ─────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

def create_token(username: str, role: str, display_name: str = "", avatar: str = "") -> str:
    header = _b64url(json.dumps({"alg":"HS256","typ":"JWT"}).encode())
    payload = _b64url(json.dumps({
        "sub": username, "role": role, "name": display_name or username,
        "avatar": avatar, "exp": int(time.time()) + JWT_EXPIRE, "iat": int(time.time()),
    }).encode())
    sig = _b64url(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"

def verify_token(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts
        expected = _b64url(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64url_decode(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


# ── Password Hashing ──────────────────────────────────

def hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = uuid.uuid4().hex[:16]
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${h}"

def check_password(password: str, stored: str) -> bool:
    if "$" not in stored:
        return password == stored
    salt, h = stored.split("$", 1)
    return hash_password(password, salt).split("$")[1] == h


# ── User Store ────────────────────────────────────────

async def get_user(username: str) -> dict | None:
    r = await get_redis()
    raw = await r.hget(USERS_KEY, username)
    if raw:
        return json.loads(raw)
    return None

async def save_user(user: dict):
    r = await get_redis()
    await r.hset(USERS_KEY, user["username"], json.dumps(user, ensure_ascii=False))

async def delete_user(username: str):
    r = await get_redis()
    await r.hdel(USERS_KEY, username)

async def list_users() -> list[dict]:
    r = await get_redis()
    raw = await r.hgetall(USERS_KEY)
    users = []
    for name, data in sorted(raw.items()):
        u = json.loads(data)
        u.pop("password", None)
        users.append(u)
    return users

async def init_default_admin():
    r = await get_redis()
    exists = await r.hexists(USERS_KEY, AUTH_USER or "admin")
    if not exists:
        username = AUTH_USER or "admin"
        password = AUTH_PASS or "admin"
        await save_user({
            "username": username,
            "password": hash_password(password),
            "role": "admin",
            "display_name": username.capitalize(),
            "avatar": "🦀",
            "created_at": time.time(),
        })
        logger.info(f"Default admin user '{username}' created")


# ── Auth Middleware ────────────────────────────────────

PUBLIC_PATHS = {"/", "/monitor", "/api/auth/login", "/api/health", "/favicon.ico"}

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/auth/login") or path.startswith("/static/"):
            return await call_next(request)

        # n8n service token auth (covers /api/n8n/ and /api/yao/ for n8n workflows)
        if (path.startswith("/api/n8n/") or path.startswith("/api/yao/")) and N8N_SERVICE_TOKEN:
            token = request.headers.get("X-N8n-Token", "")
            if token == N8N_SERVICE_TOKEN:
                request.state.user = {"sub": "n8n", "role": "service"}
                return await call_next(request)

        auth = request.headers.get("Authorization", "")

        if auth.startswith("Bearer "):
            token_data = verify_token(auth[7:])
            if token_data:
                request.state.user = token_data
                return await call_next(request)

        if auth.startswith("Basic ") and AUTH_USER:
            try:
                decoded = base64.b64decode(auth[6:]).decode()
                user, pwd = decoded.split(":", 1)
                if user == AUTH_USER and pwd == AUTH_PASS:
                    request.state.user = {"sub": user, "role": "admin", "name": user}
                    return await call_next(request)
            except Exception:
                pass

        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})


# ── Redis → Orchestrator ──────────────────────────────

LONG_RUNNING_CMDS = {"quant", "quant_optimize", "backtest", "intraday_select", "intraday_monitor", "claude", "cc", "claude_continue", "ccr", "dev"}
LONG_TIMEOUT = 1500

async def _send_and_wait(command: str, args: str = "", uid: str = "webchat_default", user_name: str = "", raw_reply: bool = False):
    """发送命令到 Orchestrator 并等待回复。raw_reply=True 时返回完整 reply dict。"""
    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(reply_channel)
    params = {"command": command, "args": args, "reply_channel": reply_channel, "uid": uid}
    if user_name:
        params["user_name"] = user_name
    msg = json.dumps({
        "id": msg_id, "sender": "webchat", "target": "orchestrator",
        "action": "route",
        "params": params,
        "timestamp": time.time(),
    })
    await r.publish("rragent:orchestrator", msg)
    timeout = LONG_TIMEOUT if command in LONG_RUNNING_CMDS else REPLY_TIMEOUT
    try:
        async def _wait():
            async for raw_msg in pubsub.listen():
                if raw_msg["type"] != "message":
                    continue
                data = json.loads(raw_msg["data"])
                if data.get("type") == "progress":
                    continue
                if raw_reply:
                    return data
                return data.get("text", json.dumps(data, ensure_ascii=False, indent=2))
        return await asyncio.wait_for(_wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if raw_reply:
            return {"text": "⏱️ 超时，Agent 未在规定时间内回复", "error": True}
        return "⏱️ 超时，Agent 未在规定时间内回复"
    except Exception as e:
        if raw_reply:
            return {"text": f"❌ 错误: {e}", "error": True}
        return f"❌ 错误: {e}"
    finally:
        await pubsub.unsubscribe(reply_channel)


async def send_to_orchestrator(command: str, args: str = "", uid: str = "webchat_default", user_name: str = "") -> str:
    return await _send_and_wait(command, args, uid, user_name, raw_reply=False)


VALID_AGENT_TARGETS = {"manager", "orchestrator", "market", "analysis", "news", "strategist", "backtest", "dev", "browser", "desktop"}


async def send_to_agent(target: str, command: str, args: str = "", uid: str = "webchat_default", user_name: str = "") -> dict:
    """发送消息到指定 Agent（manager 走 orchestrator，其它直达），返回 {text, source}"""
    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(reply_channel)

    if target in ("manager", "orchestrator"):
        channel = "rragent:orchestrator"
        action = "route"
        params = {"command": command, "args": args, "reply_channel": reply_channel, "uid": uid}
        if user_name:
            params["user_name"] = user_name
        effective_target = "orchestrator"
    else:
        channel = f"rragent:{target}"
        action = "webchat_ask"
        params = {"question": args, "reply_channel": reply_channel}
        effective_target = target

    msg = json.dumps({
        "id": msg_id, "sender": "webchat", "target": effective_target,
        "action": action, "params": params, "timestamp": time.time(),
    })
    await r.publish(channel, msg)
    try:
        async def _wait():
            async for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                data = json.loads(raw["data"])
                if data.get("type") == "progress":
                    continue
                return {
                    "text": data.get("text", json.dumps(data, ensure_ascii=False, indent=2)),
                    "source": data.get("source", target),
                }
        return await asyncio.wait_for(_wait(), timeout=REPLY_TIMEOUT)
    except asyncio.TimeoutError:
        return {"text": "⏱️ 超时，Agent 未在规定时间内回复", "source": target}
    except Exception as e:
        return {"text": f"❌ 错误: {e}", "source": target}
    finally:
        await pubsub.unsubscribe(reply_channel)


async def stream_agent(target: str, command: str, args: str = "", uid: str = "webchat_default", user_name: str = ""):
    """SSE 流式：先发 thinking，然后发结果，携带 source"""
    yield f"data: {json.dumps({'type': 'thinking', 'content': '', 'source': target})}\n\n"
    try:
        result = await send_to_agent(target, command, args, uid=uid, user_name=user_name)
        text = result["text"]
        source = result.get("source", target)
        chunks = [text[i:i+80] for i in range(0, len(text), 80)]
        for chunk in chunks:
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk, 'source': source})}\n\n"
            await asyncio.sleep(0.02)
        yield f"data: {json.dumps({'type': 'done', 'content': '', 'source': source})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': str(e), 'source': target})}\n\n"


async def stream_orchestrator(command: str, args: str = ""):
    """SSE 流式（兼容旧调用）"""
    async for chunk in stream_agent("manager", command, args):
        yield chunk


# ── FastAPI App ────────────────────────────────────────

app = FastAPI(title="OpenClaw API", docs_url=None, redoc_url=None)
app.add_middleware(AuthMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).parent / "webchat_static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def startup():
    await init_default_admin()


# ── Auth API ──────────────────────────────────────────

def get_current_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "ts": time.time()}


@app.post("/api/auth/login")
async def api_login(request: Request):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        return JSONResponse(status_code=400, content={"ok": False, "msg": "用户名和密码不能为空"})
    user = await get_user(username)
    if not user or not check_password(password, user.get("password", "")):
        return JSONResponse(status_code=401, content={"ok": False, "msg": "用户名或密码错误"})
    token = create_token(username, user["role"], user.get("display_name", ""), user.get("avatar", "🦀"))
    return {"ok": True, "token": token, "user": {
        "username": username, "role": user["role"],
        "display_name": user.get("display_name", username),
        "avatar": user.get("avatar", "🦀"),
    }}


@app.get("/api/auth/me")
async def api_me(request: Request):
    user = get_current_user(request)
    full = await get_user(user["sub"])
    if full:
        full.pop("password", None)
        return full
    return {"username": user["sub"], "role": user.get("role", "user"), "display_name": user.get("name", ""), "avatar": user.get("avatar", "🦀")}


@app.put("/api/auth/profile")
async def api_update_profile(request: Request):
    current = get_current_user(request)
    body = await request.json()
    user = await get_user(current["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if "display_name" in body:
        user["display_name"] = body["display_name"][:32]
    if "avatar" in body and body["avatar"] in AVATARS:
        user["avatar"] = body["avatar"]
    if "password" in body and body["password"]:
        user["password"] = hash_password(body["password"])
    await save_user(user)
    user.pop("password", None)
    token = create_token(user["username"], user["role"], user.get("display_name", ""), user.get("avatar", "🦀"))
    return {"ok": True, "user": user, "token": token}


# ── Admin API ─────────────────────────────────────────

@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    require_admin(request)
    return {"users": await list_users()}


@app.post("/api/admin/users")
async def api_admin_create_user(request: Request):
    require_admin(request)
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    role = body.get("role", "user")
    if not username or not password:
        return JSONResponse(status_code=400, content={"ok": False, "msg": "用户名和密码不能为空"})
    if role not in ("admin", "user", "viewer"):
        return JSONResponse(status_code=400, content={"ok": False, "msg": "角色必须是 admin/user/viewer"})
    existing = await get_user(username)
    if existing:
        return JSONResponse(status_code=409, content={"ok": False, "msg": f"用户 {username} 已存在"})
    import random
    await save_user({
        "username": username,
        "password": hash_password(password),
        "role": role,
        "display_name": body.get("display_name", username),
        "avatar": body.get("avatar", random.choice(AVATARS)),
        "created_at": time.time(),
    })
    return {"ok": True, "msg": f"用户 {username} 已创建"}


@app.put("/api/admin/users/{username}")
async def api_admin_update_user(username: str, request: Request):
    require_admin(request)
    user = await get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    body = await request.json()
    if "role" in body and body["role"] in ("admin", "user", "viewer"):
        user["role"] = body["role"]
    if "display_name" in body:
        user["display_name"] = body["display_name"][:32]
    if "avatar" in body:
        user["avatar"] = body["avatar"]
    if "password" in body and body["password"]:
        user["password"] = hash_password(body["password"])
    await save_user(user)
    return {"ok": True, "msg": f"用户 {username} 已更新"}


@app.delete("/api/admin/users/{username}")
async def api_admin_delete_user(username: str, request: Request):
    admin = require_admin(request)
    if username == admin["sub"]:
        return JSONResponse(status_code=400, content={"ok": False, "msg": "不能删除自己"})
    await delete_user(username)
    return {"ok": True, "msg": f"用户 {username} 已删除"}


@app.get("/api/admin/avatars")
async def api_admin_avatars():
    return {"avatars": AVATARS}


@app.post("/api/admin/uid-alias")
async def api_admin_uid_alias(request: Request):
    """绑定 uid 别名，让多渠道（web/telegram）共享同一记忆。
    body: {"aliases": ["web_admin", "tg_12345"], "canonical": "admin"}
    """
    require_admin(request)
    body = await request.json()
    aliases = body.get("aliases", [])
    canonical = body.get("canonical", "")
    if not canonical or not aliases:
        raise HTTPException(400, "missing canonical or aliases")
    r = await get_redis()
    for alias in aliases:
        await r.hset("rragent:uid_aliases", alias, canonical)
    await r.hset("rragent:uid_aliases", canonical, canonical)
    return {"ok": True, "msg": f"已绑定 {len(aliases)} 个别名 → {canonical}"}


@app.get("/api/admin/uid-alias")
async def api_admin_list_uid_aliases(request: Request):
    require_admin(request)
    r = await get_redis()
    raw = await r.hgetall("rragent:uid_aliases")
    aliases = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        aliases[key] = val
    return {"aliases": aliases}


# ── Overview ──────────────────────────────────────────

def _is_cn_trading_hours() -> bool:
    """判断当前是否处于A股交易时段（北京时间 09:25-15:05，周一至周五）"""
    from datetime import datetime, timezone, timedelta
    now_bj = datetime.now(timezone(timedelta(hours=8)))
    if now_bj.weekday() >= 5:
        return False
    t = now_bj.hour * 60 + now_bj.minute
    return (9 * 60 + 25) <= t <= (15 * 60 + 5)


SCHEDULED_AGENTS = {
    "intraday": "交易时段自动启动",
}


@app.get("/api/overview")
async def api_overview():
    r = await get_redis()
    agents = {}
    trading_hours = _is_cn_trading_hours()
    hb_raw = await r.hgetall("rragent:heartbeats")
    for name, raw in hb_raw.items():
        try:
            hb = json.loads(raw)
            age = time.time() - hb.get("ts", 0)
            if age < 30:
                status = "online"
            elif age < 60:
                status = "slow"
            elif name in SCHEDULED_AGENTS and not trading_hours:
                status = "sleeping"
            else:
                status = "offline"
            agents[name] = {"status": status, "pid": hb.get("pid", 0), "age": round(age),
                            "scheduled": SCHEDULED_AGENTS.get(name, "")}
        except Exception:
            agents[name] = {"status": "error", "pid": 0, "age": -1, "scheduled": ""}

    channels = {}
    ch_raw = await r.hgetall("rragent:channel_heartbeats")
    for name, raw in ch_raw.items():
        try:
            hb = json.loads(raw)
            age = time.time() - hb.get("ts", 0)
            channels[name] = {"status": "online" if age < 30 else ("slow" if age < 60 else "offline"), "age": round(age), "fails": hb.get("consecutive_failures", 0), "mode": hb.get("mode", "")}
        except Exception:
            channels[name] = {"status": "error", "age": -1, "fails": 0, "mode": ""}

    return {"agents": agents, "channels": channels, "ts": time.time()}


@app.get("/api/llm/config")
async def api_llm_config():
    """获取 LLM provider/model 全量配置"""
    try:
        from agents.llm_router import CLOUD_PROVIDERS, TASK_PROFILES, MODEL_META, get_llm_router
        router = get_llm_router()
        pref = router.get_preference()
        providers = {}
        for name, cfg in CLOUD_PROVIDERS.items():
            providers[name] = {
                "has_key": bool(cfg.get("api_key")),
                "base_url": cfg.get("base_url", ""),
                "models": list(cfg.get("models", {}).keys()),
            }
        return {
            "current": pref,
            "providers": providers,
            "model_meta": MODEL_META,
            "task_profiles": {k: {"desc": v["description"], "chain": [(p, m) for p, m in v["preferred_chain"]]} for k, v in TASK_PROFILES.items()},
        }
    except Exception as e:
        return {"error": str(e), "current": None, "providers": {}, "model_meta": {}, "task_profiles": {}}


@app.post("/api/llm/config")
async def api_llm_set(request: Request):
    """设置 LLM 首选 provider/model"""
    body = await request.json()
    provider = body.get("provider", "")
    model = body.get("model", "")
    try:
        from agents.llm_router import get_llm_router
        router = get_llm_router()
        if not provider:
            router.clear_preference()
            return {"ok": True, "msg": "已切换为自动路由"}
        ok = router.set_preference(provider, model)
        if ok:
            return {"ok": True, "msg": f"已切换到 {provider}/{model}"}
        return {"ok": False, "msg": f"无效的 provider/model: {provider}/{model}"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.get("/api/market/overview")
async def api_market_overview():
    """结构化行情总览：涨停 + 连板 + 板块 + 热股 + 大盘指数，一次全返回"""
    from agents.data_sources import api_get_with_fallback
    from agents.market_time import get_analysis_context

    ctx = get_analysis_context()

    limitup = api_get_with_fallback("limitup", {"ordering": "-pct_chg", "page_size": 30}) or {}
    limitstep = api_get_with_fallback("limitstep", {"ordering": "-limit_times", "page_size": 20}) or {}
    concepts = api_get_with_fallback("concept-boards", {"ordering": "-pct_chg", "page_size": 20}) or {}
    hot = api_get_with_fallback("ths-hot", {"market": "热股", "ordering": "rank", "page_size": 20}) or {}
    indices = api_get_with_fallback("indices", {"page_size": 10}) or {}

    zt_list = limitup.get("results", [])
    lb_list = limitstep.get("results", [])
    bk_list = concepts.get("results", [])
    hot_list = hot.get("results", [])
    idx_list = indices.get("results", [])

    zt_count = limitup.get("count", len(zt_list))
    lb_max = max((r.get("limit_times", 1) for r in lb_list), default=0)

    up_industries = {}
    for r in zt_list:
        ind = r.get("industry", "")
        if ind:
            up_industries[ind] = up_industries.get(ind, 0) + 1
    top_industries = sorted(up_industries.items(), key=lambda x: -x[1])[:8]

    return {
        "time_context": ctx,
        "stats": {
            "zt_count": zt_count,
            "lb_max_height": lb_max,
            "top_industries": top_industries,
            "concept_count": len(bk_list),
        },
        "limitup": zt_list[:30],
        "limitstep": lb_list[:20],
        "concepts": bk_list[:20],
        "hot": hot_list[:20],
        "indices": idx_list[:10],
    }


@app.post("/api/command")
async def api_command(request: Request):
    body = await request.json()
    cmd = body.get("cmd", "")
    args = body.get("args", "")
    if not cmd:
        raise HTTPException(400, "missing cmd")
    user = getattr(request.state, "user", None)
    uid = f"web_{user['sub']}" if user else "webchat_default"
    result = await send_to_orchestrator(cmd, args, uid=uid)
    await save_chat_message("command", f"/{cmd} {args}".strip(), view="system")
    await save_chat_message("result", result[:4000] if result else "", view="system")
    return {"result": result}


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    msg = body.get("message", "").strip()
    target = body.get("target", "manager").strip()
    if not msg:
        raise HTTPException(400, "missing message")
    if target not in VALID_AGENT_TARGETS:
        target = "manager"

    user = getattr(request.state, "user", None)
    uid = f"web_{user['sub']}" if user else "webchat_default"
    user_name = user.get("name", user.get("sub", "")) if user else ""

    if target in ("manager", "orchestrator"):
        if msg.startswith("/"):
            parts = msg[1:].split(None, 1)
            cmd = parts[0] if parts else ""
            args = parts[1] if len(parts) > 1 else ""
        else:
            cmd, args = "chat", msg
    else:
        cmd, args = "chat", msg

    await save_chat_message("user", msg, target=target)

    async def _stream_and_save():
        full_content = ""
        source = target
        async for chunk in stream_agent(target, cmd, args, uid=uid, user_name=user_name):
            full_content += chunk
            yield chunk
        text = ""
        for line in full_content.split("\n"):
            if line.startswith("data: "):
                try:
                    d = json.loads(line[6:])
                    if d.get("type") in ("chunk", "done"):
                        text += d.get("content", "")
                    if d.get("source"):
                        source = d["source"]
                except Exception:
                    pass
        if text:
            await save_chat_message("assistant", text, target=source)

    return StreamingResponse(
        _stream_and_save(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/tasks")
async def api_tasks():
    """列出所有任务（直接读 Redis，不经过 Orchestrator）"""
    try:
        from agents.task_manager import TaskManager, PRESET_TASKS
        r = await get_redis()
        mgr = TaskManager(r)
        tasks = await mgr.list_tasks(limit=30)
        return {
            "tasks": [t.to_dict() for t in tasks],
            "presets": {k: {"name": v["name"], "steps": len(v["steps"])} for k, v in PRESET_TASKS.items()},
        }
    except Exception as e:
        return {"tasks": [], "presets": {}, "error": str(e)}


@app.post("/api/tasks/create")
async def api_task_create(request: Request):
    body = await request.json()
    preset = body.get("preset", "")
    name = body.get("name", "")
    steps = body.get("steps", [])
    args = json.dumps({"preset": preset, "name": name, "steps": steps})
    result = await send_to_orchestrator("task_new", args)
    return {"result": result}


@app.post("/api/tasks/{task_id}/cancel")
async def api_task_cancel(task_id: str):
    result = await send_to_orchestrator("task_cancel", task_id)
    return {"result": result}


@app.get("/api/tasks/{task_id}")
async def api_task_detail(task_id: str):
    """获取任务详情（直接读 Redis）"""
    try:
        from agents.task_manager import TaskManager
        r = await get_redis()
        mgr = TaskManager(r)
        task = await mgr.get_task(task_id)
        if task:
            return {"task": task.to_dict()}
        return {"task": None, "error": "not found"}
    except Exception as e:
        return {"task": None, "error": str(e)}


@app.get("/api/tasks/{task_id}/progress")
async def api_task_progress(task_id: str):
    """SSE 端点: 实时推送任务执行进度"""
    async def _stream():
        r = await get_redis()
        pubsub = r.pubsub()
        channel = f"rragent:task_progress:{task_id}"
        await pubsub.subscribe(channel)
        try:
            yield f"data: {json.dumps({'event': 'connected', 'task_id': task_id})}\n\n"
            async for raw_msg in pubsub.listen():
                if raw_msg["type"] != "message":
                    continue
                data = raw_msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                yield f"data: {data}\n\n"
                try:
                    parsed = json.loads(data)
                    if parsed.get("status") in ("completed", "failed", "cancelled"):
                        yield f"data: {json.dumps({'event': 'done'})}\n\n"
                        break
                except Exception:
                    pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── Strategy Library ──────────────────────────────────

@app.post("/api/quant/optimize")
async def api_quant_optimize(request: Request):
    """基于已有策略模板 / preset 启动优化研究"""
    body = await request.json()
    topic = body.get("topic", "")
    base_title = body.get("base_title", "")

    if not topic:
        raise HTTPException(400, "missing topic")

    optimize_payload = {"topic": topic, "base_title": base_title}

    if body.get("base_preset"):
        optimize_payload["base_preset"] = body["base_preset"]
    else:
        optimize_payload["base_code"] = body.get("base_code", "")
        optimize_payload["base_metrics"] = body.get("base_metrics", {})

    optimize_args = json.dumps(optimize_payload, ensure_ascii=False)
    result = await send_to_orchestrator("quant_optimize", optimize_args)
    return {"result": result}


# ── Quant Stream (SSE) ────────────────────────────────

@app.post("/api/quant/stream")
async def api_quant_stream(request: Request):
    """SSE 流式量化研发 — 实时推送每一步进度"""
    body = await request.json()
    cmd = body.get("cmd", "quant")
    args = body.get("args", "")
    topic = body.get("topic", args).strip()

    if not topic and cmd == "quant":
        raise HTTPException(400, "missing topic")

    run_id = uuid.uuid4().hex[:12]
    progress_channel = f"rragent:quant_progress:{run_id}"

    backtest_mode = body.get("mode", "vectorbt")

    if cmd == "quant_optimize":
        optimize_payload = body.get("optimize_payload", {})
        if not optimize_payload.get("topic"):
            optimize_payload["topic"] = topic
        optimize_payload["mode"] = backtest_mode
        command_args = json.dumps(optimize_payload, ensure_ascii=False)
    else:
        command_args = json.dumps({"topic": topic, "mode": backtest_mode}, ensure_ascii=False)

    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"

    async def event_stream():
        r = await get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(progress_channel, reply_channel)

        msg = json.dumps({
            "id": msg_id, "sender": "webchat", "target": "orchestrator",
            "action": "route",
            "params": {
                "command": cmd, "args": command_args,
                "reply_channel": reply_channel,
                "progress_channel": progress_channel,
            },
            "timestamp": time.time(),
        })
        await r.publish("rragent:orchestrator", msg)

        yield f"data: {json.dumps({'type': 'started', 'run_id': run_id, 'topic': topic})}\n\n"

        try:
            deadline = time.time() + LONG_TIMEOUT
            while time.time() < deadline:
                raw = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0), timeout=5.0)
                if raw is None:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue
                if raw["type"] != "message":
                    continue
                channel = raw["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = json.loads(raw["data"])

                if channel == progress_channel:
                    yield f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                    if data.get("type") == "done":
                        break
                elif channel == reply_channel:
                    yield f"data: {json.dumps({'type': 'final', 'text': data.get('text', '')}, ensure_ascii=False, default=str)}\n\n"
                    break
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'content': '超时'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        finally:
            await pubsub.unsubscribe(progress_channel, reply_channel)
            yield "data: {\"type\":\"close\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/quant/records")
async def api_quant_records():
    """获取历史研发记录列表（摘要级，不含 trade_log 大对象）"""
    r = await get_redis()
    raw_list = await r.lrange("rragent:quant_records", 0, 99)
    records = []
    for raw in raw_list:
        try:
            rec = json.loads(raw)
            full_m = rec.get("metrics", {})
            summary_m = {k: v for k, v in full_m.items() if k != "trade_log"}
            records.append({
                "id": rec.get("id", ""),
                "title": rec.get("title", ""),
                "topic": rec.get("topic", ""),
                "status": rec.get("status", ""),
                "metrics": summary_m,
                "attempts": rec.get("attempts", 0),
                "created_at": rec.get("created_at", ""),
                "mode": rec.get("mode", ""),
                "type": "research",
                "has_code": bool(rec.get("code")),
            })
        except Exception:
            continue
    return {"records": records}


@app.get("/api/quant/records/{record_id}")
async def api_quant_record_detail(record_id: str):
    """获取研发记录完整详情（含代码/过程日志/指标）"""
    r = await get_redis()
    raw_list = await r.lrange("rragent:quant_records", 0, 99)
    for raw in raw_list:
        try:
            rec = json.loads(raw)
            if rec.get("id") == record_id:
                return rec
        except Exception:
            continue
    raise HTTPException(404, "record not found")


# ── Alpha Digger API ──────────────────────────────────

@app.get("/api/digger/status")
async def api_digger_status(request: Request):
    """因子库状态"""
    user = getattr(request.state, "user", None)
    uid = f"web_{user['sub']}" if user else "webchat_default"
    result = await send_to_orchestrator("digger_status", "", uid=uid)
    return {"status": "ok", "text": result}


@app.post("/api/digger/start")
async def api_digger_start(request: Request):
    """SSE 流式因子挖掘"""
    body = await request.json()
    rounds = body.get("rounds", 5)
    factors = body.get("factors", 5)
    interval = body.get("interval", 30)

    args = json.dumps({"rounds": rounds, "factors": factors, "interval": interval})
    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"
    progress_channel = f"rragent:digger_progress:{msg_id}"

    async def event_stream():
        r = await get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(progress_channel, reply_channel)

        user = getattr(request.state, "user", None)
        uid = f"web_{user['sub']}" if user else "webchat_default"

        msg = json.dumps({
            "id": msg_id, "sender": "webchat", "target": "orchestrator",
            "action": "route",
            "params": {
                "command": "digger", "args": args,
                "reply_channel": reply_channel,
                "progress_channel": progress_channel,
                "uid": uid,
            },
            "timestamp": time.time(),
        })
        await r.publish("rragent:orchestrator", msg)

        yield f"data: {json.dumps({'type': 'started', 'rounds': rounds, 'factors': factors})}\n\n"

        try:
            deadline = time.time() + LONG_TIMEOUT
            while time.time() < deadline:
                raw = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0), timeout=5.0)
                if raw is None:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue
                if raw["type"] != "message":
                    continue
                channel = raw.get("channel", "")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = json.loads(raw["data"])

                if channel == progress_channel:
                    evt_type = data.get("type", "progress")
                    text = data.get("text", "")
                    yield f"data: {json.dumps({'type': evt_type, 'text': text}, ensure_ascii=False)}\n\n"
                    if evt_type == "done":
                        break
                elif channel == reply_channel:
                    text = data.get("text", "")
                    yield f"data: {json.dumps({'type': 'done', 'text': text}, ensure_ascii=False)}\n\n"
                    break
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'text': '超时'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
        finally:
            await pubsub.unsubscribe(progress_channel, reply_channel)
            yield "data: {\"type\":\"close\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/digger/factors")
async def api_digger_factors(request: Request):
    """因子列表（全量，含代码、指标和复杂度标签）"""
    from agents.factor_library import FactorLibrary
    lib = FactorLibrary(redis_client=await get_redis())
    all_factors = await lib.get_all_factors(status="")
    items = []
    for f in all_factors:
        d = f.to_dict()
        d.pop("oos_sharpe_history", None)
        code = d.get("code", "")
        has_nested = "for i in range" in code and "for j in range" in code
        has_apply = ".apply(" in code
        d["complexity"] = "nested" if has_nested else ("apply" if has_apply else "vectorized")
        d["code_lines"] = code.count("\n") + 1 if code else 0
        d["combinable"] = not has_nested
        items.append(d)
    items.sort(key=lambda x: x.get("sharpe", 0), reverse=True)
    stats = await lib.get_stats()
    # Add complexity distribution to stats
    stats["complexity_dist"] = {
        "vectorized": sum(1 for x in items if x["complexity"] == "vectorized"),
        "apply": sum(1 for x in items if x["complexity"] == "apply"),
        "nested": sum(1 for x in items if x["complexity"] == "nested"),
    }
    return {"factors": items, "stats": stats}


@app.post("/api/digger/retire")
async def api_digger_retire(request: Request):
    """退休指定因子"""
    body = await request.json()
    factor_id = body.get("factor_id", "")
    if not factor_id:
        raise HTTPException(400, "factor_id required")
    from agents.factor_library import FactorLibrary
    lib = FactorLibrary(redis_client=await get_redis())
    ok = await lib.retire_factor(factor_id)
    return {"ok": ok, "factor_id": factor_id}


@app.post("/api/digger/combine")
async def api_digger_combine(request: Request):
    """因子融合：组合多个因子 → 回测 → 评估 → 自动回退或采纳，全程记录"""
    body = await request.json()
    factor_ids = body.get("factor_ids", [])

    from agents.factor_library import FactorLibrary
    lib = FactorLibrary(redis_client=await get_redis())

    if factor_ids:
        all_factors = await lib.get_all_factors(status="active")
        candidates = [f for f in all_factors if f.id in factor_ids]
    else:
        candidates = await lib.get_combine_candidates()

    if len(candidates) < 2:
        return {"ok": False, "error": f"需要至少 2 个因子，当前 {len(candidates)} 个"}

    max_combine = body.get("max_factors", 5)
    top_n = candidates[:max_combine]
    input_factors_info = []
    codes, summaries = [], []
    for i, f in enumerate(top_n):
        renamed = f.code.replace("def generate_factor(", f"def _factor_{i+1}(")
        codes.append(f"# --- factor {i+1}: {f.sub_theme or f.theme} (sharpe={f.sharpe:.3f}, ir={f.ir:.3f}) ---\n{renamed}")
        summaries.append(f"{f.id}: {f.sub_theme or f.theme} sharpe={f.sharpe:.3f} ir={f.ir:.3f}")
        input_factors_info.append({
            "id": f.id, "theme": f.sub_theme or f.theme,
            "sharpe": f.sharpe, "ir": f.ir, "ic_mean": f.ic_mean,
            "win_rate": f.win_rate, "trades": f.trades, "max_drawdown": f.max_drawdown,
        })

    combiner = (
        "\n\nimport numpy as np\nimport pandas as pd\n\n"
        "def generate_factor(matrices):\n"
        "    factors = []\n"
    )
    for i in range(len(top_n)):
        combiner += f"    try:\n        factors.append(_factor_{i+1}(matrices))\n    except Exception:\n        pass\n"
    combiner += (
        "    if not factors:\n"
        "        return pd.DataFrame(0, index=matrices['close'].index, columns=matrices['close'].columns)\n"
        "    stacked = np.stack([f.values for f in factors], axis=0)\n"
        "    combined = np.nanmean(stacked, axis=0)\n"
        "    return pd.DataFrame(combined, index=matrices['close'].index, columns=matrices['close'].columns)\n"
    )
    combined_code = "\n\n".join(codes) + combiner

    # Direct bridge call — bypass quant_pipeline LLM loop
    # The old path (_send_and_wait → orchestrator → quant_pipeline) failed because:
    #   1) _extract_generate_factor regex strips _factor_N helper functions
    #   2) 10 complex factors in LLM optimization loop always times out
    from agents.bridge_client import get_bridge_client
    from datetime import date, timedelta
    bridge = get_bridge_client()
    start_date = (date.today() - timedelta(days=180)).isoformat()
    end_date = date.today().isoformat()

    combined_metrics = {}
    result_text = ""
    try:
        resp = await bridge.run_factor_mining(
            factor_code=combined_code,
            start_date=start_date,
            end_date=end_date,
        )
        if resp.get("status") == "error":
            result_text = f"沙箱执行失败: {resp.get('error', '未知错误')}"
        else:
            combined_metrics = resp.get("metrics") or {}
            result_text = json.dumps(resp, ensure_ascii=False, indent=2)[:3000]
    except Exception as e:
        result_text = f"Bridge 调用失败: {e}"

    evaluation = lib.evaluate_combine_quality(input_factors_info, combined_metrics)
    verdict = evaluation["verdict"]

    record = {
        "input_factors": input_factors_info,
        "input_factor_ids": [f.id for f in top_n],
        "combined_code_preview": combined_code[:2000],
        "combined_metrics": combined_metrics,
        "evaluation": evaluation,
        "verdict": verdict,
        "result_raw": result_text[:3000] if isinstance(result_text, str) else "",
        "status": "accepted" if verdict == "accept" else "rejected" if verdict == "reject" else "marginal",
    }
    record_id = await lib.save_combine_record(record)

    return {
        "ok": True,
        "record_id": record_id,
        "factors_used": len(top_n),
        "summaries": summaries,
        "verdict": verdict,
        "evaluation": evaluation,
        "combined_metrics": combined_metrics,
        "result": result_text,
    }


@app.get("/api/digger/combine/history")
async def api_digger_combine_history(limit: int = 20):
    """获取融合历史记录"""
    from agents.factor_library import FactorLibrary
    lib = FactorLibrary(redis_client=await get_redis())
    records = await lib.get_combine_records(limit=limit)
    for rec in records:
        rec.pop("combined_code_preview", None)
        rec.pop("result_raw", None)
    return {"records": records}


@app.get("/api/digger/combine/{record_id}")
async def api_digger_combine_detail(record_id: str):
    """获取单条融合记录详情"""
    from agents.factor_library import FactorLibrary
    lib = FactorLibrary(redis_client=await get_redis())
    rec = await lib.get_combine_record(record_id)
    if not rec:
        raise HTTPException(404, "record not found")
    return rec


STRATEGY_REDIS_KEY = "rragent:strategies"

@app.post("/api/digger/to-strategy")
async def api_digger_to_strategy(request: Request):
    """将因子策略化: factor → signals → run_alpha 回测 → 存入策略库"""
    body = await request.json()
    factor_id = body.get("factor_id", "")
    if not factor_id:
        raise HTTPException(400, "factor_id required")

    from agents.factor_library import FactorLibrary
    from agents.bridge_client import get_bridge_client
    from datetime import date, timedelta
    lib = FactorLibrary(redis_client=await get_redis())
    bridge = get_bridge_client()

    all_factors = await lib.get_all_factors(status="")
    factor = next((f for f in all_factors if f.id == factor_id), None)
    if not factor:
        raise HTTPException(404, f"factor {factor_id} not found")

    entry_pct = body.get("entry_pct", 0.95)
    exit_pct = body.get("exit_pct", 0.70)
    strategy_code = factor.code + f"""

def generate_signals(matrices):
    \"\"\"基于因子排名的多空信号生成\"\"\"
    factor = generate_factor(matrices)
    close = matrices['close']
    rank_pct = factor.rank(axis=1, pct=True)
    factor_rising = factor > factor.shift(1)
    entries = (rank_pct > {entry_pct}) & factor_rising
    ma5 = close.rolling(5).mean()
    exits = (rank_pct < {exit_pct}) | (close < ma5)
    entries = entries.fillna(False)
    exits = exits.fillna(False)
    return entries, exits
"""
    start_date = (date.today() - timedelta(days=180)).isoformat()
    end_date = date.today().isoformat()

    try:
        resp = await bridge.run_alpha(
            alpha_code=strategy_code,
            start_date=start_date,
            end_date=end_date,
            mode="technical",
        )
    except Exception as e:
        return {"ok": False, "error": f"回测失败: {e}", "factor_id": factor_id}

    metrics = resp.get("metrics", {})
    status_str = resp.get("status", "error")
    ok = status_str != "error"

    # 存入策略库
    strat_id = ""
    if ok:
        r = await get_redis()
        strat_id = f"strat_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        theme = factor.sub_theme or factor.theme
        strat_record = {
            "id": strat_id,
            "title": f"[{theme}] Sharpe={factor.sharpe:.2f}",
            "description": f"基于因子 {factor_id} ({theme}) 自动生成的策略",
            "source": "factor",
            "factor_id": factor_id,
            "factor_theme": theme,
            "factor_sharpe": factor.sharpe,
            "code": strategy_code,
            "metrics": metrics,
            "params": {"entry_pct": entry_pct, "exit_pct": exit_pct},
            "status": "draft",
            "synced_to_139": False,
            "created_at": time.time(),
        }
        await r.hset(STRATEGY_REDIS_KEY, strat_id, json.dumps(strat_record, ensure_ascii=False, default=str))

    return {
        "ok": ok,
        "strategy_id": strat_id if ok else "",
        "factor_id": factor_id,
        "factor_theme": factor.sub_theme or factor.theme,
        "factor_sharpe": factor.sharpe,
        "strategy_code": strategy_code,
        "strategy_metrics": metrics,
        "error": resp.get("error", "") if not ok else "",
        "params": {"entry_pct": entry_pct, "exit_pct": exit_pct},
    }


@app.get("/api/strategies")
async def api_strategies_list():
    """策略库列表 — 合并因子策略 + Bridge presets"""
    r = await get_redis()
    raw = await r.hgetall(STRATEGY_REDIS_KEY)
    strats = []
    for k, v in raw.items():
        try:
            s = json.loads(v)
            strats.append(s)
        except Exception:
            pass
    strats.sort(key=lambda x: x.get("created_at", 0), reverse=True)

    # 也拉取 Bridge presets 作为预设策略
    presets = []
    try:
        from agents.bridge_client import get_bridge_client
        bridge = get_bridge_client()
        resp = await bridge.get_presets()
        for p in resp.get("presets", []):
            presets.append({
                "id": f"preset_{p.get('slug', p.get('id', ''))}",
                "title": p.get("name", ""),
                "description": p.get("description", ""),
                "source": "preset",
                "category": p.get("category", ""),
                "status": "active",
                "code": json.dumps(p.get("payload", {}), indent=2, ensure_ascii=False) if p.get("payload") else "",
                "metrics": {},
                "synced_to_139": True,
                "created_at": p.get("created_at", ""),
            })
    except Exception:
        pass

    return {"strategies": strats + presets, "count": len(strats) + len(presets), "factor_strategies": len(strats)}


@app.get("/api/strategies/{strategy_id}")
async def api_strategy_detail(strategy_id: str):
    """单个策略详情"""
    r = await get_redis()
    raw = await r.hget(STRATEGY_REDIS_KEY, strategy_id)
    if raw:
        return json.loads(raw)
    # 尝试 preset
    if strategy_id.startswith("preset_"):
        try:
            from agents.bridge_client import get_bridge_client
            bridge = get_bridge_client()
            resp = await bridge.get_presets()
            slug = strategy_id[7:]
            for p in resp.get("presets", []):
                if p.get("slug") == slug or str(p.get("id")) == slug:
                    return {
                        "id": strategy_id, "title": p.get("name", ""),
                        "description": p.get("description", ""),
                        "source": "preset", "category": p.get("category", ""),
                        "code": json.dumps(p.get("payload", {}), indent=2, ensure_ascii=False),
                        "metrics": {}, "status": "active", "synced_to_139": True,
                    }
        except Exception:
            pass
    raise HTTPException(404, "strategy not found")


@app.post("/api/strategies/{strategy_id}/sync")
async def api_strategy_sync(strategy_id: str):
    """将策略代码同步部署到 192.168.1.139:/opt/quant_sandbox/strategies/"""
    r = await get_redis()
    raw = await r.hget(STRATEGY_REDIS_KEY, strategy_id)
    if not raw:
        raise HTTPException(404, "strategy not found")
    strat = json.loads(raw)
    code = strat.get("code", "")
    if not code:
        raise HTTPException(400, "strategy has no code")

    filename = f"{strategy_id}.py"
    remote_path = f"/opt/quant_sandbox/strategies/{filename}"

    # 头部注释
    header = f'''"""
OpenClaw 自动生成策略
ID: {strategy_id}
来源: {strat.get("source", "factor")}
因子: {strat.get("factor_id", "N/A")}
主题: {strat.get("factor_theme", "")}
生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}
"""
'''
    full_code = header + code

    from agents.bridge_client import get_bridge_client
    bridge = get_bridge_client()
    errors = []

    # 1) 写文件到沙箱目录
    try:
        resp = await bridge._post("/strategy/deploy/", {
            "strategy_id": strategy_id,
            "code": full_code,
            "filename": filename,
        })
        if not resp.get("ok"):
            errors.append(f"文件部署失败: {resp.get('detail', resp)}")
        else:
            remote_path = resp.get("path", remote_path)
    except Exception as e:
        errors.append(f"文件部署异常: {e}")

    # 2) 写入 ReachRich 数据库 (stocks_aistrategyledger) 让前端可见
    ledger_id = None
    try:
        theme = strat.get("factor_theme", "")
        metrics = strat.get("metrics", {})
        save_resp = await bridge._post("/strategy/save/", {
            "title": strat.get("title", f"[OpenClaw] {theme}"),
            "topic": f"factor:{strat.get('factor_id', '')} | {theme}",
            "status": "APPROVE",
            "attempts": 1,
            "strategy_code": full_code,
            "backtest_metrics": metrics if isinstance(metrics, dict) else {},
            "risk_review": f"Sharpe={metrics.get('sharpe_ratio', 0):.2f}, MaxDD={metrics.get('max_drawdown_pct', 0):.1f}%",
            "decision_report": f"因子 {strat.get('factor_id','')} ({theme}) 自动策略化, Sharpe={strat.get('factor_sharpe', 0):.2f}",
            "rounds_data": [],
            "model_used": "openclaw-factor-pipeline",
        })
        ledger_id = save_resp.get("id")
    except Exception as e:
        errors.append(f"数据库写入异常: {e}")

    if errors and not ledger_id:
        return {"ok": False, "error": "; ".join(errors)}

    # 3) 注册到 screener presets (stocks_strategypreset) 让 ReachRich 选股器可调用
    preset_slug = None
    if ledger_id:
        try:
            slug = f"factor-{strategy_id.replace('strat_', '')}"
            theme = strat.get("factor_theme", "")
            preset_payload = {
                "version": "1.0",
                "execution_mode": "factor_code",
                "meta": {
                    "name": strat.get("title", f"[因子] {theme}"),
                    "owner": "openclaw",
                    "trade_date": "auto",
                },
                "universe": {"mode": "all", "exclude": ["*ST", "ST"]},
                "timeframes": [{"id": "D1", "calendar": "trading", "lookback_bars": 60}],
                "filters": {},
                "outputs": {
                    "limit": 50,
                    "fields": ["ts_code", "factor_score"],
                    "order_by": [{"factor": "factor_score", "direction": "desc"}],
                },
                "code": code,
                "backtest_metrics": strat.get("metrics", {}),
            }
            preset_resp = await bridge._post("/strategy/register-preset/", {
                "slug": slug,
                "name": strat.get("title", f"[因子] {theme}"),
                "description": f"因子策略选股: {theme} (Sharpe={strat.get('factor_sharpe', 0):.2f})",
                "category": "factor",
                "payload": preset_payload,
                "ledger_id": ledger_id,
            })
            preset_slug = preset_resp.get("slug", slug)
        except Exception as e:
            errors.append(f"Screener preset注册: {e}")

    # 更新状态
    strat["synced_to_139"] = True
    strat["sync_time"] = time.time()
    strat["remote_path"] = remote_path
    strat["status"] = "deployed"
    if ledger_id:
        strat["ledger_id"] = ledger_id
    if preset_slug:
        strat["preset_slug"] = preset_slug
    await r.hset(STRATEGY_REDIS_KEY, strategy_id, json.dumps(strat, ensure_ascii=False, default=str))

    return {
        "ok": True, "strategy_id": strategy_id,
        "remote_path": remote_path, "filename": filename,
        "ledger_id": ledger_id,
        "preset_slug": preset_slug,
        "warnings": errors if errors else None,
    }


@app.delete("/api/strategies/{strategy_id}")
async def api_strategy_delete(strategy_id: str):
    """删除策略"""
    r = await get_redis()
    deleted = await r.hdel(STRATEGY_REDIS_KEY, strategy_id)
    return {"ok": deleted > 0, "strategy_id": strategy_id}


@app.post("/api/strategies/{strategy_id}/screen")
async def api_strategy_screen(strategy_id: str, request: Request):
    """运行策略选股 — 返回最新交易日入场信号的股票"""
    r = await get_redis()
    raw = await r.hget(STRATEGY_REDIS_KEY, strategy_id)
    if not raw:
        raise HTTPException(404, "strategy not found")
    strat = json.loads(raw)
    code = strat.get("code", "")
    if not code:
        raise HTTPException(400, "strategy has no code")

    try:
        body = await request.json()
    except Exception:
        body = {}
    top_n = body.get("top_n", 50)

    try:
        from agents.bridge_client import get_bridge_client
        bridge = get_bridge_client()
        strat_name = strat.get("title") or strat.get("name") or strategy_id
        factor_payload = {
            "version": "1.0",
            "execution_mode": "factor_code",
            "meta": {
                "name": strat_name,
                "owner": "rrclaw-strategy",
                "trade_date": "auto",
            },
            "universe": {"mode": "all", "exclude": ["*ST", "ST"]},
            "timeframes": [{"id": "D1", "calendar": "trading", "lookback_bars": 60}],
            "filters": {},
            "outputs": {
                "limit": top_n,
                "fields": ["ts_code", "factor_score"],
                "order_by": [{"factor": "factor_score", "direction": "desc"}],
            },
            "code": code,
        }
        resp = await bridge._post("/screener/", {
            "payload": factor_payload,
            "limit": top_n,
        })
        # Normalize screener response: bridge returns {count, results, columns, trade_date}
        # Frontend expects: {status, stocks, count, signal_date/trade_date}
        if "results" in resp and "stocks" not in resp:
            resp["stocks"] = resp.pop("results")
        if "status" not in resp:
            resp["status"] = "success" if resp.get("count", 0) >= 0 else "error"
        if "signal_date" not in resp:
            resp["signal_date"] = resp.get("trade_date", "")
        return resp
    except Exception as e:
        return {"status": "error", "error": f"选股失败: {e}", "stocks": [], "count": 0}


@app.post("/api/strategies/{strategy_id}/backtest")
async def api_strategy_backtest(strategy_id: str, request: Request):
    """对策略执行回测"""
    r = await get_redis()
    raw = await r.hget(STRATEGY_REDIS_KEY, strategy_id)
    if not raw:
        raise HTTPException(404, "strategy not found")
    strat = json.loads(raw)
    code = strat.get("code", "")
    if not code:
        raise HTTPException(400, "strategy has no code")

    try:
        body = await request.json()
    except Exception:
        body = {}
    from agents.bridge_client import get_bridge_client
    from datetime import date, timedelta
    bridge = get_bridge_client()
    start_date = body.get("start_date", (date.today() - timedelta(days=180)).isoformat())
    end_date = body.get("end_date", date.today().isoformat())

    try:
        resp = await bridge.run_alpha(
            alpha_code=code,
            start_date=start_date,
            end_date=end_date,
            mode="technical",
        )
    except Exception as e:
        return {"ok": False, "error": f"回测失败: {e}"}

    metrics = resp.get("metrics", {})
    ok = resp.get("status", "error") != "error"

    if ok:
        strat["metrics"] = metrics
        strat["last_backtest"] = time.time()
        await r.hset(STRATEGY_REDIS_KEY, strategy_id, json.dumps(strat, ensure_ascii=False, default=str))

    return {"ok": ok, "strategy_id": strategy_id, "metrics": metrics, "error": resp.get("error", "") if not ok else ""}


@app.post("/api/digger/analyze")
async def api_digger_analyze(request: Request):
    """因子库健康分析: 过拟合检测 + 降维聚类 + 多样性报告"""
    from agents.factor_library import FactorLibrary
    import math
    lib = FactorLibrary(redis_client=await get_redis())
    all_factors = await lib.get_all_factors(status="active")

    # Overfitting tiers
    tier1, tier2, tier3, tier4 = [], [], [], []
    for f in all_factors:
        s, wr, dd, t = f.sharpe or 0, f.win_rate or 0, f.max_drawdown or 0, f.trades or 0
        code = f.code or ""
        cplx = "nested" if ("for i in range" in code and "for j in range" in code) else ("apply" if ".apply(" in code else "vectorized")
        finfo = {"id": f.id, "theme": f.theme, "sub_theme": f.sub_theme, "sharpe": s, "ir": f.ir or 0, "ic_mean": f.ic_mean or 0, "win_rate": wr, "trades": t, "max_drawdown": dd, "complexity": cplx}
        if s > 10 and wr >= 0.99 and dd <= 0.001:
            tier1.append(finfo)
        elif s > 10 or wr >= 0.95:
            tier2.append(finfo)
        elif 0.5 <= s <= 5 and wr < 0.7 and t > 500:
            tier3.append(finfo)
        else:
            tier4.append(finfo)

    # Theme distribution for T3
    from collections import Counter
    theme_dist = Counter(f["theme"] for f in tier3)

    # Clustering T3
    combinable = [f for f in tier3 if f["complexity"] != "nested"]
    s_max = max((f["sharpe"] for f in combinable), default=1) or 1
    ir_max = max((abs(f["ir"]) for f in combinable), default=1) or 1

    def nvec(f):
        return [f["sharpe"]/s_max, abs(f["ir"])/ir_max, f["win_rate"], f["trades"]/max(max((x["trades"] for x in combinable), default=1), 1)]

    clusters = []
    used = set()
    for i, fi in enumerate(combinable):
        if fi["id"] in used: continue
        cl = [fi]
        vi = nvec(fi)
        for j, fj in enumerate(combinable):
            if j <= i or fj["id"] in used: continue
            vj = nvec(fj)
            d = math.sqrt(sum((a-b)**2 for a,b in zip(vi, vj)))
            if d < 0.05:
                cl.append(fj); used.add(fj["id"])
        used.add(fi["id"]); clusters.append(cl)

    cluster_reps = []
    for cl in sorted(clusters, key=lambda c: max(f["sharpe"] for f in c), reverse=True):
        best = max(cl, key=lambda f: f["sharpe"])
        cluster_reps.append({"representative": best, "size": len(cl)})

    return {
        "total_active": len(all_factors),
        "tiers": {
            "t1_extreme_overfit": {"count": len(tier1), "desc": "Sharpe>10 + WR=100% + DD=0", "factors": tier1[:10]},
            "t2_suspect_overfit": {"count": len(tier2), "desc": "Sharpe>10 或 WR>95%", "factors": tier2[:10]},
            "t3_normal": {"count": len(tier3), "desc": "Sharpe 0.5~5, WR<70%, Trades>500", "factors": tier3[:10]},
            "t4_other": {"count": len(tier4), "desc": "其余", "factors": tier4[:10]},
        },
        "theme_distribution": dict(theme_dist.most_common(20)),
        "clusters": {"total": len(clusters), "combinable_factors": len(combinable), "top_clusters": cluster_reps[:20]},
    }


# ── n8n Webhook / Exhaustive Combination / Pipeline API ─────────

@app.post("/api/digger/combine/exhaustive")
async def api_digger_combine_exhaustive(request: Request):
    """穷举组合: 对所有可融合因子做 C(n,k) 组合 → 逐个回测 → 返回最优组合。
    body: {group_size: 2~5, max_combos: 100, skip_tested: true}
    长耗时操作，通过 SSE 流式返回进度。"""
    from agents.factor_library import FactorLibrary
    from agents.bridge_client import get_bridge_client
    from datetime import date, timedelta
    from itertools import combinations
    from starlette.responses import StreamingResponse
    import asyncio

    body = await request.json()
    group_size = min(max(body.get("group_size", 2), 2), 5)
    max_combos = min(body.get("max_combos", 100), 500)
    skip_tested = body.get("skip_tested", True)

    lib = FactorLibrary(redis_client=await get_redis())
    bridge = get_bridge_client()
    candidates = await lib.get_combine_candidates()

    if len(candidates) < group_size:
        return {"ok": False, "error": f"可融合因子仅 {len(candidates)} 个，不足 {group_size}"}

    # Get already-tested combos to skip
    tested_sets = set()
    if skip_tested:
        history = await lib.get_combine_records(limit=500)
        for rec in history:
            ids = tuple(sorted(rec.get("input_factor_ids", [])))
            tested_sets.add(ids)

    all_combos = list(combinations(range(len(candidates)), group_size))
    # Filter already tested
    combos = []
    for combo in all_combos:
        ids = tuple(sorted(candidates[i].id for i in combo))
        if ids not in tested_sets:
            combos.append(combo)
    combos = combos[:max_combos]

    start_date = (date.today() - timedelta(days=180)).isoformat()
    end_date = date.today().isoformat()

    async def stream():
        results = []
        total = len(combos)
        yield f"data: {json.dumps({'type':'start','total':total,'group_size':group_size,'candidates':len(candidates)})}\n\n"

        for idx, combo in enumerate(combos):
            factors = [candidates[i] for i in combo]
            names = [f.sub_theme or f.theme for f in factors]
            factor_ids = [f.id for f in factors]

            # Build combined code
            codes = []
            for i, f in enumerate(factors):
                renamed = f.code.replace("def generate_factor(", f"def _factor_{i+1}(")
                codes.append(renamed)
            combiner_lines = [
                "\n\nimport numpy as np\nimport pandas as pd\n",
                "def generate_factor(matrices):",
                "    factors = []",
            ]
            for i in range(len(factors)):
                combiner_lines.append(f"    try:\n        factors.append(_factor_{i+1}(matrices))\n    except Exception:\n        pass")
            combiner_lines += [
                "    if not factors:",
                "        return pd.DataFrame(0, index=matrices['close'].index, columns=matrices['close'].columns)",
                "    stacked = np.stack([f.values for f in factors], axis=0)",
                "    combined = np.nanmean(stacked, axis=0)",
                "    return pd.DataFrame(combined, index=matrices['close'].index, columns=matrices['close'].columns)",
            ]
            combined_code = "\n\n".join(codes) + "\n".join(combiner_lines)

            yield f"data: {json.dumps({'type':'progress','idx':idx+1,'total':total,'names':names,'ids':factor_ids})}\n\n"

            try:
                resp = await bridge.run_factor_mining(
                    factor_code=combined_code, start_date=start_date, end_date=end_date)
                metrics = resp.get("metrics") or {} if resp.get("status") != "error" else {}
                error = resp.get("error", "") if resp.get("status") == "error" else ""
            except Exception as e:
                metrics = {}
                error = str(e)

            input_info = [{"id": f.id, "theme": f.sub_theme or f.theme, "sharpe": f.sharpe, "ir": f.ir, "ic_mean": f.ic_mean} for f in factors]
            evaluation = lib.evaluate_combine_quality(input_info, metrics)

            record = {
                "input_factors": input_info,
                "input_factor_ids": factor_ids,
                "combined_code_preview": combined_code[:1000],
                "combined_metrics": metrics,
                "evaluation": evaluation,
                "verdict": evaluation["verdict"],
                "status": "accepted" if evaluation["verdict"] == "accept" else "rejected" if evaluation["verdict"] == "reject" else "marginal",
                "source": "exhaustive",
            }
            record_id = await lib.save_combine_record(record)

            result = {
                "idx": idx + 1, "names": names, "ids": factor_ids,
                "metrics": metrics, "verdict": evaluation["verdict"], "record_id": record_id,
                "error": error,
            }
            results.append(result)
            yield f"data: {json.dumps({'type':'result', **result})}\n\n"

            await asyncio.sleep(0.1)  # brief pause between combos

        # Summary
        accepted = [r for r in results if r["verdict"] == "accept"]
        best = max(results, key=lambda r: (r.get("metrics") or {}).get("sharpe", 0)) if results else None
        yield f"data: {json.dumps({'type':'done','tested':len(results),'accepted':len(accepted),'best':best})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/n8n/webhook/factor-mined")
async def n8n_webhook_factor_mined(request: Request):
    """n8n webhook: 因子挖掘完成后回调。
    n8n 可监听此事件触发后续 workflow (融合/策略化/通知)。"""
    body = await request.json()
    # Store latest event for n8n polling
    r = await get_redis()
    event = {
        "type": "factor_mined",
        "factor_id": body.get("factor_id", ""),
        "theme": body.get("theme", ""),
        "sharpe": body.get("sharpe", 0),
        "timestamp": __import__("time").time(),
    }
    await r.lpush("rragent:n8n:events", json.dumps(event))
    await r.ltrim("rragent:n8n:events", 0, 99)  # keep last 100
    return {"ok": True}


@app.get("/api/n8n/events")
async def n8n_get_events(limit: int = 20):
    """n8n 轮询: 获取最近因子管线事件，用于 n8n polling trigger。"""
    r = await get_redis()
    raw = await r.lrange("rragent:n8n:events", 0, limit - 1)
    events = [json.loads(e) for e in raw] if raw else []
    return {"events": events}


_MINE_SESSION_LOCK_KEY = "rragent:mine_session:running"
_MINE_SESSION_LOCK_TTL = 3600  # 1小时超时自动释放，防止崩溃后卡住

@app.post("/api/n8n/trigger/mine")
async def n8n_trigger_mine(request: Request):
    """n8n 触发: 启动一轮因子挖掘。n8n workflow 可定时调用此端点。"""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    rounds = body.get("rounds", 3)
    factors = body.get("factors_per_round", 5)
    ultra_short_weight = float(body.get("ultra_short_weight", 1.0))
    focus = body.get("focus", "")

    # 并发锁：防止多个 session 同时运行导致 139 内存/swap 耗尽
    r = await get_redis()
    locked = await r.set(_MINE_SESSION_LOCK_KEY, "1", nx=True, ex=_MINE_SESSION_LOCK_TTL)
    if not locked:
        return {"ok": False, "skipped": True,
                "message": "上一轮挖掘仍在运行，跳过此次触发（防止 139 内存溢出）"}

    from agents.alpha_digger import run_digger_session
    import asyncio

    async def _run():
        try:
            result = await run_digger_session(
                max_rounds=rounds,
                factors_per_round=factors,
                ultra_short_weight=ultra_short_weight,
            )
            r2 = await get_redis()
            event = {"type": "mine_session_done", "result": result, "focus": focus,
                     "timestamp": __import__("time").time()}
            await r2.lpush("rragent:n8n:events", json.dumps(event, default=str))
            await r2.ltrim("rragent:n8n:events", 0, 99)
        except Exception as e:
            logger.error(f"n8n mine trigger failed: {e}")
        finally:
            r3 = await get_redis()
            await r3.delete(_MINE_SESSION_LOCK_KEY)

    asyncio.create_task(_run())
    focus_tag = f" [{focus}]" if focus else ""
    return {"ok": True, "message": f"挖掘已启动{focus_tag}: {rounds} 轮 x {factors} 因子/轮, ultra_short_weight={ultra_short_weight}"}


# ══════════════════════════════════════════════════════════════════════════════
# 妖股 Dashboard API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/yao/dashboard")
async def yao_dashboard():
    """妖股因子库全貌: 主题统计 / TOP 因子 / 最新信号 / 迭代日志。"""
    from agents.yao_optimizer import analyze_library, REDIS_KEY_DASH_CACHE
    import json
    r = await get_redis()

    # 优先返回缓存
    try:
        cached = await r.get(REDIS_KEY_DASH_CACHE)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    from agents.factor_library import get_factor_library
    fl = get_factor_library()
    return await analyze_library(fl, r)


@app.post("/api/yao/analyze")
async def yao_analyze():
    """运行一次完整的妖股库分析 + 主题权重更新 (n8n 调用)。"""
    from agents.yao_optimizer import run_analysis_and_update
    from agents.factor_library import get_factor_library
    r = await get_redis()
    fl = get_factor_library()
    result = await run_analysis_and_update(fl, r)
    return {"ok": True, "result": result}


@app.post("/api/yao/signals/refresh")
async def yao_signals_refresh(request: Request):
    """用 TOP 妖股因子跑实盘截面筛选，刷新信号缓存 (n8n 调用)。"""
    from agents.yao_optimizer import refresh_signals
    from agents.factor_library import get_factor_library
    from agents.bridge_client import get_bridge_client
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    top_n = int(body.get("top_n", 3))
    r = await get_redis()
    fl = get_factor_library()
    bridge = get_bridge_client()
    signals = await refresh_signals(fl, bridge, r, top_n=top_n)
    return {"ok": True, "count": len(signals)}


@app.post("/api/yao/iterate")
async def yao_iterate(request: Request):
    """根据当前主题权重触发一次针对性迭代挖掘 (前端手动触发 / n8n 调用)。"""
    from agents.yao_optimizer import get_focus_theme_for_next_session
    r = await get_redis()
    locked = await r.set(_YAO_SESSION_LOCK_KEY, "1", nx=True, ex=_YAO_SESSION_LOCK_TTL)
    if not locked:
        return {"ok": False, "skipped": True, "message": "妖股挖掘仍在进行中"}

    focus = await get_focus_theme_for_next_session(r)
    from agents.yao_digger import run_yao_session, THEME_NAMES
    import asyncio

    async def _run():
        try:
            result = await run_yao_session(max_rounds=2, factors_per_round=5, focus_theme_id=focus)
            r2 = await get_redis()
            from agents.yao_optimizer import run_analysis_and_update, log_iteration_event
            from agents.factor_library import get_factor_library
            fl = get_factor_library()
            await run_analysis_and_update(fl, r2)
            event = {
                "ts": __import__("time").time(),
                "ts_str": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "iterate",
                "focus_theme": focus,
                "focus_theme_name": THEME_NAMES.get(focus, focus) if focus else "全主题",
                "admitted": result.get("total_admitted", 0),
                "rounds": result.get("rounds_completed", 0),
            }
            await log_iteration_event(r2, event)
        except Exception as e:
            logger.error(f"yao_iterate failed: {e}")
        finally:
            r3 = await get_redis()
            await r3.delete(_YAO_SESSION_LOCK_KEY)

    asyncio.create_task(_run())
    from agents.yao_optimizer import THEME_NAMES
    theme_label = THEME_NAMES.get(focus, focus) if focus else "全主题随机"
    return {"ok": True, "focus_theme": focus, "focus_theme_name": theme_label,
            "message": f"迭代挖掘已启动 → 重点主题: {theme_label}"}


_YAO_SESSION_LOCK_KEY = "rragent:yao_session:running"
_YAO_SESSION_LOCK_TTL = 3600

@app.post("/api/n8n/trigger/yao_mine")
async def n8n_trigger_yao_mine(request: Request):
    """n8n 触发: 启动一轮妖股因子挖掘。

    妖股因子专注于挖掘A股高弹性个股「启动前 1-3 天」的量价预测信号。
    与普通 /mine 的区别: 主题池全部针对妖股特征，LLM 上下文注入妖股先验知识。

    Body 参数:
        rounds: 挖掘轮数 (默认 3)
        factors_per_round: 每轮因子数 (默认 5)
        focus_theme: 指定妖股主题 ID (可选，空=随机轮换)
    """
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    rounds = int(body.get("rounds", 3))
    factors = int(body.get("factors_per_round", 5))
    focus_theme = body.get("focus_theme", "") or None

    # 并发锁: 妖股 session 独立锁, 与普通 mine 互不干扰
    r = await get_redis()
    locked = await r.set(_YAO_SESSION_LOCK_KEY, "1", nx=True, ex=_YAO_SESSION_LOCK_TTL)
    if not locked:
        return {"ok": False, "skipped": True,
                "message": "妖股挖掘上一轮仍在运行，跳过（防止 139 内存溢出）"}

    from agents.yao_digger import run_yao_session
    import asyncio

    async def _run():
        try:
            result = await run_yao_session(
                max_rounds=rounds,
                factors_per_round=factors,
                focus_theme_id=focus_theme,
            )
            r2 = await get_redis()
            event = {
                "type": "yao_session_done",
                "result": result,
                "focus_theme": focus_theme,
                "timestamp": __import__("time").time(),
            }
            await r2.lpush("rragent:n8n:events", json.dumps(event, default=str))
            await r2.ltrim("rragent:n8n:events", 0, 99)
        except Exception as e:
            logger.error(f"yao_mine trigger failed: {e}")
        finally:
            r3 = await get_redis()
            await r3.delete(_YAO_SESSION_LOCK_KEY)

    asyncio.create_task(_run())
    theme_tag = f" [主题={focus_theme}]" if focus_theme else ""
    return {
        "ok": True,
        "message": f"妖股挖掘已启动{theme_tag}: {rounds} 轮 × {factors} 因子/轮",
    }


@app.post("/api/n8n/trigger/combine-smart")
async def n8n_trigger_combine_smart(request: Request):
    """n8n 触发: 智能择优融合 — 跨主题互补 + 贪心序列。

    优先级: smart > greedy > exhaustive
    - smart:  跨主题互补组合 (不同主题代表因子两两/三三组合)
    - greedy: 从最强因子开始，逐步加入最互补因子，测试 2/3/4 因子组合
    两种策略自动生成组合，回测评估后记录结果。
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    max_combos = body.get("max_combos", 20)

    from agents.factor_library import FactorLibrary
    from agents.bridge_client import get_bridge_client
    from datetime import date, timedelta
    import asyncio

    async def _run():
        lib = FactorLibrary(redis_client=await get_redis())
        bridge = get_bridge_client()
        rd = await get_redis()

        # 获取已测试过的组合
        history = await lib.get_combine_records(limit=500)
        tested = set(tuple(sorted(rec.get("input_factor_ids", []))) for rec in history)

        start_date = (date.today() - timedelta(days=180)).isoformat()
        end_date = date.today().isoformat()
        results = {"smart": [], "greedy": [], "total_tested": 0, "total_accepted": 0}

        # ── Phase 1: Smart 跨主题互补组合 ──
        smart_groups = await lib.get_smart_combine_groups(max_group_size=4)
        tested_count = 0
        for group in smart_groups:
            if tested_count >= max_combos:
                break
            combo_key = tuple(sorted(f.id for f in group))
            if combo_key in tested:
                continue

            metrics, verdict, record_id, new_fid = await _run_combine(
                bridge, lib, group, start_date, end_date, source="smart"
            )
            tested.add(combo_key)
            tested_count += 1
            results["total_tested"] += 1

            result_entry = {
                "record_id": record_id,
                "factors": [{"id": f.id, "theme": f.theme, "sharpe": f.sharpe} for f in group],
                "verdict": verdict,
                "combined_sharpe": (metrics or {}).get("sharpe_ratio") or (metrics or {}).get("sharpe", 0),
            }
            results["smart"].append(result_entry)
            if verdict == "accept":
                results["total_accepted"] += 1

            await asyncio.sleep(1)

        # ── Phase 2: Greedy 贪心递增组合 ──
        greedy_seq = await lib.get_greedy_combine_sequence(max_factors=5)
        if len(greedy_seq) >= 2:
            for size in range(2, min(len(greedy_seq) + 1, 5)):
                if tested_count >= max_combos:
                    break
                group = greedy_seq[:size]
                combo_key = tuple(sorted(f.id for f in group))
                if combo_key in tested:
                    continue

                metrics, verdict, record_id, new_fid = await _run_combine(
                    bridge, lib, group, start_date, end_date, source="greedy"
                )
                tested.add(combo_key)
                tested_count += 1
                results["total_tested"] += 1

                result_entry = {
                    "record_id": record_id,
                    "factors": [{"id": f.id, "theme": f.theme, "sharpe": f.sharpe} for f in group],
                    "verdict": verdict,
                    "combined_sharpe": (metrics or {}).get("sharpe_ratio") or (metrics or {}).get("sharpe", 0),
                    "group_size": size,
                }
                results["greedy"].append(result_entry)
                if verdict == "accept":
                    results["total_accepted"] += 1

                await asyncio.sleep(1)

        # Push event
        event = {
            "type": "combine_smart_done",
            "tested": results["total_tested"],
            "accepted": results["total_accepted"],
            "smart_count": len(results["smart"]),
            "greedy_count": len(results["greedy"]),
            "timestamp": time.time(),
        }
        await rd.lpush("rragent:n8n:events", json.dumps(event, default=str))
        await rd.ltrim("rragent:n8n:events", 0, 99)

    asyncio.create_task(_run())
    return {"ok": True, "message": f"智能融合已启动 (smart + greedy, max_combos={max_combos})"}


async def _run_combine(bridge, lib, factors, start_date, end_date, source="manual"):
    """执行一次因子融合: 组装代码 → 回测 → 评估 → 记录。

    Returns: (metrics, verdict, record_id)
    """
    codes = []
    for i, f in enumerate(factors):
        renamed = f.code.replace("def generate_factor(", f"def _factor_{i+1}(")
        codes.append(renamed)

    combiner = (
        "\n\nimport numpy as np\nimport pandas as pd\n\n"
        "def generate_factor(matrices):\n"
        "    factors = []\n"
    )
    for i in range(len(factors)):
        combiner += f"    try:\n        factors.append(_factor_{i+1}(matrices))\n    except Exception:\n        pass\n"
    combiner += (
        "    if not factors:\n"
        "        return pd.DataFrame(0, index=matrices['close'].index, columns=matrices['close'].columns)\n"
        "    stacked = np.stack([f.values for f in factors], axis=0)\n"
        "    combined = np.nanmean(stacked, axis=0)\n"
        "    return pd.DataFrame(combined, index=matrices['close'].index, columns=matrices['close'].columns)\n"
    )
    combined_code = "\n\n".join(codes) + combiner

    metrics = {}
    try:
        resp = await bridge.run_factor_mining(
            factor_code=combined_code,
            start_date=start_date,
            end_date=end_date,
        )
        if resp.get("status") != "error":
            metrics = resp.get("metrics") or {}
    except Exception as e:
        logger.warning("combine backtest failed: %s", e)

    input_info = [{"id": f.id, "theme": f.sub_theme or f.theme,
                    "sharpe": f.sharpe, "ir": f.ir, "ic_mean": f.ic_mean,
                    "win_rate": f.win_rate, "max_drawdown": f.max_drawdown} for f in factors]

    evaluation = lib.evaluate_combine_quality(input_info, metrics)
    verdict = evaluation["verdict"]

    record = {
        "input_factors": input_info,
        "input_factor_ids": [f.id for f in factors],
        "combined_metrics": metrics,
        "evaluation": evaluation,
        "verdict": verdict,
        "source": source,
        "status": "accepted" if verdict == "accept" else "rejected" if verdict == "reject" else "marginal",
    }
    record_id = await lib.save_combine_record(record)

    # 融合成功的因子自动加入因子库
    new_factor_id = None
    if verdict == "accept" and metrics:
        themes = list(set(f.theme for f in factors if f.theme))
        combined_theme = "combo_" + "+".join(themes[:3]) if themes else "combo"
        sub_theme = f"融合{len(factors)}因子({source})"
        ok, reason, new_factor_id = await lib.add_factor(
            code=combined_code,
            metrics=metrics,
            theme=combined_theme,
            sub_theme=sub_theme,
        )
        if ok:
            logger.info("融合因子入库: %s (来源: %s, sharpe=%.3f)",
                        new_factor_id, source, metrics.get("sharpe_ratio", metrics.get("sharpe", 0)))

    return metrics, verdict, record_id, new_factor_id


@app.post("/api/n8n/trigger/combine-all")
async def n8n_trigger_combine_all(request: Request):
    """n8n 触发: 穷举融合 (在 smart combine 之后执行，覆盖剩余组合)。"""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    group_size = body.get("group_size", 2)
    max_combos = body.get("max_combos", 50)

    from agents.factor_library import FactorLibrary
    from agents.bridge_client import get_bridge_client
    from itertools import combinations
    from datetime import date, timedelta
    import asyncio

    async def _run():
        lib = FactorLibrary(redis_client=await get_redis())
        bridge = get_bridge_client()
        candidates = await lib.get_combine_candidates()
        if len(candidates) < group_size:
            return

        history = await lib.get_combine_records(limit=500)
        tested = set(tuple(sorted(rec.get("input_factor_ids", []))) for rec in history)

        all_combos = list(combinations(range(len(candidates)), group_size))
        combos = [c for c in all_combos if tuple(sorted(candidates[i].id for i in c)) not in tested][:max_combos]

        start_date = (date.today() - timedelta(days=180)).isoformat()
        end_date = date.today().isoformat()
        accepted = 0

        for combo in combos:
            factors = [candidates[i] for i in combo]
            codes = []
            for i, f in enumerate(factors):
                codes.append(f.code.replace("def generate_factor(", f"def _factor_{i+1}("))
            combiner = (
                "\n\nimport numpy as np\nimport pandas as pd\n\n"
                "def generate_factor(matrices):\n    factors = []\n"
            )
            for i in range(len(factors)):
                combiner += f"    try:\n        factors.append(_factor_{i+1}(matrices))\n    except Exception:\n        pass\n"
            combiner += (
                "    if not factors:\n        return pd.DataFrame(0, index=matrices['close'].index, columns=matrices['close'].columns)\n"
                "    stacked = np.stack([f.values for f in factors], axis=0)\n"
                "    combined = np.nanmean(stacked, axis=0)\n"
                "    return pd.DataFrame(combined, index=matrices['close'].index, columns=matrices['close'].columns)\n"
            )
            combined_code = "\n\n".join(codes) + combiner
            try:
                resp = await bridge.run_factor_mining(factor_code=combined_code, start_date=start_date, end_date=end_date)
                metrics = resp.get("metrics") or {} if resp.get("status") != "error" else {}
            except Exception:
                metrics = {}

            input_info = [{"id": f.id, "theme": f.sub_theme or f.theme, "sharpe": f.sharpe, "ir": f.ir, "ic_mean": f.ic_mean} for f in factors]
            evaluation = lib.evaluate_combine_quality(input_info, metrics)
            record = {
                "input_factors": input_info, "input_factor_ids": [f.id for f in factors],
                "combined_metrics": metrics, "evaluation": evaluation, "verdict": evaluation["verdict"],
                "status": "accepted" if evaluation["verdict"] == "accept" else "rejected",
                "source": "n8n_exhaustive",
            }
            await lib.save_combine_record(record)
            if evaluation["verdict"] == "accept":
                accepted += 1
            await asyncio.sleep(1)

        r = await get_redis()
        event = {"type": "combine_all_done", "tested": len(combos), "accepted": accepted, "timestamp": __import__("time").time()}
        await r.lpush("rragent:n8n:events", json.dumps(event))
        await r.ltrim("rragent:n8n:events", 0, 99)

    asyncio.create_task(_run())
    return {"ok": True, "message": f"穷举融合已启动: group_size={group_size}, max_combos={max_combos}"}


@app.get("/api/n8n/pipeline/status")
async def n8n_pipeline_status():
    """n8n 查询: 获取因子管线整体状态 (因子数、最近融合、挖掘状态)。"""
    from agents.factor_library import FactorLibrary
    lib = FactorLibrary(redis_client=await get_redis())
    stats = await lib.get_stats()
    r = await get_redis()
    recent_events = await r.lrange("rragent:n8n:events", 0, 4)
    events = [json.loads(e) for e in recent_events] if recent_events else []
    digger_running = bool(await r.get("rragent:digger:running"))
    return {
        "factor_stats": stats,
        "digger_running": digger_running,
        "recent_events": events,
    }


@app.post("/api/n8n/trigger/promote")
async def n8n_trigger_promote(request: Request):
    """n8n 触发: 自动将 Top N 因子策略化 → 同步到 139 screener。

    流程:
      1. 从因子库选出 sharpe 最高且尚未策略化的因子
      2. 逐个执行 to-strategy (因子 → 回测 → 存入策略库)
      3. 对生成的策略执行 sync (部署到 139 + 注册 screener preset)
      4. 推送事件供 n8n 通知
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    top_n = body.get("top_n", 3)
    min_sharpe = body.get("min_sharpe", 1.0)

    from agents.factor_library import FactorLibrary
    from agents.bridge_client import get_bridge_client
    from datetime import date, timedelta
    import asyncio

    async def _run():
        lib = FactorLibrary(redis_client=await get_redis())
        bridge = get_bridge_client()
        rd = await get_redis()

        # 获取已策略化的因子 ID
        raw_strats = await rd.hgetall(STRATEGY_REDIS_KEY)
        strategized_fids = set()
        for v in raw_strats.values():
            try:
                s = json.loads(v)
                if s.get("factor_id"):
                    strategized_fids.add(s["factor_id"])
            except Exception:
                pass

        # 选出尚未策略化的 Top 因子
        all_factors = await lib.get_all_factors(status="active")
        candidates = [f for f in all_factors
                      if f.id not in strategized_fids and f.sharpe >= min_sharpe]
        candidates.sort(key=lambda f: f.sharpe, reverse=True)
        targets = candidates[:top_n]

        if not targets:
            event = {"type": "promote_done", "promoted": 0,
                     "message": "没有符合条件的未策略化因子",
                     "timestamp": time.time()}
            await rd.lpush("rragent:n8n:events", json.dumps(event))
            await rd.ltrim("rragent:n8n:events", 0, 99)
            return

        promoted = []
        for factor in targets:
            try:
                # Step 1: to-strategy
                entry_pct = 0.95
                exit_pct = 0.70
                strategy_code = factor.code + f"""

def generate_signals(matrices):
    factor = generate_factor(matrices)
    close = matrices['close']
    rank_pct = factor.rank(axis=1, pct=True)
    factor_rising = factor > factor.shift(1)
    entries = (rank_pct > {entry_pct}) & factor_rising
    ma5 = close.rolling(5).mean()
    exits = (rank_pct < {exit_pct}) | (close < ma5)
    entries = entries.fillna(False)
    exits = exits.fillna(False)
    return entries, exits
"""
                start_date = (date.today() - timedelta(days=180)).isoformat()
                end_date = date.today().isoformat()

                resp = await bridge.run_alpha(
                    alpha_code=strategy_code,
                    start_date=start_date, end_date=end_date,
                    mode="technical",
                )
                metrics = resp.get("metrics", {})
                if resp.get("status") == "error":
                    logger.warning("promote: factor %s backtest failed: %s", factor.id, resp.get("error"))
                    continue

                # 质量检查: 策略回测后也要过质量关
                from agents.factor_quality import metrics_audit
                qr = metrics_audit(metrics)
                if not qr.passed:
                    logger.info("promote: factor %s strategy failed quality: %s", factor.id, qr.summary)
                    continue

                strat_id = f"strat_{int(time.time())}_{uuid.uuid4().hex[:6]}"
                theme = factor.sub_theme or factor.theme
                strat_record = {
                    "id": strat_id,
                    "title": f"[{theme}] Sharpe={factor.sharpe:.2f}",
                    "description": f"基于因子 {factor.id} ({theme}) 自动策略化",
                    "source": "factor", "factor_id": factor.id,
                    "factor_theme": theme, "factor_sharpe": factor.sharpe,
                    "code": strategy_code, "metrics": metrics,
                    "params": {"entry_pct": entry_pct, "exit_pct": exit_pct},
                    "status": "draft", "synced_to_139": False,
                    "quality_grade": qr.grade, "quality_score": qr.score,
                    "created_at": time.time(),
                }
                await rd.hset(STRATEGY_REDIS_KEY, strat_id, json.dumps(strat_record, ensure_ascii=False, default=str))

                # Step 2: sync to 139 (deploy + ledger + register-preset)
                try:
                    await bridge._post("/strategy/deploy/", {
                        "strategy_id": strat_id, "code": strategy_code,
                        "filename": f"{strat_id}.py",
                    })
                    ledger_resp = await bridge.save_strategy(
                        title=f"[因子] {theme} Sharpe={factor.sharpe:.2f}",
                        strategy_code=strategy_code,
                        backtest_metrics=metrics,
                        status="APPROVE", topic=theme,
                        model_used="rrclaw-factor-pipeline",
                    )
                    ledger_id = ledger_resp.get("id")
                    # Register as preset so it appears in /presets/ list
                    slug = f"factor_{factor.id}_{int(time.time())}"
                    await bridge._post("/strategy/register-preset/", {
                        "slug": slug,
                        "name": f"[因子] {theme} Sharpe={factor.sharpe:.2f}",
                        "description": f"因子策略选股: {theme} (Sharpe={factor.sharpe:.2f})",
                        "category": "factor",
                        "payload": {
                            "code": strategy_code,
                            "metrics": metrics,
                            "params": {"entry_pct": entry_pct, "exit_pct": exit_pct},
                        },
                        "ledger_id": ledger_id,
                    })
                    strat_record["synced_to_139"] = True
                    strat_record["status"] = "synced"
                    strat_record["ledger_id"] = ledger_id
                    strat_record["preset_slug"] = slug
                    await rd.hset(STRATEGY_REDIS_KEY, strat_id, json.dumps(strat_record, ensure_ascii=False, default=str))
                except Exception as e:
                    logger.warning("promote: sync to 139 failed for %s: %s", strat_id, e)

                promoted.append({
                    "strategy_id": strat_id, "factor_id": factor.id,
                    "theme": theme, "sharpe": factor.sharpe,
                    "quality_grade": qr.grade,
                })
                await asyncio.sleep(2)

            except Exception as e:
                logger.error("promote: factor %s failed: %s", factor.id, e)

        event = {
            "type": "promote_done",
            "promoted": len(promoted),
            "strategies": promoted,
            "timestamp": time.time(),
        }
        await rd.lpush("rragent:n8n:events", json.dumps(event, default=str))
        await rd.ltrim("rragent:n8n:events", 0, 99)

    asyncio.create_task(_run())
    return {"ok": True, "message": f"策略推送已启动: top_n={top_n}, min_sharpe={min_sharpe}"}


# ── n8n: System Health & Auto-Promote ────────────────

@app.get("/api/n8n/system/health")
async def n8n_system_health():
    """n8n 查询: 全面检查 rrclaw 系统状态 — 服务健康、因子库质量、是否需要 selfimprove。"""
    import subprocess
    r = await get_redis()

    # 1. 检查各服务进程
    services = {
        "webchat": {"process": "webchat", "port": 7789},
        "orchestrator": {"process": "orchestrator", "port": None},
        "telegram_bot": {"process": "telegram_bot", "port": None},
        "feishu_bot": {"process": "feishu_bot", "port": None},
    }
    service_status = {}
    for name, info in services.items():
        if name == "webchat":
            # webchat 就是当前进程，肯定在运行
            import os as _os
            service_status[name] = {"running": True, "pids": [str(_os.getpid())]}
            continue
        if info.get("port"):
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{info['port']}"],
                    capture_output=True, text=True, timeout=5
                )
                pids = [p for p in result.stdout.strip().split("\n") if p]
                service_status[name] = {"running": len(pids) > 0, "pids": pids}
            except Exception:
                service_status[name] = {"running": False, "pids": []}
        else:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", info["process"]],
                    capture_output=True, text=True, timeout=5
                )
                service_status[name] = {"running": result.returncode == 0, "pids": result.stdout.strip().split("\n") if result.returncode == 0 else []}
            except Exception:
                service_status[name] = {"running": False, "pids": []}

    # 2. Redis 连通性
    redis_ok = False
    try:
        await r.ping()
        redis_ok = True
    except Exception:
        pass

    # 3. 因子库状态
    from agents.factor_library import FactorLibrary
    lib = FactorLibrary(redis_client=r)
    factor_stats = await lib.get_stats()

    # 4. 渠道心跳
    heartbeats = {}
    try:
        raw = await r.hgetall("rragent:channel_heartbeats")
        for ch, hb in raw.items():
            ch_name = ch if isinstance(ch, str) else ch.decode()
            try:
                hb_data = json.loads(hb)
                age = time.time() - hb_data.get("ts", 0)
                heartbeats[ch_name] = {"online": age < 30, "age_seconds": round(age, 1)}
            except Exception:
                heartbeats[ch_name] = {"online": False, "age_seconds": -1}
    except Exception:
        pass

    # 5. 最近事件
    recent_events = await r.lrange("rragent:n8n:events", 0, 9)
    events = [json.loads(e) for e in recent_events] if recent_events else []

    # 6. SOUL 完整性
    soul_ok = False
    try:
        soul_raw = await r.get("rragent:soul")
        soul_ok = bool(soul_raw and len(soul_raw) > 100)
    except Exception:
        pass

    # 7. Memory 状态
    memory_count = 0
    try:
        memory_count = await r.hlen("rragent:memory:entities") or 0
    except Exception:
        pass

    # 8. selfimprove 判断
    needs_selfimprove = False
    selfimprove_reasons = []

    # 因子库太少
    active_count = factor_stats.get("active_count", 0)
    if active_count < 30:
        needs_selfimprove = True
        selfimprove_reasons.append(f"因子库偏少: {active_count} (目标>50)")

    # 平均质量差
    avg_sharpe = factor_stats.get("avg_sharpe", 0)
    if avg_sharpe and avg_sharpe < 1.0:
        needs_selfimprove = True
        selfimprove_reasons.append(f"因子平均 Sharpe 偏低: {avg_sharpe:.3f} (目标>1.0)")

    # 服务宕机
    down_services = [name for name, s in service_status.items() if not s["running"]]
    if down_services:
        selfimprove_reasons.append(f"服务未运行: {', '.join(down_services)}")

    # 渠道离线
    offline_channels = [ch for ch, s in heartbeats.items() if not s["online"]]
    if offline_channels:
        selfimprove_reasons.append(f"渠道离线: {', '.join(offline_channels)}")

    # SOUL 缺失
    if not soul_ok:
        needs_selfimprove = True
        selfimprove_reasons.append("SOUL 缺失或损坏")

    # 长时间未挖掘
    last_mine = None
    for ev in events:
        if ev.get("type") == "mine_session_done":
            last_mine = ev.get("timestamp")
            break
    if last_mine and (time.time() - last_mine) > 86400:
        needs_selfimprove = True
        selfimprove_reasons.append(f"超过 24h 未进行因子挖掘")

    # 9. ReflectionEngine 洞察
    reflection_insight = ""
    try:
        from agents.memory.reflection_engine import ReflectionEngine
        re = ReflectionEngine()
        reflection_insight = re.generate_daily_insight()
        failing_agents = list(re.get_failure_prone_agents())
        if failing_agents:
            needs_selfimprove = True
            selfimprove_reasons.append(f"Agent 失败率过高: {', '.join(failing_agents)}")
    except Exception:
        reflection_insight = ""
        failing_agents = []

    return {
        "services": service_status,
        "redis": redis_ok,
        "channels": heartbeats,
        "factor_stats": factor_stats,
        "soul_ok": soul_ok,
        "memory_entities": memory_count,
        "recent_events": events[:5],
        "needs_selfimprove": needs_selfimprove,
        "selfimprove_reasons": selfimprove_reasons,
        "reflection_insight": reflection_insight,
        "failing_agents": failing_agents if 'failing_agents' in dir() else [],
    }


# ── Self-Improve (自修复 + 自学习 + 自我进化) ─────────

# launchctl label 映射
_LAUNCHD_LABELS = {
    "orchestrator": "com.openclaw.orchestrator",
    "market": "com.openclaw.market-agent",
    "analysis": "com.openclaw.analysis-agent",
    "news": "com.openclaw.news-agent",
    "strategist": "com.openclaw.strategist-agent",
    "browser": "com.openclaw.browser-agent",
    "general": "com.openclaw.general-agent",
    "backtest": "com.openclaw.backtest-agent",
    "monitor": "com.openclaw.monitor-agent",
    "telegram_bot": "com.openclaw.telegram-bot",
    "feishu_bot": "com.openclaw.feishu-bot",
    "webchat": "com.openclaw.webchat",
    "n8n": "com.openclaw.n8n",
    "desktop": "com.openclaw.desktop-agent",
    "dev": "com.openclaw.dev-agent",
    "apple": "com.openclaw.apple-agent",
}


async def _call_orchestrator_skill(r, action: str, params: dict = None, timeout: float = 30) -> dict:
    """通过 Redis Pub/Sub 调用 orchestrator skill 并等待回复。"""
    import asyncio
    msg_id = f"selfimprove_{action}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    # orchestrator replies to rragent:{sender}, sender="selfimprove"
    reply_ch = "rragent:selfimprove"

    pubsub = r.pubsub()
    await pubsub.subscribe(reply_ch)

    msg = json.dumps({
        "id": msg_id, "sender": "selfimprove", "target": "orchestrator",
        "action": action, "params": params or {},
        "timestamp": time.time(),
    })
    await r.publish("rragent:orchestrator", msg)

    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                timeout=min(2.0, deadline - time.time() + 0.1),
            )
            if raw is None:
                continue
            if raw["type"] != "message":
                continue
            data = json.loads(raw["data"])
            # orchestrator reply sets id=original.id and reply_to=original.id
            if data.get("id") == msg_id or data.get("reply_to") == msg_id:
                return data.get("result", data)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    finally:
        await pubsub.unsubscribe(reply_ch)
        await pubsub.close()

    return {"error": f"orchestrator {action} 超时 ({timeout}s)"}


_EVOLVER_DIR = "/Users/clawagent/.openclaw/workspace/skills/capability-evolver"


async def _run_evolver_op(op_script: str, timeout: int = 30) -> dict:
    """在 clawagent 用户下运行 capability-evolver 操作模块，返回 JSON 结果。"""
    import subprocess as _sp
    # 用绝对路径 require，避免 cwd 依赖问题
    script = op_script.replace('require("./src/', f'require("{_EVOLVER_DIR}/src/')
    node_bin = "/opt/homebrew/bin/node"
    import getpass
    if getpass.getuser() == "clawagent":
        cmd = [node_bin, "-e", script]
    else:
        cmd = ["sudo", "-u", "clawagent", "-H", node_bin, "-e", script]
    try:
        result = _sp.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_EVOLVER_DIR)
        # evolver 可能把 JSON 输出到 stdout 或 stderr (warnings 混合)
        combined = (result.stdout + "\n" + result.stderr).strip()
        # 提取最后一行 JSON
        for line in reversed(combined.split("\n")):
            line = line.strip()
            if line.startswith("{") or line.startswith("["):
                return json.loads(line)
        return {"raw": combined[:500], "returncode": result.returncode}
    except _sp.TimeoutExpired:
        return {"error": f"evolver op timeout ({timeout}s)"}
    except Exception as e:
        return {"error": str(e)[:200]}


async def _launchctl_restart(svc_name: str, label: str) -> str:
    """通过 launchctl 重启服务，返回结果描述。"""
    import subprocess as _sp
    try:
        uid_result = _sp.run(["id", "-u", "clawagent"], capture_output=True, text=True, timeout=5)
        claw_uid = uid_result.stdout.strip() or "503"
        result = _sp.run(
            ["sudo", "launchctl", "kickstart", "-k", f"gui/{claw_uid}/{label}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return f"{svc_name}: kickstart 成功"
        plist_path = f"/Users/clawagent/Library/LaunchAgents/{label}.plist"
        _sp.run(["sudo", "launchctl", "bootout", f"gui/{claw_uid}/{label}"],
                capture_output=True, timeout=10)
        res2 = _sp.run(["sudo", "launchctl", "bootstrap", f"gui/{claw_uid}", plist_path],
                       capture_output=True, text=True, timeout=10)
        if res2.returncode == 0:
            return f"{svc_name}: bootstrap 成功 (fallback)"
        return f"FAIL:{svc_name}: {result.stderr.strip()[:100]}"
    except Exception as e:
        return f"FAIL:{svc_name}: {str(e)[:100]}"


@app.post("/api/n8n/trigger/selfimprove")
async def n8n_trigger_selfimprove(request: Request):
    """n8n 触发: 系统自修复 + 自学习 + 自我进化。

    工作流:
    1. 调用 health 检测问题
    2. 服务宕机 → launchctl 重启
    3. SOUL 缺失 → 调用 orchestrator.soul_check 检测 + 重建
    4. 记忆退化 → 调用 memory_health + memory_compress + memory_remind
    5. 路由不准 → 调用 reflect_insight 自学习优化路由
    6. 长时间未挖掘 → 触发轻量挖掘
    7. n8n 连通 → 自检修复
    8. Agent 失败率高 → 调用 reflect_stats 分析 + 自我进化
    """
    import subprocess as _sp
    import asyncio

    r = await get_redis()
    repairs = []
    failures = []
    skills_called = []

    # ── 1. 获取 health 状态 ──
    health = await n8n_system_health()
    reasons = health.get("selfimprove_reasons", [])

    if not reasons and not health.get("failing_agents"):
        return {"ok": True, "message": "系统健康，无需修复", "repairs": [], "failures": []}

    # ── 2. 修复未运行的服务 ──
    for svc_name, svc_info in health.get("services", {}).items():
        if svc_info.get("running"):
            continue
        label = _LAUNCHD_LABELS.get(svc_name)
        if not label:
            failures.append(f"{svc_name}: 无 launchd label 映射")
            continue
        msg = await _launchctl_restart(svc_name, label)
        if msg.startswith("FAIL:"):
            failures.append(msg[5:])
        else:
            repairs.append(msg)

    # ── 3. SOUL 修复 — 调用 orchestrator skill ──
    if not health.get("soul_ok"):
        # 先尝试通过 orchestrator 的 soul_check 诊断
        soul_result = await _call_orchestrator_skill(r, "soul_check", timeout=15)
        skills_called.append("soul_check")

        if soul_result.get("error") or soul_result.get("status") == "tampered":
            # soul_check 报异常，手动重建 SOUL
            try:
                import pathlib, hashlib
                souls_dir = pathlib.Path(__file__).parent / "agents" / "souls"
                soul_data = {}
                for md_file in sorted(souls_dir.glob("*.md")):
                    content = md_file.read_text(encoding="utf-8")
                    if content.strip():
                        soul_data[md_file.stem] = content
                if soul_data:
                    soul_blob = json.dumps(soul_data, ensure_ascii=False, sort_keys=True)
                    soul_hash = hashlib.sha256(soul_blob.encode()).hexdigest()[:16]
                    await r.set("rragent:soul", soul_blob)
                    await r.set("rragent:soul:hash", soul_hash)
                    repairs.append(f"SOUL 重建: {len(soul_data)} 身份 (hash={soul_hash})")
                    # 接受新基线
                    await _call_orchestrator_skill(r, "soul_accept", timeout=10)
                    skills_called.append("soul_accept")
                else:
                    failures.append("SOUL: souls 目录无有效 .md 文件")
            except Exception as e:
                failures.append(f"SOUL 重建失败: {str(e)[:100]}")
        else:
            repairs.append(f"SOUL 检查通过: {soul_result.get('status', 'ok')}")

    # ── 4. 记忆系统健康 — 调用 orchestrator skills ──
    memory_issues = [r for r in reasons if "记忆" in r or "memory" in r.lower()]
    if memory_issues or health.get("memory_entities", 0) == 0:
        # 记忆健康检查
        mem_health = await _call_orchestrator_skill(r, "memory_health", timeout=20)
        skills_called.append("memory_health")

        # 如果有孤立节点或退化，执行压缩
        orphans = mem_health.get("graph", {}).get("orphan_nodes", 0) if isinstance(mem_health, dict) else 0
        if orphans > 10 or memory_issues:
            compress_result = await _call_orchestrator_skill(r, "memory_compress", timeout=30)
            skills_called.append("memory_compress")
            repairs.append(f"记忆压缩: orphans={orphans}, 已执行 compress")

        # 触发跨 Agent 记忆提醒
        remind_result = await _call_orchestrator_skill(r, "memory_remind", timeout=20)
        skills_called.append("memory_remind")
        repairs.append("记忆提醒: 已触发跨 Agent 冗余扫描")

    # ── 5. 自学习 — 反思引擎 ──
    failing = health.get("failing_agents", [])
    if failing:
        # 有 Agent 失败率过高，调用反思统计 + 洞察
        stats_result = await _call_orchestrator_skill(r, "reflect_stats", timeout=15)
        skills_called.append("reflect_stats")
        repairs.append(f"反思统计: 已分析 Agent 失败率 ({', '.join(failing)})")

    # 每次 selfimprove 都生成洞察用于自我进化
    insight_result = await _call_orchestrator_skill(r, "reflect_insight", timeout=15)
    skills_called.append("reflect_insight")
    insight_text = ""
    if isinstance(insight_result, dict):
        insight_text = insight_result.get("text", "")
    repairs.append(f"自学习洞察: {insight_text[:100] if insight_text else '已执行'}")

    # ── 6. 长时间未挖掘 → 触发挖掘 ──
    for reason in reasons:
        if "未进行因子挖掘" in reason:
            mine_msg = json.dumps({
                "id": f"selfimprove_mine_{int(time.time())}",
                "sender": "selfimprove", "target": "orchestrator",
                "action": "mine_factors",
                "params": {"themes": ["volatility_regime", "mean_reversion"], "count": 1},
                "timestamp": time.time(),
            })
            await r.publish("rragent:orchestrator", mine_msg)
            repairs.append("因子挖掘: 已触发轻量挖掘 (2 themes x 1 round)")
            break

    # ── 7. n8n 连通性自检 ──
    try:
        n8n_check = _sp.run(
            ["curl", "-s", "--max-time", "3", "http://127.0.0.1:5678/healthz"],
            capture_output=True, text=True, timeout=5,
        )
        if '"ok"' in n8n_check.stdout:
            repairs.append("n8n: 连通正常")
        else:
            msg = await _launchctl_restart("n8n", "com.openclaw.n8n")
            if msg.startswith("FAIL:"):
                failures.append(msg[5:])
            else:
                repairs.append(f"n8n: {msg}")
    except Exception as e:
        failures.append(f"n8n 检测异常: {str(e)[:100]}")

    # ── 8. capability-evolver 自修复 ──
    evolver_health = {}
    try:
        # 8a. git 自修复
        repair_result = await _run_evolver_op(
            'const sr = require("./src/ops/self_repair");'
            'console.log(JSON.stringify(sr.repair()));',
            timeout=20,
        )
        if isinstance(repair_result, list) and repair_result:
            repairs.append(f"evolver git 修复: {', '.join(repair_result)}")
        skills_called.append("evolver.self_repair")

        # 8b. 系统健康检查 (磁盘/内存/进程)
        hc_result = await _run_evolver_op(
            'const hc = require("./src/ops/health_check");'
            'console.log(JSON.stringify(hc.runHealthCheck()));',
            timeout=15,
        )
        evolver_health = hc_result
        skills_called.append("evolver.health_check")

        if isinstance(hc_result, dict):
            for check in hc_result.get("checks", []):
                if not check.get("ok"):
                    sev = check.get("severity", "info")
                    desc = f"evolver: {check['name']}={check.get('status', '?')}"
                    if sev == "critical":
                        failures.append(desc)
                    elif sev == "warning":
                        repairs.append(f"[warn] {desc}")
    except Exception as e:
        failures.append(f"evolver 调用异常: {str(e)[:100]}")

    # ── 9. capability-evolver 进化触发 ──
    has_critical = any("FAIL:" in f or "critical" in f.lower() for f in failures)
    if not has_critical and (repairs or reasons):
        try:
            trigger_result = await _run_evolver_op(
                'const t = require("./src/ops/trigger");'
                'console.log(JSON.stringify({sent: t.send()}));',
                timeout=10,
            )
            if isinstance(trigger_result, dict) and trigger_result.get("sent"):
                repairs.append("evolver: 进化唤醒信号已发送")
            skills_called.append("evolver.trigger")
        except Exception as e:
            pass  # 进化触发是可选的，不影响整体

    # ── 10. self-improving 学习反馈 ──
    corrections_logged = 0
    try:
        corrections = []
        for rep in repairs:
            corrections.append({
                "type": "repair_success",
                "detail": rep,
                "timestamp": time.time(),
            })
        for fail in failures:
            corrections.append({
                "type": "repair_failure",
                "detail": fail,
                "timestamp": time.time(),
            })
        if corrections:
            await r.lpush(
                "rragent:selfimprove:corrections",
                *[json.dumps(c, ensure_ascii=False) for c in corrections],
            )
            await r.ltrim("rragent:selfimprove:corrections", 0, 499)
            corrections_logged = len(corrections)
            skills_called.append("self_improving.corrections")
    except Exception as e:
        pass  # 学习反馈写入失败不影响整体

    # ── 11. 重新验证 ──
    await asyncio.sleep(3)
    post_health = await n8n_system_health()
    still_broken = post_health.get("selfimprove_reasons", [])

    # ── 12. 记录事件 + 通知 ──
    event = {
        "type": "selfimprove_done",
        "repairs": repairs,
        "failures": failures,
        "skills_called": skills_called,
        "remaining_issues": still_broken,
        "evolver_health": evolver_health,
        "corrections_logged": corrections_logged,
        "timestamp": time.time(),
    }
    await r.lpush("rragent:n8n:events", json.dumps(event, ensure_ascii=False, default=str))
    await r.ltrim("rragent:n8n:events", 0, 99)

    return {
        "ok": len(failures) == 0 and len(still_broken) == 0,
        "repairs": repairs,
        "failures": failures,
        "skills_called": skills_called,
        "remaining_issues": still_broken,
        "evolver_health": evolver_health,
        "corrections_logged": corrections_logged,
        "insight": insight_text[:200] if insight_text else "",
    }


@app.post("/api/n8n/trigger/auto-promote")
async def n8n_trigger_auto_promote(request: Request):
    """n8n 触发: 检查是否有新的高质量因子可策略化推送。

    与 promote 不同，此接口先检查是否有新的未策略化因子，
    有才执行推送，否则直接返回 skip。适合高频调用。
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    min_sharpe = body.get("min_sharpe", 1.0)
    max_per_run = body.get("max_per_run", 3)

    from agents.factor_library import FactorLibrary
    r = await get_redis()
    lib = FactorLibrary(redis_client=r)

    # 快速检查: 是否有新的未策略化因子
    raw_strats = await r.hgetall(STRATEGY_REDIS_KEY)
    strategized_fids = set()
    for v in raw_strats.values():
        try:
            s = json.loads(v)
            if s.get("factor_id"):
                strategized_fids.add(s["factor_id"])
        except Exception:
            pass

    all_factors = await lib.get_all_factors(status="active")
    candidates = [f for f in all_factors
                  if f.id not in strategized_fids and f.sharpe >= min_sharpe]
    candidates.sort(key=lambda f: f.sharpe, reverse=True)
    targets = candidates[:max_per_run]

    if not targets:
        return {"ok": True, "action": "skip", "message": "无新因子需要策略化", "new_count": 0}

    # 有新因子 — 转发给 promote 执行
    from agents.bridge_client import get_bridge_client
    from agents.factor_quality import metrics_audit
    from datetime import date, timedelta
    import asyncio

    async def _run():
        bridge = get_bridge_client()
        rd = await get_redis()
        promoted = []

        for factor in targets:
            try:
                entry_pct = 0.95
                exit_pct = 0.70
                strategy_code = factor.code + f"""

def generate_signals(matrices):
    factor = generate_factor(matrices)
    close = matrices['close']
    rank_pct = factor.rank(axis=1, pct=True)
    factor_rising = factor > factor.shift(1)
    entries = (rank_pct > {entry_pct}) & factor_rising
    ma5 = close.rolling(5).mean()
    exits = (rank_pct < {exit_pct}) | (close < ma5)
    entries = entries.fillna(False)
    exits = exits.fillna(False)
    return entries, exits
"""
                start_date = (date.today() - timedelta(days=180)).isoformat()
                end_date = date.today().isoformat()

                resp = await bridge.run_alpha(
                    alpha_code=strategy_code,
                    start_date=start_date, end_date=end_date,
                    mode="technical",
                )
                bt_metrics = resp.get("metrics", {})
                if resp.get("status") == "error":
                    continue

                qr = metrics_audit(bt_metrics)
                if not qr.passed:
                    continue

                strat_id = f"strat_{int(time.time())}_{uuid.uuid4().hex[:6]}"
                theme = factor.sub_theme or factor.theme
                strat_record = {
                    "id": strat_id,
                    "title": f"[{theme}] Sharpe={factor.sharpe:.2f}",
                    "description": f"基于因子 {factor.id} ({theme}) 自动策略化",
                    "source": "auto_promote", "factor_id": factor.id,
                    "factor_theme": theme, "factor_sharpe": factor.sharpe,
                    "code": strategy_code, "metrics": bt_metrics,
                    "params": {"entry_pct": entry_pct, "exit_pct": exit_pct},
                    "status": "draft", "synced_to_139": False,
                    "quality_grade": qr.grade, "quality_score": qr.score,
                    "created_at": time.time(),
                }
                await rd.hset(STRATEGY_REDIS_KEY, strat_id, json.dumps(strat_record, ensure_ascii=False, default=str))

                try:
                    await bridge._post("/strategy/deploy/", {
                        "strategy_id": strat_id, "code": strategy_code,
                        "filename": f"{strat_id}.py",
                    })
                    await bridge.save_strategy(
                        title=f"[因子] {theme} Sharpe={factor.sharpe:.2f}",
                        strategy_code=strategy_code,
                        backtest_metrics=bt_metrics,
                        status="APPROVE", topic=theme,
                        model_used="rrclaw-auto-promote",
                    )
                    strat_record["synced_to_139"] = True
                    strat_record["status"] = "synced"
                    await rd.hset(STRATEGY_REDIS_KEY, strat_id, json.dumps(strat_record, ensure_ascii=False, default=str))
                except Exception as e:
                    logger.warning("auto-promote: sync to 139 failed for %s: %s", strat_id, e)

                promoted.append({
                    "strategy_id": strat_id, "factor_id": factor.id,
                    "theme": theme, "sharpe": factor.sharpe,
                    "quality_grade": qr.grade,
                })
                await asyncio.sleep(2)
            except Exception as e:
                logger.error("auto-promote: factor %s failed: %s", factor.id, e)

        if promoted:
            event = {
                "type": "auto_promote_done",
                "promoted": len(promoted),
                "strategies": promoted,
                "timestamp": time.time(),
            }
            await rd.lpush("rragent:n8n:events", json.dumps(event, default=str))
            await rd.ltrim("rragent:n8n:events", 0, 99)

    asyncio.create_task(_run())
    return {
        "ok": True, "action": "promoting",
        "message": f"发现 {len(targets)} 个新因子待策略化",
        "new_count": len(targets),
        "factors": [{"id": f.id, "theme": f.theme, "sharpe": f.sharpe} for f in targets],
    }


# ── Factor Evolution & Pool Classification ──────────────────────────────────

@app.post("/api/n8n/trigger/evolve-factors")
async def n8n_trigger_evolve_factors(request: Request):
    """n8n 触发: 批量进化因子参数，连续3次失败降入低因子池。

    Body (可选):
      max_factors: int = 20   每批进化的因子数
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    max_factors = int(body.get("max_factors", 20))

    from agents.factor_evolver import run_batch_evolution
    from agents.factor_library import FactorLibrary
    from agents.bridge_client import get_bridge_client

    async def _run():
        lib = FactorLibrary(redis_client=await get_redis())
        bridge = get_bridge_client()
        rd = await get_redis()

        async def _notify(text: str):
            await rd.lpush("rragent:n8n:events",
                           json.dumps({"type": "evolve", "message": text, "timestamp": time.time()}))
            await rd.ltrim("rragent:n8n:events", 0, 99)

        result = await run_batch_evolution(lib, bridge, notify_fn=_notify, max_factors=max_factors)
        event = {"type": "evolve_done", "timestamp": time.time(), **result}
        await rd.lpush("rragent:n8n:events", json.dumps(event, default=str))
        await rd.ltrim("rragent:n8n:events", 0, 99)
        return result

    asyncio.create_task(_run())
    return {"action": "evolving", "max_factors": max_factors}


@app.post("/api/n8n/trigger/classify-factors")
async def n8n_trigger_classify_factors(request: Request):
    """n8n 触发: 对所有 active 因子重新分类到 high_pool / low_pool。

    high_pool: sharpe >= 1.5 AND win_rate >= 50%
    low_pool:  evolution_failures >= 3
    """
    from agents.factor_library import FactorLibrary

    lib = FactorLibrary(redis_client=await get_redis())
    counts = await lib.classify_all_factors()

    rd = await get_redis()
    event = {"type": "classify_done", "timestamp": time.time(), **counts}
    await rd.lpush("rragent:n8n:events", json.dumps(event))
    await rd.ltrim("rragent:n8n:events", 0, 99)

    logger.info("因子分类完成: high=%d low=%d active=%d",
                counts["high_pool"], counts["low_pool"], counts["active"])
    return {"action": "classified", **counts}


@app.post("/api/n8n/trigger/push-screener-groups")
async def n8n_trigger_push_screener_groups(request: Request):
    """n8n 触发: 将高因子池中的因子推送到 139 screener，按 pool_score(sharpe×胜率) 排序。

    Body (可选):
      top_n: int = 30        推送前 N 个高池因子
      group_size: int = 5    每组因子数 (组合成一个 screener preset)
      min_pool_score: float = 0.6  最低 pool_score 门槛
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    top_n = int(body.get("top_n", 30))
    group_size = int(body.get("group_size", 5))
    min_score = float(body.get("min_pool_score", 0.6))

    from agents.factor_library import FactorLibrary
    from agents.bridge_client import get_bridge_client

    lib = FactorLibrary(redis_client=await get_redis())
    bridge = get_bridge_client()
    rd = await get_redis()

    high_factors = await lib.get_high_pool_factors(limit=top_n)
    high_factors = [f for f in high_factors if getattr(f, "pool_score", 0) >= min_score]

    if not high_factors:
        return {"action": "push_screener", "pushed_groups": 0,
                "message": f"无符合条件的高池因子 (pool_score >= {min_score})"}

    pushed = []
    errors = []

    # 按 pool_score 降序，每 group_size 个因子组成一个 screener 因子组
    for group_idx, start in enumerate(range(0, len(high_factors), group_size)):
        group = high_factors[start:start + group_size]
        rank = group_idx + 1
        avg_sharpe = sum(f.sharpe for f in group) / len(group)
        avg_wr = sum((f.win_rate if f.win_rate <= 1 else f.win_rate / 100) for f in group) / len(group)

        # 主题分布
        themes = list(dict.fromkeys(f.theme for f in group))[:3]
        theme_label = "+".join(t.replace("_", "")[:6] for t in themes)
        group_name = f"{rank:02d}_高池因子组_{theme_label}"
        group_slug = f"high-pool-group-{rank:02d}"

        # 构建组合因子代码: 等权合并各因子的截面 rank
        code_parts = []
        for fi, fac in enumerate(group):
            fn_name = f"generate_factor_{fi}"
            renamed = fac.code.replace("def generate_factor(", f"def {fn_name}(")
            code_parts.append(renamed)

        combine_calls = "\n    ".join(
            f'scores.append({fn}(matrices).rank(axis=1, pct=True).fillna(0))'
            for fn in [f"generate_factor_{i}" for i in range(len(group))]
        )
        combined_code = "\n\n".join(code_parts) + f"""

import numpy as np
import pandas as pd

def generate_factor(matrices):
    scores = []
    {combine_calls}
    return sum(scores) / len(scores)
"""

        # 构建 screener preset payload
        preset_payload = {
            "version": "1.0",
            "execution_mode": "factor_code",
            "meta": {
                "name": group_name,
                "owner": "openclaw-high-pool",
                "trade_date": "auto",
                "pool": "high_pool",
                "rank": rank,
                "avg_sharpe": round(avg_sharpe, 3),
                "avg_win_rate": round(avg_wr, 3),
                "pool_score": round(sum(getattr(f, "pool_score", 0) for f in group) / len(group), 3),
                "factor_ids": [f.id for f in group],
            },
            "universe": {"mode": "all", "exclude": ["*ST", "ST"]},
            "timeframes": [{"id": "D1", "calendar": "trading", "lookback_bars": 60}],
            "filters": {},
            "outputs": {
                "limit": 50,
                "fields": ["ts_code", "factor_score"],
                "order_by": [{"factor": "factor_score", "direction": "desc"}],
            },
            "code": combined_code,
            "backtest_metrics": {
                "sharpe_ratio": avg_sharpe,
                "win_rate_pct": avg_wr * 100,
            },
        }

        try:
            resp = await bridge._post("/strategy/register-preset/", {
                "slug": group_slug,
                "name": group_name,
                "description": (
                    f"高池因子组 #{rank} | "
                    f"主题: {', '.join(themes)} | "
                    f"Sharpe={avg_sharpe:.2f} 胜率={avg_wr*100:.1f}% "
                    f"(pool_score={preset_payload['meta']['pool_score']:.3f})"
                ),
                "category": "factor_group",
                "payload": preset_payload,
            })
            pushed.append({
                "slug": group_slug,
                "name": group_name,
                "factors": len(group),
                "avg_sharpe": avg_sharpe,
                "avg_win_rate": avg_wr,
            })
            logger.info("screener 因子组推送成功: %s (sharpe=%.2f)", group_slug, avg_sharpe)
        except Exception as e:
            errors.append(f"{group_slug}: {e}")
            logger.error("screener 因子组推送失败: %s %s", group_slug, e)

    event = {
        "type": "push_screener_done",
        "timestamp": time.time(),
        "pushed_groups": len(pushed),
        "total_factors": len(high_factors),
        "errors": len(errors),
    }
    await rd.lpush("rragent:n8n:events", json.dumps(event))
    await rd.ltrim("rragent:n8n:events", 0, 99)

    return {
        "action": "push_screener",
        "pushed_groups": len(pushed),
        "total_factors": len(high_factors),
        "groups": pushed,
        "errors": errors,
    }


# ── Strategy Optimization (n8n) ───────────────────────

@app.post("/api/n8n/trigger/optimize-strategies")
async def n8n_trigger_optimize_strategies(request: Request):
    """n8n 触发: 策略优化 — 参数调优 + 因子叠加, 回测更优则入库。

    优化方式:
    1. 参数调优: 对已有策略尝试不同 entry_pct / exit_pct 组合
    2. 因子叠加: 从因子库选高质量因子叠加到已有策略代码上
    回测效果优于原始策略则存为新策略模板。
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    max_strategies = body.get("max_strategies", 5)
    max_variants = body.get("max_variants", 6)
    improvement_threshold = body.get("improvement_threshold", 0.05)

    from agents.factor_library import FactorLibrary
    r = await get_redis()
    lib = FactorLibrary(redis_client=r)

    raw_strats = await r.hgetall(STRATEGY_REDIS_KEY)
    strats = []
    for v in raw_strats.values():
        try:
            s = json.loads(v)
            if s.get("code") and s.get("source") in ("factor", "auto_promote") and s.get("factor_id"):
                strats.append(s)
        except Exception:
            pass

    if not strats:
        return {"ok": True, "action": "skip", "message": "无可优化策略", "optimized": 0}

    strats.sort(key=lambda x: x.get("factor_sharpe", 0), reverse=True)
    targets = strats[:max_strategies]

    all_factors = await lib.get_all_factors(status="active")
    top_factors = sorted(all_factors, key=lambda f: f.sharpe, reverse=True)[:20]

    import asyncio

    async def _run():
        from agents.bridge_client import get_bridge_client
        from agents.factor_quality import metrics_audit
        from datetime import date, timedelta
        import random

        bridge = get_bridge_client()
        rd = await get_redis()
        optimized = []

        start_date = (date.today() - timedelta(days=180)).isoformat()
        end_date = date.today().isoformat()

        for strat in targets:
            strat_id = strat["id"]
            original_metrics = strat.get("metrics", {})
            original_sharpe = float(original_metrics.get("sharpe_ratio") or original_metrics.get("sharpe", 0) or strat.get("factor_sharpe", 0))
            original_code = strat.get("code", "")
            original_params = strat.get("params", {})
            factor_id = strat.get("factor_id", "")

            if not original_code or original_sharpe <= 0:
                continue

            best_sharpe = original_sharpe
            best_code = None
            best_params = None
            best_metrics = None
            best_method = None
            variants_tested = 0

            # --- 方式1: 参数调优 ---
            orig_entry = original_params.get("entry_pct", 0.95)
            orig_exit = original_params.get("exit_pct", 0.70)
            param_variants = [
                (0.90, 0.60), (0.92, 0.65), (0.93, 0.70),
                (0.95, 0.75), (0.97, 0.65), (0.98, 0.60),
            ]
            param_variants = [(e, x) for e, x in param_variants
                              if abs(e - orig_entry) > 0.005 or abs(x - orig_exit) > 0.005]
            random.shuffle(param_variants)

            base_code = original_code.split("def generate_signals(")[0].strip()

            for entry_pct, exit_pct in param_variants[:max_variants // 2]:
                variant_code = base_code + f"""

def generate_signals(matrices):
    factor = generate_factor(matrices)
    close = matrices['close']
    rank_pct = factor.rank(axis=1, pct=True)
    factor_rising = factor > factor.shift(1)
    entries = (rank_pct > {entry_pct}) & factor_rising
    ma5 = close.rolling(5).mean()
    exits = (rank_pct < {exit_pct}) | (close < ma5)
    entries = entries.fillna(False)
    exits = exits.fillna(False)
    return entries, exits
"""
                try:
                    resp = await bridge.run_alpha(
                        alpha_code=variant_code,
                        start_date=start_date, end_date=end_date,
                        mode="technical",
                    )
                    variants_tested += 1
                    if resp.get("status") == "error":
                        continue
                    m = resp.get("metrics", {})
                    s = float(m.get("sharpe_ratio") or m.get("sharpe", 0))
                    if s > best_sharpe * (1 + improvement_threshold):
                        best_sharpe = s
                        best_code = variant_code
                        best_params = {"entry_pct": entry_pct, "exit_pct": exit_pct}
                        best_metrics = m
                        best_method = f"参数调优 entry={entry_pct} exit={exit_pct}"
                except Exception:
                    pass
                await asyncio.sleep(2)

            # --- 方式2: 因子叠加 ---
            strat_factor_ids = {factor_id} if factor_id else set()
            overlay_candidates = [f for f in top_factors if f.id not in strat_factor_ids]
            random.shuffle(overlay_candidates)

            for overlay_f in overlay_candidates[:max_variants // 2]:
                overlay_renamed = overlay_f.code.replace(
                    "def generate_factor(", "def _overlay_factor("
                )
                overlay_code = base_code + "\n\n" + overlay_renamed + """

import numpy as np
import pandas as pd

def generate_factor(matrices):
    f1 = _original_factor(matrices)
    try:
        f2 = _overlay_factor(matrices)
    except Exception:
        return f1
    s = np.stack([f1.values, f2.values], axis=0)
    combined = np.nanmean(s, axis=0)
    return pd.DataFrame(combined, index=f1.index, columns=f1.columns)
"""
                overlay_code = overlay_code.replace(
                    base_code.split("def generate_factor(")[0] + "def generate_factor(",
                    base_code.split("def generate_factor(")[0] + "def _original_factor(",
                    1,
                )
                entry_pct = best_params["entry_pct"] if best_params else orig_entry
                exit_pct = best_params["exit_pct"] if best_params else orig_exit
                overlay_code += f"""
def generate_signals(matrices):
    factor = generate_factor(matrices)
    close = matrices['close']
    rank_pct = factor.rank(axis=1, pct=True)
    factor_rising = factor > factor.shift(1)
    entries = (rank_pct > {entry_pct}) & factor_rising
    ma5 = close.rolling(5).mean()
    exits = (rank_pct < {exit_pct}) | (close < ma5)
    entries = entries.fillna(False)
    exits = exits.fillna(False)
    return entries, exits
"""
                try:
                    resp = await bridge.run_alpha(
                        alpha_code=overlay_code,
                        start_date=start_date, end_date=end_date,
                        mode="technical",
                    )
                    variants_tested += 1
                    if resp.get("status") == "error":
                        continue
                    m = resp.get("metrics", {})
                    s = float(m.get("sharpe_ratio") or m.get("sharpe", 0))
                    if s > best_sharpe * (1 + improvement_threshold):
                        best_sharpe = s
                        best_code = overlay_code
                        best_params = {"entry_pct": entry_pct, "exit_pct": exit_pct, "overlay_factor": overlay_f.id}
                        best_metrics = m
                        best_method = f"因子叠加 +{overlay_f.theme}/{overlay_f.id}"
                except Exception:
                    pass
                await asyncio.sleep(2)

            # --- 保存最优变体 ---
            if best_code and best_metrics:
                qr = metrics_audit(best_metrics)
                if not qr.passed:
                    continue

                new_id = f"strat_{int(time.time())}_{uuid.uuid4().hex[:6]}"
                theme = strat.get("factor_theme", "optimized")
                new_record = {
                    "id": new_id,
                    "title": f"[优化] {theme} Sharpe={best_sharpe:.2f}",
                    "description": f"基于 {strat_id} 优化 ({best_method}), 原Sharpe={original_sharpe:.2f}→{best_sharpe:.2f}",
                    "source": "optimize",
                    "base_strategy_id": strat_id,
                    "factor_id": factor_id,
                    "factor_theme": theme,
                    "factor_sharpe": best_sharpe,
                    "code": best_code,
                    "metrics": best_metrics,
                    "params": best_params,
                    "optimize_method": best_method,
                    "original_sharpe": original_sharpe,
                    "status": "draft",
                    "synced_to_139": False,
                    "quality_grade": qr.grade,
                    "quality_score": qr.score,
                    "created_at": time.time(),
                }
                await rd.hset(STRATEGY_REDIS_KEY, new_id, json.dumps(new_record, ensure_ascii=False, default=str))

                try:
                    await bridge._post("/strategy/deploy/", {
                        "strategy_id": new_id, "code": best_code,
                        "filename": f"{new_id}.py",
                    })
                    await bridge.save_strategy(
                        title=f"[优化] {theme} Sharpe={best_sharpe:.2f}",
                        strategy_code=best_code,
                        backtest_metrics=best_metrics,
                        status="APPROVE", topic=theme,
                        model_used="rrclaw-optimize",
                    )
                    new_record["synced_to_139"] = True
                    new_record["status"] = "synced"
                    await rd.hset(STRATEGY_REDIS_KEY, new_id, json.dumps(new_record, ensure_ascii=False, default=str))
                except Exception as e:
                    logger.warning("optimize: sync to 139 failed for %s: %s", new_id, e)

                optimized.append({
                    "new_strategy_id": new_id,
                    "base_strategy_id": strat_id,
                    "theme": theme,
                    "method": best_method,
                    "original_sharpe": original_sharpe,
                    "new_sharpe": best_sharpe,
                    "improvement": f"+{((best_sharpe/original_sharpe)-1)*100:.1f}%",
                    "quality_grade": qr.grade,
                    "variants_tested": variants_tested,
                })
                await asyncio.sleep(2)

        if optimized:
            event = {
                "type": "strategy_optimize_done",
                "optimized": len(optimized),
                "strategies": optimized,
                "timestamp": time.time(),
            }
            await rd.lpush("rragent:n8n:events", json.dumps(event, default=str))
            await rd.ltrim("rragent:n8n:events", 0, 99)
        else:
            event = {
                "type": "strategy_optimize_done",
                "optimized": 0,
                "message": f"测试了 {len(targets)} 个策略但未找到显著改进",
                "timestamp": time.time(),
            }
            await rd.lpush("rragent:n8n:events", json.dumps(event, default=str))
            await rd.ltrim("rragent:n8n:events", 0, 99)

    asyncio.create_task(_run())
    return {
        "ok": True, "action": "optimizing",
        "message": f"开始优化 {len(targets)} 个策略 (参数调优 + 因子叠加)",
        "target_count": len(targets),
        "strategies": [{"id": s["id"], "theme": s.get("factor_theme", ""), "sharpe": s.get("factor_sharpe", 0)} for s in targets],
    }


# ── Intraday Monitor API ──────────────────────────────

@app.get("/api/intraday/status")
async def api_intraday_status():
    """获取盘中监控状态（股票池 + 最近信号 + 扫描日志）"""
    from agents.intraday_pipeline import get_intraday_status
    r = await get_redis()
    return await get_intraday_status(r)


@app.post("/api/intraday/select")
async def api_intraday_select(req: Request):
    """触发盘后选股（存入 Redis 目标池）"""
    body = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    strategy = body.get("strategy", "")
    result = await send_to_orchestrator("intraday_select", strategy)
    return {"result": result}


@app.post("/api/intraday/scan")
async def api_intraday_scan(req: Request):
    """触发单次盘中扫描"""
    body = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    logic = body.get("strategy_logic", "")
    result = await send_to_orchestrator("intraday_scan", logic)
    return {"result": result}


@app.post("/api/intraday/monitor")
async def api_intraday_monitor(req: Request):
    """启动盘中持续监控"""
    body = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    logic = body.get("strategy_logic", "")
    result = await send_to_orchestrator("intraday_monitor", logic)
    return {"result": result}


@app.post("/api/intraday/stop")
async def api_intraday_stop():
    """停止盘中持续监控"""
    result = await send_to_orchestrator("intraday_stop", "")
    return {"result": result}


# ── News API ─────────────────────────────────────────

@app.get("/api/news")
async def api_news(keyword: str = "", limit: int = 20):
    """获取新闻列表 — 返回结构化新闻数据"""
    args = json.dumps({"keyword": keyword, "limit": limit}) if keyword else ""
    reply = await _send_and_wait("news", args, raw_reply=True)
    raw_data = reply.get("raw", {}) if isinstance(reply, dict) else {}
    agent_raw = raw_data.get("raw") if isinstance(raw_data, dict) else None
    items = []
    if isinstance(agent_raw, dict):
        for r in (agent_raw.get("results") or agent_raw.get("news") or []):
            items.append({
                "title": r.get("title", ""),
                "summary": r.get("summary", r.get("content", "")),
                "source": r.get("source", ""),
                "time": str(r.get("publish_time") or r.get("pub_date") or r.get("datetime") or "")[:16],
                "sentiment": r.get("sentiment_label", ""),
                "importance": r.get("importance", ""),
            })
    text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
    return {"items": items, "raw": text if not items else ""}


@app.get("/api/news/summary")
async def api_news_summary():
    """AI 生成新闻摘要"""
    result = await send_to_orchestrator("news", json.dumps({"mode": "summary"}))
    return {"summary": result}


# ── Monitor API ──────────────────────────────────────

@app.get("/api/monitor/alerts")
async def api_monitor_alerts():
    """获取 Prometheus 活跃告警"""
    result = await send_to_orchestrator("alerts")
    return {"text": result}


@app.get("/api/monitor/targets")
async def api_monitor_targets():
    """获取 Prometheus 目标健康状态"""
    result = await send_to_orchestrator("targets")
    return {"text": result}


@app.get("/api/monitor/grafana")
async def api_monitor_grafana():
    """获取 Grafana 告警"""
    result = await send_to_orchestrator("grafana_alerts")
    return {"text": result}


@app.post("/api/monitor/patrol")
async def api_monitor_patrol():
    """一键巡检"""
    result = await send_to_orchestrator("patrol")
    return {"text": result}


@app.post("/api/monitor/silence")
async def api_monitor_silence(request: Request):
    """静默告警"""
    body = await request.json()
    matcher = body.get("matcher", "")
    duration = body.get("duration", "2h")
    result = await send_to_orchestrator("silence", json.dumps({"matcher": matcher, "duration": duration}))
    return {"text": result}


# ── AI Tools API ─────────────────────────────────────

@app.post("/api/tools/translate")
async def api_tools_translate(request: Request):
    """AI 翻译"""
    body = await request.json()
    text = body.get("text", "")
    target_lang = body.get("target", "en")
    if not text:
        raise HTTPException(400, "missing text")
    result = await send_to_orchestrator("translate", json.dumps({"text": text, "target": target_lang}))
    return {"result": result}


@app.post("/api/tools/write")
async def api_tools_write(request: Request):
    """AI 写作"""
    body = await request.json()
    prompt = body.get("prompt", "")
    style = body.get("style", "professional")
    if not prompt:
        raise HTTPException(400, "missing prompt")
    result = await send_to_orchestrator("write", json.dumps({"prompt": prompt, "style": style}))
    return {"result": result}


@app.post("/api/tools/code")
async def api_tools_code(request: Request):
    """AI 代码生成/分析"""
    body = await request.json()
    prompt = body.get("prompt", "")
    lang = body.get("language", "python")
    if not prompt:
        raise HTTPException(400, "missing prompt")
    result = await send_to_orchestrator("code", json.dumps({"prompt": prompt, "language": lang}))
    return {"result": result}


@app.post("/api/tools/calc")
async def api_tools_calc(request: Request):
    """AI 计算器 / 数学"""
    body = await request.json()
    expr = body.get("expression", "")
    if not expr:
        raise HTTPException(400, "missing expression")
    result = await send_to_orchestrator("calc", expr)
    return {"result": result}


@app.post("/api/tools/websearch")
async def api_tools_websearch(request: Request):
    """Web 搜索"""
    body = await request.json()
    query = body.get("query", "")
    if not query:
        raise HTTPException(400, "missing query")
    result = await send_to_orchestrator("websearch", query)
    return {"result": result}


# ── Apple API ────────────────────────────────────────

@app.get("/api/apple/calendar")
async def api_apple_calendar(days: int = 7):
    """获取日历事件"""
    result = await send_to_orchestrator("calendar", str(days))
    return {"text": result}


@app.post("/api/apple/calendar")
async def api_apple_calendar_add(request: Request):
    """新增日历事件"""
    body = await request.json()
    result = await send_to_orchestrator("cal_add", json.dumps(body))
    return {"text": result}


@app.get("/api/apple/reminders")
async def api_apple_reminders(request: Request):
    """获取提醒事项 — 支持 filter 和 list 参数"""
    filter_type = request.query_params.get("filter", "upcoming")
    list_name = request.query_params.get("list", "")
    params = {"filter": filter_type}
    if list_name:
        params["list_name"] = list_name
    result = await send_to_orchestrator("remind_list", json.dumps(params))
    return {"text": result}


@app.post("/api/apple/reminders")
async def api_apple_reminder_add(request: Request):
    """新增提醒 — 支持 title, notes, url, due, priority, list_name, flagged"""
    body = await request.json()
    result = await send_to_orchestrator("remind", json.dumps(body))
    return {"text": result}


@app.put("/api/apple/reminders")
async def api_apple_reminder_edit(request: Request):
    """编辑提醒"""
    body = await request.json()
    result = await send_to_orchestrator("remind_edit", json.dumps(body))
    return {"text": result}


@app.delete("/api/apple/reminders")
async def api_apple_reminder_delete(request: Request):
    """删除提醒"""
    body = await request.json()
    result = await send_to_orchestrator("remind_del", json.dumps(body))
    return {"text": result}


@app.get("/api/apple/reminders/lists")
async def api_apple_reminder_lists():
    """获取提醒列表"""
    result = await send_to_orchestrator("remind_lists", "")
    return {"text": result}


@app.get("/api/apple/notes")
async def api_apple_notes(keyword: str = ""):
    """搜索备忘录"""
    result = await send_to_orchestrator("note_search", keyword)
    return {"text": result}


@app.post("/api/apple/notes")
async def api_apple_note_create(request: Request):
    """创建备忘录"""
    body = await request.json()
    result = await send_to_orchestrator("note", json.dumps(body))
    return {"text": result}


@app.get("/api/apple/sysinfo")
async def api_apple_sysinfo():
    """系统信息"""
    result = await send_to_orchestrator("sysinfo")
    return {"text": result}


@app.post("/api/apple/notify")
async def api_apple_notify(request: Request):
    """发送系统通知"""
    body = await request.json()
    result = await send_to_orchestrator("notify", json.dumps(body))
    return {"text": result}


@app.post("/api/apple/music")
async def api_apple_music(request: Request):
    """Music 控制"""
    body = await request.json()
    result = await send_to_orchestrator("music", json.dumps(body))
    return {"text": result}


@app.get("/api/apple/music/status")
async def api_apple_music_status():
    """Music 状态"""
    result = await send_to_orchestrator("music", json.dumps({"action": "status"}))
    return {"text": result}


@app.get("/api/apple/shortcuts")
async def api_apple_shortcuts():
    """快捷指令列表"""
    result = await send_to_orchestrator("shortcut_list")
    return {"text": result}


@app.post("/api/apple/shortcuts/run")
async def api_apple_shortcut_run(request: Request):
    """运行快捷指令"""
    body = await request.json()
    result = await send_to_orchestrator("shortcut", json.dumps(body))
    return {"text": result}


@app.get("/api/apple/clipboard")
async def api_apple_clipboard():
    """读取剪贴板"""
    result = await send_to_orchestrator("clip")
    return {"text": result}


@app.post("/api/apple/clipboard")
async def api_apple_clipboard_write(request: Request):
    """写入剪贴板"""
    body = await request.json()
    result = await send_to_orchestrator("clip_set", json.dumps(body))
    return {"text": result}


@app.post("/api/apple/finder")
async def api_apple_finder(request: Request):
    """Finder 打开路径"""
    body = await request.json()
    result = await send_to_orchestrator("finder", json.dumps(body))
    return {"text": result}


@app.post("/api/apple/alarm")
async def api_apple_alarm_set(request: Request):
    """设置闹钟"""
    body = await request.json()
    result = await send_to_orchestrator("alarm", json.dumps(body))
    return {"text": result}


@app.get("/api/apple/alarm/list")
async def api_apple_alarm_list(request: Request):
    """列出闹钟"""
    result = await send_to_orchestrator("alarm_list", "")
    return {"text": result}


@app.post("/api/apple/alarm/cancel")
async def api_apple_alarm_cancel(request: Request):
    """取消闹钟"""
    body = await request.json()
    result = await send_to_orchestrator("alarm_cancel", json.dumps(body))
    return {"text": result}


@app.post("/api/apple/timer")
async def api_apple_timer(request: Request):
    """设置定时器"""
    body = await request.json()
    result = await send_to_orchestrator("timer", json.dumps(body))
    return {"text": result}


@app.post("/api/apple/volume")
async def api_apple_volume(request: Request):
    """音量控制"""
    body = await request.json()
    result = await send_to_orchestrator("volume", json.dumps(body))
    return {"text": result}


@app.post("/api/apple/brightness")
async def api_apple_brightness(request: Request):
    """亮度控制"""
    body = await request.json()
    result = await send_to_orchestrator("brightness", json.dumps(body))
    return {"text": result}


@app.post("/api/apple/dnd")
async def api_apple_dnd(request: Request):
    """勿扰模式"""
    body = await request.json()
    result = await send_to_orchestrator("dnd", json.dumps(body))
    return {"text": result}


@app.get("/api/apple/contacts")
async def api_apple_contacts(request: Request):
    """通讯录搜索"""
    kw = request.query_params.get("keyword", "")
    result = await send_to_orchestrator("contact", kw)
    return {"text": result}


# ── Dev / SSH API ────────────────────────────────────

@app.post("/api/dev/ssh")
async def api_dev_ssh(request: Request):
    """远程 SSH 命令 — 支持 host 参数指定目标主机"""
    body = await request.json()
    cmd = body.get("command", "")
    host = body.get("host", "")
    if not cmd:
        raise HTTPException(400, "missing command")
    args = json.dumps({"command": cmd, "host": host}) if host else cmd
    result = await send_to_orchestrator("ssh", args)
    return {"result": result}


@app.post("/api/dev/claude")
async def api_dev_claude(request: Request):
    """Claude 编程任务 — SSE 流式返回进度和结果"""
    body = await request.json()
    task = body.get("task", "")
    if not task:
        raise HTTPException(400, "missing task")
    work_dir = body.get("work_dir", "")
    model = body.get("model", "")
    provider = body.get("provider", "")
    params = {"prompt": task}
    if work_dir:
        params["work_dir"] = work_dir
    if model:
        params["model"] = model
    if provider:
        params["provider"] = provider
    args = json.dumps(params)

    user = getattr(request.state, "user", None)
    uid = f"web_{user['sub']}" if user else "webchat_default"
    user_name = user.get("name", user.get("sub", "")) if user else ""

    async def _stream():
        msg_id = uuid.uuid4().hex[:12]
        reply_channel = f"rragent:reply:{msg_id}"
        r = await get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(reply_channel)

        orch_params = {"command": "claude", "args": args, "reply_channel": reply_channel, "uid": uid}
        if user_name:
            orch_params["user_name"] = user_name
        msg = json.dumps({
            "id": msg_id, "sender": "webchat", "target": "orchestrator",
            "action": "route", "params": orch_params, "timestamp": time.time(),
        })
        await r.publish("rragent:orchestrator", msg)

        yield f"data: {json.dumps({'type': 'start', 'content': 'Claude Code 处理中...'})}\n\n"

        deadline = time.time() + LONG_TIMEOUT
        try:
            while time.time() < deadline:
                raw = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0), timeout=5.0
                )
                if raw is None:
                    continue
                data = json.loads(raw["data"])
                evt_type = data.get("type", "")
                if evt_type == "progress":
                    yield f"data: {json.dumps({'type': 'progress', 'content': data.get('text', '')})}\n\n"
                elif evt_type == "done":
                    text = data.get("text", "")
                    raw_result = data.get("raw")
                    yield f"data: {json.dumps({'type': 'done', 'content': text, 'raw': raw_result}, default=str)}\n\n"
                    break
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Timeout waiting for result'})}\n\n"
        finally:
            await pubsub.unsubscribe(reply_channel)

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/dev/local")
async def api_dev_local(request: Request):
    """本地命令"""
    body = await request.json()
    cmd = body.get("command", "")
    if not cmd:
        raise HTTPException(400, "missing command")
    result = await send_to_orchestrator("dev", cmd)
    return {"result": result}


@app.get("/api/dev/hosts")
async def api_dev_hosts():
    """获取可用主机和动态发现的 Git 仓库列表"""
    reply = await _send_and_wait("dev_status", "", raw_reply=True)
    raw = reply.get("raw", {}) if isinstance(reply, dict) else {}
    return {
        "hosts": raw.get("hosts", {}),
        "repos": raw.get("repos", {}),
        "claude": raw.get("claude_code", {}),
        "scan_dirs": raw.get("scan_dirs", []),
    }


@app.post("/api/dev/host/test")
async def api_dev_host_test(request: Request):
    """测试主机连接"""
    body = await request.json()
    host = body.get("host", "")
    if not host:
        raise HTTPException(400, "missing host")
    reply = await _send_and_wait("host_test", json.dumps({"host": host}), raw_reply=True)
    raw = reply.get("raw", {}) if isinstance(reply, dict) else {}
    text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
    return {
        "result": raw.get("text") or text,
        "connected": raw.get("connected", False),
        "host": host,
    }


@app.post("/api/dev/git")
async def api_dev_git(request: Request):
    """Git 操作 — action: status/pull/log/diff/sync"""
    body = await request.json()
    action = body.get("action", "status")
    repo = body.get("repo", "")
    valid_actions = {"status": "git_status", "pull": "git_pull", "log": "git_log", "diff": "git_diff", "sync": "git_sync"}
    if action not in valid_actions:
        raise HTTPException(400, f"invalid action: {action}")
    args = json.dumps({"repo": repo, "path": body.get("path", ""), "branch": body.get("branch", ""), "deploy_to": body.get("deploy_to", ""), "count": body.get("count", 15)})
    result = await send_to_orchestrator(valid_actions[action], args)
    return {"result": result}


# ── Skills / Agents / Factors API ────────────────────

@app.get("/api/skills")
async def api_skills():
    """返回所有 Agent 的 skills 清单（结构化 JSON）"""
    import yaml as _yaml
    skills_dir = Path(__file__).parent / "agents" / "skills"
    result = []
    if skills_dir.exists():
        for yf in sorted(skills_dir.glob("*_skills.yaml")):
            try:
                data = _yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
                agent = data.get("agent", yf.stem.replace("_skills", ""))
                skills = []
                for s in data.get("skills", []):
                    if not isinstance(s, dict):
                        continue
                    skills.append({
                        "name": s.get("name", ""),
                        "description": s.get("description", ""),
                        "trigger": s.get("trigger", ""),
                        "params": {k: v for k, v in (s.get("params") or {}).items()},
                    })
                result.append({
                    "agent": agent,
                    "description": data.get("description", ""),
                    "skills": skills,
                })
            except Exception:
                continue
    return {"agents": result}


@app.get("/api/agents/info")
async def api_agents_info():
    """返回所有 Agent 运行状态 + 目标 + 技能列表"""
    import yaml as _yaml
    r = await get_redis()
    hb_raw = await r.hgetall("rragent:heartbeats")
    skills_dir = Path(__file__).parent / "agents" / "skills"
    agent_meta = {}
    if skills_dir.exists():
        for yf in sorted(skills_dir.glob("*_skills.yaml")):
            try:
                data = _yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
                name = data.get("agent", yf.stem.replace("_skills", ""))
                agent_meta[name] = {
                    "description": data.get("description", ""),
                    "skills": [s.get("name", "?") for s in data.get("skills", []) if isinstance(s, dict)],
                }
            except Exception:
                continue
    agents = {}
    now = time.time()
    for name in sorted(set(list(hb_raw.keys()) + list(agent_meta.keys()))):
        hb = {}
        status = "offline"
        pid = 0
        age = -1
        if name in hb_raw:
            try:
                hb = json.loads(hb_raw[name])
                age = now - hb.get("ts", 0)
                status = "online" if age < 30 else ("slow" if age < 60 else "offline")
                pid = hb.get("pid", 0)
            except Exception:
                pass
        meta = agent_meta.get(name, {})
        agents[name] = {
            "status": status,
            "pid": pid,
            "age": round(age) if age >= 0 else -1,
            "description": meta.get("description", ""),
            "skills": meta.get("skills", hb.get("skills", [])),
        }
    return {"agents": agents}


@app.get("/api/reflect/stats")
async def api_reflect_stats():
    """反思引擎状态"""
    result = await send_to_orchestrator("reflect_stats", "", uid="webchat_api")
    return {"text": result}


@app.get("/api/reflect/insight")
async def api_reflect_insight():
    """今日反思洞察"""
    result = await send_to_orchestrator("reflect", "", uid="webchat_api")
    return {"text": result}


# ── System Diagnostics API ───────────────────────────

@app.get("/api/diagnostics")
async def api_diagnostics():
    """综合系统诊断：agent 健康、Redis 连通、LLM 状态"""
    r = await get_redis()
    diag = {"ts": time.time(), "agents": {}, "redis": {}, "llm": {}, "memory": {}}

    hb_raw = await r.hgetall("rragent:heartbeats")
    for name, raw in hb_raw.items():
        try:
            hb = json.loads(raw)
            age = time.time() - hb.get("ts", 0)
            diag["agents"][name] = {"status": "ok" if age < 30 else "warn" if age < 60 else "dead", "age_s": round(age), "pid": hb.get("pid", 0)}
        except Exception:
            diag["agents"][name] = {"status": "error"}

    try:
        pong = await r.ping()
        info = await r.info("memory")
        diag["redis"] = {"ping": pong, "used_memory_human": info.get("used_memory_human", "?"), "connected_clients": (await r.info("clients")).get("connected_clients", 0)}
    except Exception as e:
        diag["redis"] = {"ping": False, "error": str(e)}

    try:
        from agents.llm_router import get_llm_router
        router = get_llm_router()
        status = router.get_status()
        diag["llm"] = status
    except Exception as e:
        diag["llm"] = {"error": str(e)}

    try:
        mem_keys = 0
        async for _ in r.scan_iter("rragent:session:*:history"):
            mem_keys += 1
        diag["memory"] = {"session_count": mem_keys}
    except Exception:
        pass

    return diag


# ── Chat History & Daily Log ───────────────────────────

HISTORY_KEY = "rragent:chat_history"
DAILY_LOG_KEY = "rragent:daily_log"
HISTORY_MAX = 500


async def save_chat_message(role: str, content: str, view: str = "chat", target: str = ""):
    """保存一条对话记录到 Redis List + 当日日志"""
    r = await get_redis()
    ts = time.time()
    today = time.strftime("%Y-%m-%d")
    entry = json.dumps({
        "role": role, "content": content[:4000],
        "view": view, "ts": ts, "date": today,
        "target": target,
    }, ensure_ascii=False)
    await r.lpush(HISTORY_KEY, entry)
    await r.ltrim(HISTORY_KEY, 0, HISTORY_MAX - 1)
    await r.lpush(f"{DAILY_LOG_KEY}:{today}", entry)
    await r.expire(f"{DAILY_LOG_KEY}:{today}", 86400 * 30)


@app.get("/api/history")
async def api_history(limit: int = 100, offset: int = 0):
    """获取对话历史"""
    r = await get_redis()
    raw = await r.lrange(HISTORY_KEY, offset, offset + limit - 1)
    messages = []
    for item in raw:
        try:
            messages.append(json.loads(item))
        except Exception:
            pass
    messages.reverse()
    return {"messages": messages, "total": await r.llen(HISTORY_KEY)}


@app.delete("/api/history")
async def api_history_clear():
    """清空对话历史"""
    r = await get_redis()
    await r.delete(HISTORY_KEY)
    return {"ok": True}


@app.get("/api/daily-log")
async def api_daily_log(date: str = ""):
    """获取指定日期的工作日志 (对话 + 命令 + 任务)"""
    if not date:
        date = time.strftime("%Y-%m-%d")
    r = await get_redis()

    chat_raw = await r.lrange(f"{DAILY_LOG_KEY}:{date}", 0, -1)
    chats = []
    for item in chat_raw:
        try:
            chats.append(json.loads(item))
        except Exception:
            pass
    chats.reverse()

    tasks_raw = await r.zrangebyscore("rragent:tasks", "-inf", "+inf")
    tasks = []
    for tid in tasks_raw:
        tdata = await r.get(f"rragent:task:{tid}")
        if tdata:
            try:
                t = json.loads(tdata)
                created = t.get("created_at", "")
                if isinstance(created, str) and created.startswith(date):
                    tasks.append(t)
                elif isinstance(created, (int, float)):
                    if time.strftime("%Y-%m-%d", time.localtime(created)) == date:
                        tasks.append(t)
            except Exception:
                pass

    return {
        "date": date,
        "chats": chats,
        "chat_count": len(chats),
        "tasks": tasks,
        "task_count": len(tasks),
    }


@app.get("/api/daily-log/dates")
async def api_daily_log_dates():
    """获取有日志的日期列表"""
    r = await get_redis()
    keys = []
    async for key in r.scan_iter(f"{DAILY_LOG_KEY}:*"):
        d = key.replace(f"{DAILY_LOG_KEY}:", "")
        keys.append(d)
    keys.sort(reverse=True)
    return {"dates": keys[:30]}


# ── LLM Usage Monitor API ──────────────────────────────

@app.get("/api/usage")
async def api_usage(days: int = 30, recent: int = 100):
    """获取 LLM API 使用数据（用于监控面板）"""
    try:
        from agents.llm_router import get_llm_router
        router = get_llm_router()
        usage = router.get_usage_data(days=min(days, 90), recent_limit=min(recent, 500))
        status = router.get_status()
        return {**usage, "router_status": status}
    except Exception as e:
        logger.error(f"Usage API error: {e}")
        return {"error": str(e), "daily": [], "recent_calls": [], "totals": {}, "by_provider": {}, "by_task_type": {}}


@app.delete("/api/usage")
async def api_usage_clear():
    """清空 LLM 使用记录"""
    r = await get_redis()
    from agents.llm_router import LLM_USAGE_KEY, LLM_USAGE_DAILY_PREFIX
    await r.delete(LLM_USAGE_KEY)
    keys = []
    async for key in r.scan_iter(f"{LLM_USAGE_DAILY_PREFIX}*"):
        keys.append(key)
    if keys:
        await r.delete(*keys)
    return {"ok": True, "deleted_keys": len(keys) + 1}



# ── Plan Trace API (Viking 分层路由执行轨迹) ────────────────

PLAN_LOG_PREFIX_API = "rrclaw:plan_log:"
PLAN_HISTORY_KEY_API = "rrclaw:plan_history"


@app.get("/api/plan/history")
async def api_plan_history(limit: int = 20):
    """返回最近 N 条执行计划的摘要列表"""
    r = await get_redis()
    ids = await r.lrange(PLAN_HISTORY_KEY_API, 0, min(limit, 200) - 1)
    summaries = []
    for plan_id in ids:
        raw = await r.get(f"{PLAN_LOG_PREFIX_API}{plan_id}")
        if not raw:
            continue
        try:
            rec = json.loads(raw)
            summaries.append({
                "id": rec.get("id"),
                "uid": rec.get("uid"),
                "input": rec.get("input", "")[:80],
                "route_level": rec.get("route_level"),
                "steps": len(rec.get("steps", [])),
                "latency_ms": rec.get("latency_ms", {}).get("total"),
                "ts": rec.get("ts"),
            })
        except Exception:
            continue
    return {"plans": summaries}


@app.get("/api/plan/{plan_id}")
async def api_plan_detail(plan_id: str):
    """返回单条执行计划的完整轨迹"""
    r = await get_redis()
    raw = await r.get(f"{PLAN_LOG_PREFIX_API}{plan_id}")
    if not raw:
        raise HTTPException(404, "plan not found")
    return json.loads(raw)


MONITOR_PATH = Path(__file__).parent / "usage_monitor.html"


@app.get("/monitor", response_class=HTMLResponse)
async def serve_monitor():
    if MONITOR_PATH.exists():
        return MONITOR_PATH.read_text(encoding="utf-8")
    return "<h1>Monitor frontend not found</h1>"


# ── autoresearch-mlx ───────────────────────────────────

_AUTORESEARCH_DIR = "/Users/zayl/OpenClaw-Universe/autoresearch-mlx"
_AUTORESEARCH_RUNNING: dict = {}  # track running experiment


@app.post("/api/n8n/trigger/autoresearch")
async def n8n_trigger_autoresearch(request: Request):
    """n8n 触发: autoresearch-mlx 自主 LLM 训练实验。

    启动 Claude Code agent 执行 program.md 中定义的自主实验循环。
    每次实验: 修改 train.py → 训练 5 分钟 → 评估 val_bpb → 保留/丢弃。

    参数:
        max_experiments: 最大实验次数 (默认 5)
        timeout_minutes: 总超时 (默认 60)
    """
    try:
        return await _do_autoresearch(request)
    except Exception as e:
        import traceback
        logger.error(f"autoresearch error: {traceback.format_exc()}")
        return {"ok": False, "error": str(e)[:500]}


async def _do_autoresearch(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    max_experiments = body.get("max_experiments", 5)
    timeout_minutes = body.get("timeout_minutes", 60)
    model = body.get("model", "haiku")

    import subprocess as _sp

    # 检查是否已有实验在运行
    if _AUTORESEARCH_RUNNING.get("pid"):
        pid = _AUTORESEARCH_RUNNING["pid"]
        # Check process is alive AND not a zombie
        import subprocess as _sp2
        ps_check = _sp2.run(["ps", "-p", str(pid), "-o", "state="], capture_output=True, text=True)
        state = ps_check.stdout.strip()
        if ps_check.returncode == 0 and state and "Z" not in state:
            return {
                "ok": True, "action": "already_running",
                "message": f"autoresearch 实验已在运行 (PID {pid})",
                "started_at": _AUTORESEARCH_RUNNING.get("started_at"),
            }
        else:
            _AUTORESEARCH_RUNNING.clear()

    # 检查目录和数据
    run_script = os.path.join(_AUTORESEARCH_DIR, "_run_experiment.sh")
    for check_path, label in [
        (os.path.join(_AUTORESEARCH_DIR, "train.py"), "train.py"),
        (run_script, "_run_experiment.sh"),
        ("/Users/zayl/.cache/autoresearch/data", "training data"),
    ]:
        chk = _sp.run(["sudo", "-u", "zayl", "test", "-e", check_path], capture_output=True)
        if chk.returncode != 0:
            return {"ok": False, "error": f"autoresearch-mlx: {label} not found at {check_path}"}

    tag = time.strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(_AUTORESEARCH_DIR, "logs")
    log_file = os.path.join(log_dir, f"{tag}.log")

    _sp.run(["sudo", "-u", "zayl", "mkdir", "-p", log_dir], capture_output=True)

    # 启动 _run_experiment.sh (内部调用 Claude Code CLI 进行自主实验)
    import getpass as _gp
    shell_cmd = f"bash {run_script} {max_experiments} {timeout_minutes} {model}"
    if _gp.getuser() == "zayl":
        cmd = ["bash", "-c", shell_cmd]
    else:
        cmd = ["sudo", "-u", "zayl", "-H", "-i", "bash", "-c", shell_cmd]

    proc = _sp.Popen(
        cmd,
        stdout=_sp.DEVNULL,
        stderr=_sp.DEVNULL,
        cwd="/tmp",
        start_new_session=True,
    )

    _AUTORESEARCH_RUNNING.update({
        "pid": proc.pid,
        "started_at": time.time(),
        "tag": tag,
        "log_file": log_file,
        "max_experiments": max_experiments,
    })

    # 记录事件
    r = await get_redis()
    event = {
        "type": "autoresearch_started",
        "tag": tag,
        "pid": proc.pid,
        "max_experiments": max_experiments,
        "timestamp": time.time(),
    }
    await r.lpush("rragent:n8n:events", json.dumps(event, ensure_ascii=False))
    await r.ltrim("rragent:n8n:events", 0, 99)

    return {
        "ok": True,
        "action": "started",
        "message": f"autoresearch 实验已启动 (model={model}, {max_experiments} 轮, 超时 {timeout_minutes}min)",
        "pid": proc.pid,
        "tag": tag,
        "log_file": log_file,
    }


@app.get("/api/n8n/autoresearch/status")
async def n8n_autoresearch_status(request: Request):
    """获取 autoresearch 实验状态和最近结果。"""
    result = {"running": False, "results": []}

    # 检查运行状态
    if _AUTORESEARCH_RUNNING.get("pid"):
        try:
            os.kill(_AUTORESEARCH_RUNNING["pid"], 0)
            result["running"] = True
            result["pid"] = _AUTORESEARCH_RUNNING["pid"]
            result["started_at"] = _AUTORESEARCH_RUNNING.get("started_at")
            result["tag"] = _AUTORESEARCH_RUNNING.get("tag")
        except OSError:
            _AUTORESEARCH_RUNNING.clear()

    import subprocess as _sp
    import re

    # 读取 results.tsv (via sudo -u zayl)
    tsv_path = os.path.join(_AUTORESEARCH_DIR, "results.tsv")
    tsv_out = _sp.run(["sudo", "-u", "zayl", "cat", tsv_path], capture_output=True, text=True)
    if tsv_out.returncode == 0 and tsv_out.stdout.strip():
        lines = tsv_out.stdout.strip().split("\n")
        if len(lines) > 1:
            headers = lines[0].strip().split("\t")
            for line in lines[1:]:
                fields = line.strip().split("\t")
                if len(fields) >= len(headers):
                    result["results"].append(dict(zip(headers, fields)))

    # 读取最新日志
    log_file = _AUTORESEARCH_RUNNING.get("log_file")
    if log_file:
        tail_out = _sp.run(["sudo", "-u", "zayl", "tail", "-20", log_file], capture_output=True, text=True)
        if tail_out.returncode == 0:
            result["log_tail"] = tail_out.stdout

    # best/latest val_bpb from results.tsv (more reliable than parsing logs)
    kept = [r for r in result["results"] if r.get("status") == "keep" and r.get("val_bpb", "0") != "0.000000"]
    if kept:
        bpb_values = [float(r["val_bpb"]) for r in kept]
        result["best_val_bpb"] = min(bpb_values)
        result["latest_val_bpb"] = bpb_values[-1]

    # latest log tail for progress monitoring
    log_dir = os.path.join(_AUTORESEARCH_DIR, "logs")
    ls_out = _sp.run(
        ["sudo", "-u", "zayl", "bash", "-c", f"ls -t {log_dir}/*.log 2>/dev/null | head -1"],
        capture_output=True, text=True,
    )
    latest_log = ls_out.stdout.strip()
    if latest_log and not log_file:
        tail_out = _sp.run(["sudo", "-u", "zayl", "tail", "-20", latest_log], capture_output=True, text=True)
        if tail_out.returncode == 0:
            result["log_tail"] = tail_out.stdout

    return result


# ── Serve Frontend ─────────────────────────────────────

FRONTEND_PATH = Path(__file__).parent / "webchat_frontend.html"


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if FRONTEND_PATH.exists():
        return FRONTEND_PATH.read_text(encoding="utf-8")
    return "<h1>Frontend not found</h1>"


def main():
    import uvicorn
    logger.info(f"Starting OpenClaw API on {WEBCHAT_HOST}:{WEBCHAT_PORT}")
    uvicorn.run(app, host=WEBCHAT_HOST, port=WEBCHAT_PORT, log_level="info")


if __name__ == "__main__":
    main()
