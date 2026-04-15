"""
OpenClaw Dashboard — Gradio 5 多 Tab 控制台
Tabs: 总览 | 对话 | 行情 | 任务 | 系统

启动: .venv/bin/python webchat_app.py
"""

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webchat.env"))
load_dotenv()

import gradio as gr
import redis.asyncio as aioredis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Dashboard] %(levelname)s: %(message)s")
logger = logging.getLogger("dashboard")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REPLY_TIMEOUT = int(os.getenv("REPLY_TIMEOUT", "120"))
WEBCHAT_PORT = int(os.getenv("WEBCHAT_PORT", "7789"))
WEBCHAT_HOST = os.getenv("WEBCHAT_HOST", "0.0.0.0")
WEBCHAT_AUTH_USER = os.getenv("WEBCHAT_AUTH_USER", "")
WEBCHAT_AUTH_PASS = os.getenv("WEBCHAT_AUTH_PASS", "")

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def send_to_orchestrator(command: str, args: str = "") -> str:
    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:reply:{msg_id}"
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(reply_channel)

    msg = json.dumps({
        "id": msg_id,
        "sender": "webchat",
        "target": "orchestrator",
        "action": "route",
        "params": {"command": command, "args": args, "reply_channel": reply_channel},
        "timestamp": time.time(),
    })
    await r.publish("rragent:orchestrator", msg)

    try:
        async def _wait():
            async for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                data = json.loads(raw["data"])
                return data.get("text", json.dumps(data, ensure_ascii=False, indent=2))
        return await asyncio.wait_for(_wait(), timeout=REPLY_TIMEOUT)
    except asyncio.TimeoutError:
        return "⏱️ 超时，Agent 未在规定时间内回复"
    except Exception as e:
        return f"❌ 错误: {e}"
    finally:
        await pubsub.unsubscribe(reply_channel)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tab 1: 总览
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AGENT_LABELS = {
    "orchestrator": ("🎯", "编排器"),
    "market": ("📊", "行情"),
    "analysis": ("🔬", "分析"),
    "news": ("📰", "新闻"),
    "strategist": ("📈", "策略"),
    "dev": ("💻", "开发"),
    "browser": ("🌐", "浏览器"),
    "desktop": ("🖥️", "桌面"),
}

CHANNEL_LABELS = {
    "telegram": ("✈️", "Telegram"),
    "feishu": ("🐦", "飞书"),
}


async def refresh_overview():
    try:
        r = await get_redis()

        hb_raw = await r.hgetall("rragent:heartbeat")
        agents_md = "### Agent 状态\n\n"
        agents_md += "| 状态 | Agent | PID | 心跳 |\n|------|-------|-----|------|\n"

        for name, (emoji, label) in AGENT_LABELS.items():
            raw = hb_raw.get(name, "")
            if raw:
                try:
                    hb = json.loads(raw)
                    age = time.time() - hb.get("ts", 0)
                    pid = hb.get("pid", "?")
                    if age < 30:
                        status = "🟢"
                    elif age < 60:
                        status = "🟡"
                    else:
                        status = "🔴"
                    agents_md += f"| {status} | {emoji} {label} | {pid} | {age:.0f}s ago |\n"
                except Exception:
                    agents_md += f"| ⚪ | {emoji} {label} | - | 解析错误 |\n"
            else:
                agents_md += f"| ⚪ | {emoji} {label} | - | 无心跳 |\n"

        ch_raw = await r.hgetall("rragent:channel_heartbeats")
        channels_md = "\n### 渠道状态\n\n"
        channels_md += "| 状态 | 渠道 | 模式 | 心跳 | 连续失败 |\n|------|------|------|------|----------|\n"

        for name, (emoji, label) in CHANNEL_LABELS.items():
            raw = ch_raw.get(name, "")
            if raw:
                try:
                    hb = json.loads(raw)
                    age = time.time() - hb.get("ts", 0)
                    mode = hb.get("mode", "-")
                    fails = hb.get("consecutive_failures", 0)
                    status = "🟢" if age < 30 and fails < 3 else ("🟡" if age < 60 else "🔴")
                    channels_md += f"| {status} | {emoji} {label} | {mode} | {age:.0f}s ago | {fails} |\n"
                except Exception:
                    channels_md += f"| ⚪ | {emoji} {label} | - | 解析错误 | - |\n"
            else:
                channels_md += f"| ⚪ | {emoji} {label} | - | 未连接 | - |\n"

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"*最后刷新: {now_str}*\n\n{agents_md}\n{channels_md}"

    except Exception as e:
        return f"❌ 无法连接 Redis: {e}"


