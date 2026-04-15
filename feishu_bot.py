"""
Feishu/Lark Bot — 飞书渠道前端 (Webhook 模式 + 应用机器人)

发送消息: 通过自定义机器人 Webhook URL，直接 POST 即可
接收消息: 需配置飞书应用机器人 (APP_ID + 事件订阅)

与 Telegram Bot 互为冗余渠道，通过 Redis 与 Orchestrator 通信。
指令集与 Telegram 保持逻辑同步。
"""

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

import asyncio
import json
import logging
import signal
import subprocess
import time
import uuid
from typing import Optional

import aiohttp
import redis.asyncio as aioredis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FeishuBot] %(levelname)s: %(message)s",
)
logger = logging.getLogger("feishu_bot")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REPLY_TIMEOUT = int(os.getenv("REPLY_TIMEOUT", "300"))
ALLOWED_USERS = os.getenv("FEISHU_ALLOWED_USERS", "")

CHANNEL_NAME = "feishu"
HEARTBEAT_KEY = "rragent:channel_heartbeats"
HEARTBEAT_INTERVAL = 10

_redis: aioredis.Redis | None = None
_shutting_down = False

_start_time = time.time()
_last_send_ok = 0.0
_last_send_fail = 0.0
_consecutive_failures = 0


class FeishuConfig:
    webhook_url: str = ""
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""


_cfg = FeishuConfig()


def _load_config():
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feishu.env")
    load_dotenv(env_path)
    load_dotenv()

    _cfg.webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
    _cfg.app_id = os.getenv("FEISHU_APP_ID", "")
    _cfg.app_secret = os.getenv("FEISHU_APP_SECRET", "")
    _cfg.encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
    _cfg.verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")

    global ALLOWED_USERS, REDIS_URL
    ALLOWED_USERS = os.getenv("FEISHU_ALLOWED_USERS", ALLOWED_USERS)
    REDIS_URL = os.getenv("REDIS_URL", REDIS_URL)


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def check_auth(user_id: str) -> bool:
    if not ALLOWED_USERS:
        return True
    allowed = [x.strip() for x in ALLOWED_USERS.split(",") if x.strip()]
    return user_id in allowed


# ── Webhook 发送 ─────────────────────────────────────

TOPIC_EMOJI = {
    "market": "📊",
    "query": "🔍",
    "strategy": "📈",
    "system": "⚙️",
}


async def _webhook_send(text: str, topic: str = ""):
    """通过 Webhook URL 发送消息到飞书群"""
    if not _cfg.webhook_url:
        logger.warning("FEISHU_WEBHOOK_URL not configured, skip send")
        return

    prefix = TOPIC_EMOJI.get(topic, "")
    if prefix:
        text = f"{prefix} [{topic.upper()}]\n{text}"

    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    async with aiohttp.ClientSession() as session:
        for chunk in chunks:
            payload = {
                "msg_type": "text",
                "content": {"text": chunk},
            }
            async with session.post(_cfg.webhook_url, json=payload) as resp:
                result = await resp.json()
                if result.get("code") != 0:
                    raise RuntimeError(f"Feishu webhook error: {result}")


async def _webhook_send_rich(title: str, content_lines: list[str], topic: str = ""):
    """发送富文本消息（标题 + 多行内容）"""
    if not _cfg.webhook_url:
        return

    elements = []
    for line in content_lines:
        elements.append([{"tag": "text", "text": line}])

    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"{TOPIC_EMOJI.get(topic, '')} {title}",
                    "content": elements,
                }
            }
        },
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_cfg.webhook_url, json=payload) as resp:
            result = await resp.json()
            if result.get("code") != 0:
                raise RuntimeError(f"Feishu rich send error: {result}")


