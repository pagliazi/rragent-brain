"""
Telegram Bot — 薄前端（asyncio 自管理 + 话题分流 + 渠道心跳）
通过 Redis 与 Orchestrator 通信，支持 Forum Topics 消息分区。
采用手动 asyncio 事件循环管理，避免 run_polling() 与 launchd SIGTERM 冲突。
"""

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

import asyncio
import json
import logging
import os
import signal
import subprocess
import time

import redis.asyncio as aioredis
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TelegramBot] %(levelname)s: %(message)s",
)
logger = logging.getLogger("telegram_bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS = os.getenv("TELEGRAM_ALLOWED_USERS", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REPLY_TIMEOUT = int(os.getenv("REPLY_TIMEOUT", "300"))

GROUP_ID = os.getenv("TELEGRAM_GROUP_ID", "")
TOPIC_IDS = {
    "market": os.getenv("TOPIC_MARKET", ""),
    "query": os.getenv("TOPIC_QUERY", ""),
    "strategy": os.getenv("TOPIC_STRATEGY", ""),
    "system": os.getenv("TOPIC_SYSTEM", ""),
}

CHANNEL_NAME = "telegram"
HEARTBEAT_KEY = "rragent:channel_heartbeats"
HEARTBEAT_INTERVAL = 10

_redis: aioredis.Redis | None = None
_shutting_down = False


def _topic_id(name: str) -> int | None:
    val = TOPIC_IDS.get(name, "")
    return int(val) if val else None


def _group_chat_id() -> int | None:
    return int(GROUP_ID) if GROUP_ID else None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def check_auth(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    allowed = [int(x.strip()) for x in ALLOWED_USERS.split(",") if x.strip()]
    return user_id in allowed


# ── 心跳上报 ──────────────────────────────────────────

_start_time = time.time()
_last_send_ok = 0.0
_last_send_fail = 0.0
_consecutive_failures = 0


async def _heartbeat_loop():
    """每 10 秒上报渠道心跳到 Redis"""
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
            })
            await r.hset(HEARTBEAT_KEY, CHANNEL_NAME, hb)
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def _mark_send_ok():
    global _last_send_ok, _consecutive_failures
    _last_send_ok = time.time()
    _consecutive_failures = 0


async def _mark_send_fail():
    global _last_send_fail, _consecutive_failures
    _last_send_fail = time.time()
    _consecutive_failures += 1


# ── 投递确认 ──────────────────────────────────────────

async def _ack_delivery(msg_id: str):
    """向 Redis 发布投递确认"""
    try:
        r = await get_redis()
        await r.publish("rragent:notify:ack", json.dumps({
            "channel": CHANNEL_NAME,
            "msg_id": msg_id,
            "ts": time.time(),
        }))
    except Exception:
        pass


# ── Redis → Orchestrator 通信 ─────────────────────────

async def send_to_orchestrator(command: str, args: str, chat_id: int,
                               user_name: str = "", on_progress=None) -> str:
    import uuid

    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"

    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(reply_channel)

    params = {
        "command": command,
        "args": args,
        "reply_channel": reply_channel,
        "uid": f"tg_{chat_id}",
    }
    if user_name:
        params["user_name"] = user_name

    msg = json.dumps({
        "id": msg_id,
        "sender": "telegram",
        "target": "orchestrator",
        "action": "route",
        "params": params,
        "timestamp": time.time(),
    })
    await r.publish("rragent:orchestrator", msg)

    try:
        async def _wait_reply():
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

        return await asyncio.wait_for(_wait_reply(), timeout=REPLY_TIMEOUT)
    except asyncio.TimeoutError:
        return "⏱️ 超时，Agent 未在规定时间内回复"
    finally:
        await pubsub.unsubscribe(reply_channel)


# ── 消息发送 (带投递追踪) ─────────────────────────────