async def get_memory_health():
    try:
        return await send_to_orchestrator("memory_health")
    except Exception as e:
        return f"❌ {e}"


async def get_channel_status():
    try:
        return await send_to_orchestrator("channel")
    except Exception as e:
        return f"❌ {e}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tab 2: 对话
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

QUICK_COMMANDS = {
    "📊 涨停板": "zt",
    "🔗 连板股": "lb",
    "🏷️ 板块": "bk",
    "🔥 热股": "hot",
    "📋 行情摘要": "summary",
    "📰 新闻": "news",
    "📈 策略": "strategy",
    "💊 记忆健康": "memory_health",
    "🔧 系统状态": "status",
}


async def chat_submit(message: str, history: list):
    if not message or not message.strip():
        return history, ""

    msg = message.strip()
    history = history + [{"role": "user", "content": msg}]

    if msg.startswith("/"):
        parts = msg[1:].split(None, 1)
        cmd = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        result = await send_to_orchestrator(cmd, args)
    else:
        result = await send_to_orchestrator("chat", msg)

    history = history + [{"role": "assistant", "content": result}]
    return history, ""


async def quick_cmd(cmd_key: str, history: list):
    cmd = QUICK_COMMANDS.get(cmd_key, "")
    if not cmd:
        return history, ""
    history = history + [{"role": "user", "content": f"/{cmd}"}]
    result = await send_to_orchestrator(cmd, "")
    history = history + [{"role": "assistant", "content": result}]
    return history, ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tab 3: 行情
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_market_data(section: str):
    cmd_map = {
        "涨停板": "zt",
        "连板股": "lb",
        "概念板块": "bk",
        "热股排行": "hot",
        "行情摘要": "summary",
    }
    cmd = cmd_map.get(section, "summary")
    try:
        return await send_to_orchestrator(cmd)
    except Exception as e:
        return f"❌ {e}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tab 4: 任务
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PRESET_TASKS = {
    "🌅 盘前准备": "morning_prep",
    "🌆 收盘复盘": "close_review",
    "🔬 深度研究": "deep_research",
    "🧹 记忆维护": "memory_maintenance",
}


async def list_tasks():
    try:
        return await send_to_orchestrator("jobs")
    except Exception as e:
        return f"❌ {e}"


async def create_task(preset_label: str):
    preset = PRESET_TASKS.get(preset_label, "")
    if not preset:
        return "请选择预设任务"
    try:
        return await send_to_orchestrator("task_new", preset)
    except Exception as e:
        return f"❌ {e}"


async def get_task_detail(task_id: str):
    if not task_id.strip():
        return "请输入任务 ID"
    try:
        return await send_to_orchestrator("task_status", task_id.strip())
    except Exception as e:
        return f"❌ {e}"


async def cancel_task(task_id: str):
    if not task_id.strip():
        return "请输入任务 ID"
    try:
        return await send_to_orchestrator("task_cancel", task_id.strip())
    except Exception as e:
        return f"❌ {e}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tab 5: 系统
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def get_llm_status():
    try:
        return await send_to_orchestrator("llm_status")
    except Exception as e:
        return f"❌ {e}"


async def get_embed_status():
    try:
        return await send_to_orchestrator("embed_status")
    except Exception as e:
        return f"❌ {e}"


async def get_data_source_status():
    try:
        return await send_to_orchestrator("data_source_status")
    except Exception as e:
        return f"❌ {e}"


async def get_soul_status():
    try:
        return await send_to_orchestrator("soul_check")
    except Exception as e:
        return f"❌ {e}"