async def _webhook_send_card(title: str, text: str, topic: str = "", color: str = "blue"):
    """发送互动卡片消息"""
    if not _cfg.webhook_url:
        return

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{TOPIC_EMOJI.get(topic, '')} {title}",
                },
                "template": color,
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": text,
                }
            ],
        },
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_cfg.webhook_url, json=card) as resp:
            result = await resp.json()
            if result.get("code") != 0:
                raise RuntimeError(f"Feishu card send error: {result}")


# ── 发送统一入口 ────────────────────────────────────

async def _send_topic_msg(text: str, topic: str = "market", msg_id: str = ""):
    global _last_send_ok, _last_send_fail, _consecutive_failures

    try:
        if len(text) > 500:
            title_line = text.split("\n")[0][:40]
            await _webhook_send_card(title_line, text, topic=topic)
        else:
            await _webhook_send(text, topic=topic)

        _last_send_ok = time.time()
        _consecutive_failures = 0
        if msg_id:
            await _ack_delivery(msg_id)
    except Exception as e:
        _last_send_fail = time.time()
        _consecutive_failures += 1
        logger.error(f"Feishu send failed: {e}")
        try:
            await _webhook_send(text, topic=topic)
            _last_send_ok = time.time()
            _consecutive_failures = 0
            if msg_id:
                await _ack_delivery(msg_id)
        except Exception:
            raise


# ── 投递确认 ─────────────────────────────────────────

async def _ack_delivery(msg_id: str):
    try:
        r = await get_redis()
        await r.publish("rragent:notify:ack", json.dumps({
            "channel": CHANNEL_NAME,
            "msg_id": msg_id,
            "ts": time.time(),
        }))
    except Exception:
        pass


# ── Redis → Orchestrator 通信 ────────────────────────

async def send_to_orchestrator(command: str, args: str, user_id: str = "",
                               user_name: str = "", on_progress=None) -> str:
    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"

    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(reply_channel)

    params = {
        "command": command,
        "args": args,
        "reply_channel": reply_channel,
        "uid": f"feishu_{user_id}" if user_id else "feishu_unknown",
    }
    if user_name:
        params["user_name"] = user_name

    msg = json.dumps({
        "id": msg_id,
        "sender": "feishu",
        "target": "orchestrator",
        "action": "route",
        "params": params,
        "timestamp": time.time(),
    })
    await r.publish("rragent:orchestrator", msg)

    try:
        async def _wait():
            async for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                data = json.loads(raw["data"])
                msg_type = data.get("type", "")
                if msg_type == "progress":
                    if on_progress:
                        try:
                            await on_progress(data.get("text", ""))
                        except Exception:
                            pass
                    continue
                return data.get("text", json.dumps(data, ensure_ascii=False, indent=2))

        return await asyncio.wait_for(_wait(), timeout=REPLY_TIMEOUT)
    except asyncio.TimeoutError:
        return "⏱️ 超时，Agent 未在规定时间内回复"
    finally:
        await pubsub.unsubscribe(reply_channel)


# ── 心跳上报 ─────────────────────────────────────────

async def _heartbeat_loop():
    while not _shutting_down:
        try:
            r = await get_redis()
            hb = json.dumps({
                "pid": os.getpid(),
                "ts": time.time(),
                "status": "healthy" if _consecutive_failures < 3 else "degraded",
                "last_send_ok": _last_send_ok,
                "last_send_fail": _last_send_fail,
                "consecutive_failures": _consecutive_failures,
                "uptime": time.time() - _start_time,
                "mode": "webhook" if _cfg.webhook_url else "app",
            })
            await r.hset(HEARTBEAT_KEY, CHANNEL_NAME, hb)
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


# ── 通知监听 ─────────────────────────────────────────

NOTIFY_TOPIC_MAP = {
    "market": "market",
    "query": "query",
    "strategy": "strategy",
    "system": "system",
}