async def _send_topic_msg(app: Application, text: str, topic: str = "market", msg_id: str = ""):
    gid = _group_chat_id()
    tid = _topic_id(topic)
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]

    try:
        if gid and tid:
            for chunk in chunks:
                await app.bot.send_message(chat_id=gid, text=chunk, message_thread_id=tid)
        elif gid:
            for chunk in chunks:
                await app.bot.send_message(chat_id=gid, text=chunk)
        elif ALLOWED_USERS:
            for uid_str in ALLOWED_USERS.split(","):
                uid_str = uid_str.strip()
                if uid_str:
                    for chunk in chunks:
                        await app.bot.send_message(chat_id=int(uid_str), text=chunk)
        else:
            logger.warning("No group or allowed users configured")
            return

        await _mark_send_ok()
        if msg_id:
            await _ack_delivery(msg_id)
    except Exception as e:
        await _mark_send_fail()
        logger.error(f"Send failed: {e}")
        raise


NOTIFY_TOPIC_MAP = {
    "market": "market",
    "strategy": "strategy",
    "system": "system",
}


# ── 通知监听 ──────────────────────────────────────────

async def _notify_listener(app: Application):
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("rragent:notify:telegram", "rragent:notify:all")
    logger.info("Notify listener started (telegram + all)")

    while not _shutting_down:
        try:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            if msg["type"] != "message":
                continue

            data = json.loads(msg["data"])
            text = data.get("text", "")
            if not text:
                continue
            topic = NOTIFY_TOPIC_MAP.get(data.get("topic", ""), "market")
            notify_id = data.get("id", "")
            await _send_topic_msg(app, text, topic, msg_id=notify_id)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Notify error: {e}")
            await asyncio.sleep(1)

    await pubsub.unsubscribe()
    logger.info("Notify listener stopped")


# ── 渠道互检 ──────────────────────────────────────────

async def _peer_health_check():
    """检查飞书渠道心跳，若离线尝试 kickstart"""
    while not _shutting_down:
        try:
            r = await get_redis()
            raw = await r.hget(HEARTBEAT_KEY, "feishu")
            if raw:
                hb = json.loads(raw)
                age = time.time() - hb.get("ts", 0)
                if age > 60:
                    logger.warning(f"Feishu channel offline ({age:.0f}s), attempting restart")
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "launchctl", "kickstart", "-k",
                        "gui/503/com.openclaw.feishu-bot",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
        except Exception as e:
            logger.debug(f"Peer health check error: {e}")
        await asyncio.sleep(30)


# ── Markdown 安全回复 ─────────────────────────────────

async def _reply_md(message, text: str):
    """尝试用 Markdown 发送，失败则回退 plain text"""
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        try:
            await message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await message.reply_text(chunk)


# ── 本地截图能力 (从 telegram_agent 吸收) ──────────────