async def get_memory_hygiene():
    try:
        return await send_to_orchestrator("memory_hygiene")
    except Exception as e:
        return f"❌ {e}"


async def run_custom_command(cmd: str, args: str):
    if not cmd.strip():
        return "请输入命令"
    try:
        return await send_to_orchestrator(cmd.strip(), args.strip())
    except Exception as e:
        return f"❌ {e}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 构建 UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CUSTOM_CSS = """
.agent-header { text-align: center; padding: 8px 0; }
.agent-header h1 { font-size: 1.8em; margin: 0; }
.agent-header p { color: #666; margin: 4px 0 0 0; }
.quick-btn { min-width: 100px !important; }
footer { display: none !important; }
"""


def build_app():
    with gr.Blocks(title="OpenClaw Dashboard") as app:

        gr.HTML("""
        <div class="agent-header">
            <h1>🦀 OpenClaw Dashboard</h1>
            <p>多智能体 A 股分析系统 · Mac Mini M4</p>
        </div>
        """)

        with gr.Tabs():

            # ── Tab 1: 总览 ──────────────────────────────
            with gr.Tab("📊 总览", id="overview"):
                with gr.Row():
                    with gr.Column(scale=2):
                        overview_md = gr.Markdown("*点击刷新加载状态...*")
                        refresh_btn = gr.Button("🔄 刷新状态", variant="primary")
                    with gr.Column(scale=1):
                        memory_md = gr.Markdown("")
                        mem_btn = gr.Button("🧠 检查记忆")

                refresh_btn.click(fn=refresh_overview, outputs=overview_md)
                mem_btn.click(fn=get_memory_health, outputs=memory_md)

                app.load(fn=refresh_overview, outputs=overview_md)

            # ── Tab 2: 对话 ──────────────────────────────
            with gr.Tab("💬 对话", id="chat"):
                gr.Markdown("与 Agent 对话 — 输入自由文本或 `/命令`")

                with gr.Row():
                    for label in QUICK_COMMANDS:
                        gr.Button(label, size="sm", elem_classes="quick-btn")

                chatbot = gr.Chatbot(
                    height=480,
                )

                with gr.Row():
                    chat_input = gr.Textbox(
                        placeholder="输入消息或 /命令 (如 /ask 大盘怎么看)...",
                        show_label=False,
                        scale=5,
                        lines=1,
                    )
                    send_btn = gr.Button("发送", variant="primary", scale=1)

                send_btn.click(
                    fn=chat_submit,
                    inputs=[chat_input, chatbot],
                    outputs=[chatbot, chat_input],
                )
                chat_input.submit(
                    fn=chat_submit,
                    inputs=[chat_input, chatbot],
                    outputs=[chatbot, chat_input],
                )

                for i, (label, cmd) in enumerate(QUICK_COMMANDS.items()):
                    pass

            # ── Tab 3: 行情 ──────────────────────────────
            with gr.Tab("📈 行情", id="market"):
                with gr.Row():
                    mkt_section = gr.Dropdown(
                        choices=["涨停板", "连板股", "概念板块", "热股排行", "行情摘要"],
                        value="涨停板",
                        label="查看内容",
                        scale=2,
                    )
                    mkt_btn = gr.Button("🔄 获取数据", variant="primary", scale=1)

                mkt_result = gr.Markdown("*选择内容后点击获取*")
                mkt_btn.click(fn=fetch_market_data, inputs=mkt_section, outputs=mkt_result)

                with gr.Row():
                    gr.Button("📊 涨停板", size="sm").click(
                        fn=lambda: fetch_market_data("涨停板"), outputs=mkt_result
                    )
                    gr.Button("🔗 连板股", size="sm").click(
                        fn=lambda: fetch_market_data("连板股"), outputs=mkt_result
                    )
                    gr.Button("🏷️ 板块", size="sm").click(
                        fn=lambda: fetch_market_data("概念板块"), outputs=mkt_result
                    )
                    gr.Button("🔥 热股", size="sm").click(
                        fn=lambda: fetch_market_data("热股排行"), outputs=mkt_result
                    )

                with gr.Accordion("📝 自由提问", open=False):
                    ask_input = gr.Textbox(
                        placeholder="输入分析问题，如：今天哪些板块最强？",
                        label="问题",
                        lines=2,
                    )
                    ask_btn = gr.Button("🔬 发送分析", variant="secondary")
                    ask_result = gr.Markdown("")
                    ask_btn.click(
                        fn=lambda q: send_to_orchestrator("ask", q),
                        inputs=ask_input,
                        outputs=ask_result,
                    )

            # ── Tab 4: 任务 ──────────────────────────────
            with gr.Tab("📋 任务", id="tasks"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 创建任务")
                        preset_dd = gr.Dropdown(
                            choices=list(PRESET_TASKS.keys()),
                            label="选择预设",
                            value="🌅 盘前准备",
                        )
                        create_btn = gr.Button("🚀 启动任务", variant="primary")
                        create_result = gr.Markdown("")
                        create_btn.click(fn=create_task, inputs=preset_dd, outputs=create_result)

                    with gr.Column(scale=2):
                        gr.Markdown("### 任务列表")
                        tasks_md = gr.Markdown("*点击刷新查看任务*")
                        tasks_btn = gr.Button("🔄 刷新列表")
                        tasks_btn.click(fn=list_tasks, outputs=tasks_md)

                with gr.Row():
                    task_id_input = gr.Textbox(placeholder="task_xxxxxxxx", label="任务 ID", scale=2)
                    detail_btn = gr.Button("📄 详情", scale=1)
                    cancel_btn = gr.Button("🚫 取消", variant="stop", scale=1)

                task_detail_md = gr.Markdown("")
                detail_btn.click(fn=get_task_detail, inputs=task_id_input, outputs=task_detail_md)
                cancel_btn.click(fn=cancel_task, inputs=task_id_input, outputs=task_detail_md)

            # ── Tab 5: 系统 ──────────────────────────────
            with gr.Tab("⚙️ 系统", id="system"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### LLM 路由")
                        llm_md = gr.Markdown("")
                        gr.Button("🔄 刷新").click(fn=get_llm_status, outputs=llm_md)

                    with gr.Column():
                        gr.Markdown("### Embedding")
                        embed_md = gr.Markdown("")
                        gr.Button("🔄 刷新").click(fn=get_embed_status, outputs=embed_md)

                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### 数据源")
                        ds_md = gr.Markdown("")
                        gr.Button("🔄 刷新").click(fn=get_data_source_status, outputs=ds_md)

                    with gr.Column():
                        gr.Markdown("### SOUL 守护")
                        soul_md = gr.Markdown("")
                        gr.Button("🔄 刷新").click(fn=get_soul_status, outputs=soul_md)

                with gr.Accordion("🧹 记忆卫生报告", open=False):
                    hygiene_md = gr.Markdown("")
                    gr.Button("📋 生成报告").click(fn=get_memory_hygiene, outputs=hygiene_md)

                with gr.Accordion("🔧 自定义命令", open=False):
                    with gr.Row():
                        custom_cmd = gr.Textbox(placeholder="命令名 (如 status)", label="命令", scale=1)
                        custom_args = gr.Textbox(placeholder="参数 (可选)", label="参数", scale=2)
                        custom_btn = gr.Button("▶️ 执行", variant="secondary", scale=1)
                    custom_result = gr.Markdown("")
                    custom_btn.click(
                        fn=run_custom_command,
                        inputs=[custom_cmd, custom_args],
                        outputs=custom_result,
                    )

    return app


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    app = build_app()

    auth = None
    if WEBCHAT_AUTH_USER and WEBCHAT_AUTH_PASS:
        auth = (WEBCHAT_AUTH_USER, WEBCHAT_AUTH_PASS)
        logger.info(f"Auth enabled for user: {WEBCHAT_AUTH_USER}")

    logger.info(f"Starting OpenClaw Dashboard on {WEBCHAT_HOST}:{WEBCHAT_PORT}")
    app.launch(
        server_name=WEBCHAT_HOST,
        server_port=WEBCHAT_PORT,
        auth=auth,
        share=False,
    )


if __name__ == "__main__":
    main()