async def _notify_listener():
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("rragent:notify:feishu", "rragent:notify:all")
    logger.info("Notify listener started (feishu + all)")

    while not _shutting_down:
        try:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            if msg["type"] != "message":
                continue
            logger.info(f"Notify received: ch={msg.get('channel')} len={len(msg.get('data',''))}")
            data = json.loads(msg["data"])
            text = data.get("text", "")
            if not text:
                continue
            topic = NOTIFY_TOPIC_MAP.get(data.get("topic", ""), "market")
            notify_id = data.get("id", "")
            logger.info(f"Sending notify: topic={topic} text={text[:60]}...")
            await _send_topic_msg(text, topic, msg_id=notify_id)
            logger.info("Notify sent OK")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Notify error: {e}")
            await asyncio.sleep(1)

    await pubsub.unsubscribe()
    logger.info("Notify listener stopped")


# ── 渠道互检 (检查 TG) ───────────────────────────────

async def _peer_health_check():
    while not _shutting_down:
        try:
            r = await get_redis()
            raw = await r.hget(HEARTBEAT_KEY, "telegram")
            if raw:
                hb = json.loads(raw)
                age = time.time() - hb.get("ts", 0)
                if age > 60:
                    logger.warning(f"Telegram channel offline ({age:.0f}s), attempting restart")
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "launchctl", "kickstart", "-k",
                        "gui/503/com.openclaw.telegram-bot",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
        except Exception as e:
            logger.debug(f"Peer health check error: {e}")
        await asyncio.sleep(30)


# ── 命令与指令集 (与 Telegram 同步) ────────────────────

_CODE_OUTPUT_CMDS = {"shell", "ssh", "local", "sysinfo", "clip"}

COMMANDS = [
    # Market
    "zt", "lb", "bk", "hot", "summary",
    # Analysis / General
    "ask", "q", "translate", "summarize",
    "write", "code", "calc", "websearch",
    # Dev
    "dev", "ssh", "claude", "deploy", "local",
    # Browser / Desktop
    "task", "url", "browse",
    "shell", "app", "type", "key", "click", "windows", "do",
    # News / Strategy
    "news", "strategy",
    # Backtest / Quant / Digger
    "backtest", "bt_cache", "ledger", "strategy_list", "strategy_detail",
    "quant", "quant_optimize", "digger", "digger_status",
    # Apple
    "calendar", "cal_add", "cal_del",
    "remind", "remind_list", "remind_done", "remind_edit", "remind_del", "remind_lists",
    "note", "note_search", "contact", "mail",
    "notify", "search", "music", "shortcut", "shortcut_list",
    "sysinfo", "clip", "clip_set", "finder",
    "volume", "app_ctrl", "brightness", "dnd",
    "alarm", "alarm_list", "alarm_cancel", "timer",
    # Monitor
    "alerts", "targets", "alert_history", "grafana_alerts", "patrol", "silence",
    # Intraday
    "intraday_select", "intraday_monitor", "intraday_status",
    "intraday_scan", "intraday_stop", "intraday_pool",
    # System diagnostics
    "llm_status", "embed_status", "data_source_status",
    "soul_check", "memory_health", "memory_hygiene",
    # Reflection Engine
    "reflect", "reflect_weekly", "reflect_stats",
    # System introspection
    "skills", "agents", "factor_list", "factor_detail",
    # Channel / Mgmt
    "channel",
    "jobs", "task_new", "task_status", "task_cancel", "task_list",
]