async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """截取 Mac 屏幕并直接发送图片到 TG"""
    if not check_auth(update.effective_user.id):
        await update.message.reply_text("⛔ 未授权")
        return
    tmp_path = "/tmp/openclaw_screen.jpg"
    try:
        proc = await asyncio.create_subprocess_exec(
            "screencapture", "-x", "-C", "-t", "jpg", tmp_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        if proc.returncode != 0 or not os.path.exists(tmp_path):
            await update.message.reply_text("❌ 截图失败（可能需要屏幕录制权限）")
            return
        file_size = os.path.getsize(tmp_path)
        if file_size > 5 * 1024 * 1024:
            resize = await asyncio.create_subprocess_exec(
                "sips", "--resampleWidth", "1920", tmp_path, "--out", tmp_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(resize.wait(), timeout=10)
        with open(tmp_path, "rb") as f:
            await update.message.reply_photo(
                photo=f, caption=f"📸 ({file_size // 1024}KB)",
                read_timeout=60, write_timeout=60,
            )
    except asyncio.TimeoutError:
        await update.message.reply_text("⏱️ 截图超时")
    except Exception as e:
        await update.message.reply_text(f"❌ 截图错误: {e}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── 定时行情推送 (从 telegram_agent 吸收) ──────────────

_periodic_tasks: dict[int, asyncio.Task] = {}


async def _alert_loop(chat_id: int, interval_min: int, bot):
    """每隔 interval_min 分钟通过 Orchestrator 获取行情摘要并推送"""
    round_num = 0
    while True:
        round_num += 1
        try:
            result = await send_to_orchestrator("summary", "", chat_id)
            header = f"📡 行情推送 #{round_num}\n\n"
            await bot.send_message(chat_id, header + result[:3900])
        except asyncio.CancelledError:
            await bot.send_message(chat_id, f"🛑 行情推送已停止（共 {round_num - 1} 轮）")
            return
        except Exception as e:
            logger.error(f"Alert push error: {e}")
            try:
                await bot.send_message(chat_id, f"⚠️ 推送异常: {e}")
            except Exception:
                pass
        try:
            await asyncio.sleep(interval_min * 60)
        except asyncio.CancelledError:
            await bot.send_message(chat_id, f"🛑 行情推送已停止（共 {round_num} 轮）")
            return


async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """定时推送行情摘要"""
    if not check_auth(update.effective_user.id):
        await update.message.reply_text("⛔ 未授权")
        return
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(
            "用法: /alert <间隔分钟>\n\n"
            "例: /alert 5 — 每 5 分钟推送行情摘要\n"
            "停止: /stop"
        )
        return
    try:
        interval_min = int(context.args[0])
        if interval_min < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("间隔必须是 ≥1 的整数")
        return
    if chat_id in _periodic_tasks and not _periodic_tasks[chat_id].done():
        _periodic_tasks[chat_id].cancel()
        await asyncio.sleep(0.3)
    await update.message.reply_text(f"✅ 行情推送已启动 — 每 {interval_min} 分钟\n发送 /stop 停止")
    task = asyncio.create_task(_alert_loop(chat_id, interval_min, context.bot))
    _periodic_tasks[chat_id] = task


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """停止当前聊天的定时任务"""
    if not check_auth(update.effective_user.id):
        await update.message.reply_text("⛔ 未授权")
        return
    chat_id = update.effective_chat.id
    if chat_id in _periodic_tasks and not _periodic_tasks[chat_id].done():
        _periodic_tasks[chat_id].cancel()
        del _periodic_tasks[chat_id]
        await update.message.reply_text("🛑 正在停止定时任务...")
    else:
        await update.message.reply_text("没有正在运行的定时任务")


# ── Telegram 命令处理器 ────────────────────────────────

_CODE_OUTPUT_CMDS = {"shell", "ssh", "local", "sysinfo", "clip"}


def make_handler(cmd_name: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not check_auth(update.effective_user.id):
            await update.message.reply_text("⛔ 未授权")
            return
        args = " ".join(context.args) if context.args else ""
        tg_user = update.effective_user
        uname = tg_user.full_name or tg_user.first_name or tg_user.username or ""

        async def _on_progress(text: str):
            if text:
                await update.message.reply_text(text)

        try:
            result = await send_to_orchestrator(
                cmd_name, args, update.effective_chat.id,
                user_name=uname, on_progress=_on_progress)
            if cmd_name in _CODE_OUTPUT_CMDS and not result.startswith("❌"):
                await _reply_md(update.message, f"```\n{result}\n```")
            else:
                for i in range(0, len(result), 4000):
                    await update.message.reply_text(result[i:i + 4000])
        except Exception as e:
            await update.message.reply_text(f"❌ 错误: {e}")
    return handler


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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🤖 ReachRich Claw Multi-Agent Bot\n"
        f"Your ID: {uid}\n\n"
        f"📊 行情: /zt /lb /bk /hot /summary\n"
        f"🧠 分析: /ask <问题> /q <通用问答>\n"
        f"📈 策略: /strategy <问题> /backtest <代码>\n"
        f"🔧 工具: /translate /write /code /calc /websearch\n"
        f"💻 开发: /dev /ssh /claude /deploy /local\n"
        f"🌐 浏览: /task <任务> /url <网址> /browse <指令>\n"
        f"🖥️ 桌面: /screen /shell /app /do <指令>\n"
        f"📰 新闻: /news [关键词]\n"
        f"🍎 Apple: /calendar /remind /note /mail /music /sysinfo /alarm /timer\n"
        f"🔍 监控: /alerts /patrol /targets /grafana_alerts\n"
        f"⛏️ 量化: /quant /digger /digger_status /ledger\n"
        f"📡 盘中: /intraday_status /intraday_select /intraday_scan\n"
        f"⏰ 定时: /alert <分钟> /stop\n"
        f"🔬 诊断: /llm_status /memory_health /soul_check\n"
        f"🔄 反思: /reflect /reflect_weekly /reflect_stats\n"
        f"📋 自省: /skills /agents /factor_list /factor_detail <id>\n"
        f"ℹ️ 状态: /status /model /channel\n\n"
        f"💬 也可以直接发文字消息，AI 自动识别意图并路由"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id):
        await update.message.reply_text("⛔ 未授权")
        return
    try:
        result = await send_to_orchestrator("status", "", update.effective_chat.id)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id):
        await update.message.reply_text("⛔ 未授权")
        return
    provider = os.getenv("LLM_PROVIDER", "deepseek")
    model = os.getenv("LLM_MODEL", "")
    await update.message.reply_text(
        f"🧠 AI: {provider} / {model}\n"
        f"(修改 telegram.env 后重启切换)"
    )


async def cmd_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看渠道健康状态"""
    if not check_auth(update.effective_user.id):
        await update.message.reply_text("⛔ 未授权")
        return
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
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


# ── 消息去重 (防 Telegram webhook 超时重试) ────────────
_recent_messages: dict[str, float] = {}  # key -> timestamp
_DEDUP_WINDOW = 30  # 30秒内相同消息视为重复


def _dedup_key(chat_id: int, text: str) -> str:
    return f"{chat_id}:{hash(text)}"


def _is_duplicate(chat_id: int, text: str) -> bool:
    key = _dedup_key(chat_id, text)
    now = time.time()
    # 清理过期条目
    expired = [k for k, ts in _recent_messages.items() if now - ts > _DEDUP_WINDOW * 2]
    for k in expired:
        _recent_messages.pop(k, None)
    if key in _recent_messages and now - _recent_messages[key] < _DEDUP_WINDOW:
        return True
    _recent_messages[key] = now
    return False


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id):
        return
    text = update.message.text
    if not text or text.startswith("/"):
        return

    # 消息去重: 防止 Telegram 超时重试导致重复处理
    chat_id = update.effective_chat.id
    if _is_duplicate(chat_id, text):
        logger.info(f"Duplicate message ignored: {text[:40]}")
        return

    tg_user = update.effective_user
    uname = tg_user.full_name or tg_user.first_name or tg_user.username or ""

    async def _on_progress(progress_text: str):
        if progress_text:
            try:
                await update.message.reply_text(progress_text)
            except Exception:
                pass

    try:
        result = await send_to_orchestrator(
            "chat", text, chat_id,
            user_name=uname, on_progress=_on_progress)
        for i in range(0, len(result), 4000):
            await update.message.reply_text(result[i:i + 4000])
    except Exception as e:
        await update.message.reply_text(f"❌ 错误: {e}")


# ── 主入口: asyncio 自管理 ────────────────────────────

async def _run():
    """手动管理 Application 生命周期，不依赖 run_polling()"""
    from dotenv import load_dotenv

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram.env")
    load_dotenv(env_path)
    load_dotenv()

    global TOKEN, ALLOWED_USERS, REDIS_URL, GROUP_ID
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", TOKEN)
    ALLOWED_USERS = os.getenv("TELEGRAM_ALLOWED_USERS", ALLOWED_USERS)
    REDIS_URL = os.getenv("REDIS_URL", REDIS_URL)
    GROUP_ID = os.getenv("TELEGRAM_GROUP_ID", GROUP_ID)

    for k in TOPIC_IDS:
        TOPIC_IDS[k] = os.getenv(f"TOPIC_{k.upper()}", TOPIC_IDS[k])

    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("channel", cmd_channel))
    app.add_handler(CommandHandler("screen", cmd_screen))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("stop", cmd_stop))
    for cmd in COMMANDS:
        app.add_handler(CommandHandler(cmd, make_handler(cmd)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    logger.info("Telegram Bot (asyncio-managed) running")

    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.info(f"Received {sig.name}, initiating graceful shutdown...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    tasks = [
        asyncio.create_task(_notify_listener(app), name="notify_listener"),
        asyncio.create_task(_heartbeat_loop(), name="heartbeat"),
        asyncio.create_task(_peer_health_check(), name="peer_health"),
    ]

    await stop_event.wait()

    global _shutting_down
    _shutting_down = True
    logger.info("Shutting down background tasks...")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("Stopping updater...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

    if _redis:
        await _redis.aclose()

    logger.info("Telegram Bot stopped cleanly")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