HELP_TEXT = (
    "🤖 ReachRich Claw Multi-Agent Bot (飞书)\n\n"
    "📊 行情: /zt /lb /bk /hot /summary\n"
    "🧠 分析: /ask <问题> /q <通用问答>\n"
    "📈 策略: /strategy <问题> /backtest <代码>\n"
    "🔧 工具: /translate /write /code /calc /websearch\n"
    "💻 开发: /dev /ssh /claude /deploy /local\n"
    "🌐 浏览: /task <任务> /url <网址> /browse <指令>\n"
    "🖥️ 桌面: /screen /shell /app /do <指令>\n"
    "📰 新闻: /news [关键词]\n"
    "🍎 Apple: /calendar /remind /note /mail /music /sysinfo /alarm /timer\n"
    "🔍 监控: /alerts /patrol /targets /grafana_alerts\n"
    "⛏️ 量化: /quant /digger /digger_status /ledger\n"
    "📡 盘中: /intraday_status /intraday_select /intraday_scan\n"
    "⏰ 定时: /alert <分钟> /stop\n"
    "🔬 诊断: /llm_status /memory_health /soul_check\n"
    "🔄 反思: /reflect /reflect_weekly /reflect_stats\n"
    "📋 自省: /skills /agents /factor_list /factor_detail <id>\n"
    "ℹ️ 状态: /status /model /channel\n\n"
    "💬 也可以直接发文字消息，AI 自动识别意图并路由"
)


# ── 定时行情推送 ─────────────────────────────────────

_periodic_tasks: dict[str, asyncio.Task] = {}


async def _alert_loop(chat_id: str, interval_min: int, reply_fn):
    """每隔 interval_min 分钟通过 Orchestrator 获取行情摘要并推送"""
    round_num = 0
    while True:
        round_num += 1
        try:
            result = await send_to_orchestrator("summary", "", chat_id)
            header = f"📡 行情推送 #{round_num}\n\n"
            await reply_fn(header + result[:3900])
        except asyncio.CancelledError:
            await reply_fn(f"🛑 行情推送已停止（共 {round_num - 1} 轮）")
            return
        except Exception as e:
            logger.error(f"Alert push error: {e}")
            try:
                await reply_fn(f"⚠️ 推送异常: {e}")
            except Exception:
                pass
        try:
            await asyncio.sleep(interval_min * 60)
        except asyncio.CancelledError:
            await reply_fn(f"🛑 行情推送已停止（共 {round_num} 轮）")
            return


# ── 截图能力 ─────────────────────────────────────────

async def _do_screenshot() -> str:
    """截取 Mac 屏幕，返回结果文本"""
    tmp_path = "/tmp/openclaw_screen_feishu.jpg"
    try:
        proc = await asyncio.create_subprocess_exec(
            "screencapture", "-x", "-C", "-t", "jpg", tmp_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        if proc.returncode != 0 or not os.path.exists(tmp_path):
            return "❌ 截图失败（可能需要屏幕录制权限）"
        file_size = os.path.getsize(tmp_path)
        if file_size > 5 * 1024 * 1024:
            resize = await asyncio.create_subprocess_exec(
                "sips", "--resampleWidth", "1920", tmp_path, "--out", tmp_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(resize.wait(), timeout=10)
        return f"📸 截图已保存 ({file_size // 1024}KB)"
    except asyncio.TimeoutError:
        return "⏱️ 截图超时"
    except Exception as e:
        return f"❌ 截图错误: {e}"
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── 飞书应用机器人事件接收 ─────────────────────────────

async def _app_webhook_server():
    """
    应用机器人模式：HTTP 服务器接收飞书事件回调。
    仅在配置了 APP_ID 时启动。
    """
    if not _cfg.app_id:
        logger.info("App bot not configured (no FEISHU_APP_ID), webhook server skipped")
        return

    _tenant_token: Optional[str] = None
    _tenant_token_expires: float = 0.0

    async def _get_tenant_token() -> str:
        nonlocal _tenant_token, _tenant_token_expires
        now = time.time()
        if _tenant_token and now < _tenant_token_expires - 60:
            return _tenant_token

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": _cfg.app_id, "app_secret": _cfg.app_secret},
            )
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu token error: {data}")
            _tenant_token = data["tenant_access_token"]
            _tenant_token_expires = now + data.get("expire", 7200)
            return _tenant_token

    async def _reply_in_group(chat_id: str, text: str):
        token = await _get_tenant_token()
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        async with aiohttp.ClientSession() as session:
            for chunk in chunks:
                await session.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    json={
                        "receive_id": chat_id,
                        "receive_id_type": "chat_id",
                        "msg_type": "text",
                        "content": json.dumps({"text": chunk}),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )

    async def _reply_card(chat_id: str, title: str, content: str, color: str = "blue"):
        """发送卡片消息到群聊"""
        token = await _get_tenant_token()
        card = {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [{"tag": "markdown", "content": content}],
        }
        async with aiohttp.ClientSession() as session:
            await session.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                json={
                    "receive_id": chat_id,
                    "receive_id_type": "chat_id",
                    "msg_type": "interactive",
                    "content": json.dumps(card),
                },
                headers={"Authorization": f"Bearer {token}"},
            )

    from aiohttp import web

    async def handle_event(request: web.Request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"code": -1})

        if "challenge" in body:
            return web.json_response({"challenge": body["challenge"]})

        header = body.get("header", {})
        if _cfg.verification_token and header.get("token") != _cfg.verification_token:
            return web.json_response({"code": -1})

        event = body.get("event", {})
        msg_obj = event.get("message", {})
        msg_type = msg_obj.get("message_type", "")
        sender = event.get("sender", {})
        sender_id = sender.get("sender_id", {}).get("open_id", "")
        sender_name = sender.get("sender_id", {}).get("name", "")

        if msg_type == "text":
            content = json.loads(msg_obj.get("content", "{}"))
            text = content.get("text", "").strip()
            chat_id = msg_obj.get("chat_id", "")

            if not text:
                return web.json_response({"code": 0})

            # auth check
            if not check_auth(sender_id):
                if chat_id:
                    try:
                        await _reply_in_group(chat_id, "⛔ 未授权用户")
                    except Exception:
                        pass
                return web.json_response({"code": 0})

            # parse command
            if text.startswith("/"):
                parts = text[1:].split(maxsplit=1)
                cmd, args = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
            else:
                cmd, args = "chat", text

            # build reply function for this chat
            async def _reply(reply_text: str):
                if chat_id:
                    try:
                        await _reply_in_group(chat_id, reply_text)
                    except Exception as e:
                        logger.error(f"Reply error: {e}")

            # handle special commands
            if cmd in ("start", "help"):
                if chat_id:
                    try:
                        await _reply_card(chat_id, "🤖 ReachRich Claw", HELP_TEXT, "blue")
                    except Exception:
                        await _reply(HELP_TEXT)
                return web.json_response({"code": 0})

            if cmd == "status":
                try:
                    result = await send_to_orchestrator("status", "", sender_id, user_name=sender_name)
                    await _reply(result)
                except Exception as e:
                    await _reply(f"❌ {e}")
                return web.json_response({"code": 0})

            if cmd == "model":
                provider = os.getenv("LLM_PROVIDER", "deepseek")
                model = os.getenv("LLM_MODEL", "")
                await _reply(f"🧠 AI: {provider} / {model}\n(修改 feishu.env 后重启切换)")
                return web.json_response({"code": 0})

            if cmd == "channel":
                try:
                    r = await get_redis()
                    all_hb = await r.hgetall(HEARTBEAT_KEY)
                    lines = ["📡 渠道状态:"]
                    now = time.time()
                    for ch_name, raw in sorted(all_hb.items()):
                        try:
                            hb = json.loads(raw)
                            age = now - hb.get("ts", 0)
                            status = hb.get("status", "unknown")
                            uptime_h = hb.get("uptime", 0) / 3600
                            icon = "✅" if age < 30 else ("⚠️" if age < 120 else "❌")
                            lines.append(
                                f"  {icon} {ch_name}: {status} "
                                f"(心跳 {age:.0f}s前, 运行 {uptime_h:.1f}h, "
                                f"失败 {hb.get('consecutive_failures', 0)}次)"
                            )
                        except Exception:
                            lines.append(f"  ❓ {ch_name}: 解析失败")
                    await _reply("\n".join(lines))
                except Exception as e:
                    await _reply(f"❌ {e}")
                return web.json_response({"code": 0})

            if cmd == "screen":
                result = await _do_screenshot()
                await _reply(result)
                return web.json_response({"code": 0})

            if cmd == "alert":
                if not args:
                    await _reply(
                        "用法: /alert <间隔分钟>\n\n"
                        "例: /alert 5 — 每 5 分钟推送行情摘要\n"
                        "停止: /stop"
                    )
                    return web.json_response({"code": 0})
                try:
                    interval_min = int(args.strip())
                    if interval_min < 1:
                        raise ValueError
                except ValueError:
                    await _reply("间隔必须是 ≥1 的整数")
                    return web.json_response({"code": 0})
                key = chat_id or sender_id
                if key in _periodic_tasks and not _periodic_tasks[key].done():
                    _periodic_tasks[key].cancel()
                    await asyncio.sleep(0.3)
                await _reply(f"✅ 行情推送已启动 — 每 {interval_min} 分钟\n发送 /stop 停止")
                task = asyncio.create_task(_alert_loop(key, interval_min, _reply))
                _periodic_tasks[key] = task
                return web.json_response({"code": 0})

            if cmd == "stop":
                key = chat_id or sender_id
                if key in _periodic_tasks and not _periodic_tasks[key].done():
                    _periodic_tasks[key].cancel()
                    del _periodic_tasks[key]
                    await _reply("🛑 正在停止定时任务...")
                else:
                    await _reply("没有正在运行的定时任务")
                return web.json_response({"code": 0})

            # generic command handler (same as Telegram make_handler)
            if cmd in COMMANDS or cmd == "chat":
                async def _on_progress(progress_text: str):
                    if progress_text:
                        await _reply(progress_text)

                try:
                    result = await send_to_orchestrator(
                        cmd, args, sender_id,
                        user_name=sender_name, on_progress=_on_progress)
                    if cmd in _CODE_OUTPUT_CMDS and not result.startswith("❌"):
                        await _reply(f"```\n{result}\n```")
                    else:
                        await _reply(result)
                except Exception as e:
                    await _reply(f"❌ 错误: {e}")
            else:
                # unknown command, treat as chat
                try:
                    result = await send_to_orchestrator("chat", text, sender_id, user_name=sender_name)
                    await _reply(result)
                except Exception as e:
                    await _reply(f"❌ 错误: {e}")

        return web.json_response({"code": 0})

    app = web.Application()
    app.router.add_post("/feishu/event", handle_event)

    port = int(os.getenv("FEISHU_WEBHOOK_PORT", "9090"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Feishu app webhook listening on :{port}")

    while not _shutting_down:
        await asyncio.sleep(1)

    await runner.cleanup()


# ── 主入口 ───────────────────────────────────────────

async def _run():
    _load_config()

    if not _cfg.webhook_url and not _cfg.app_id:
        logger.error("Neither FEISHU_WEBHOOK_URL nor FEISHU_APP_ID set, exiting")
        return

    mode = "webhook" if _cfg.webhook_url else "app-only"
    logger.info(f"Feishu Bot starting in [{mode}] mode")

    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.info(f"Received {sig.name}, shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    tasks = [
        asyncio.create_task(_heartbeat_loop(), name="heartbeat"),
        asyncio.create_task(_notify_listener(), name="notify"),
        asyncio.create_task(_peer_health_check(), name="peer_check"),
        asyncio.create_task(_app_webhook_server(), name="app_webhook"),
    ]

    logger.info("Feishu Bot (asyncio-managed) running")
    await stop_event.wait()

    global _shutting_down
    _shutting_down = True
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    if _redis:
        await _redis.aclose()

    logger.info("Feishu Bot stopped cleanly")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
