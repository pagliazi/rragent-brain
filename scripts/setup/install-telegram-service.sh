#!/bin/bash
set -e

AGENT_USER="clawagent"
AGENT_HOME="/Users/${AGENT_USER}"
OPENCLAW_DIR="${AGENT_HOME}/openclaw"
DEPLOY_DIR="/Users/zayl/openclaw-deploy"

SERVICE_LABEL="com.openclaw.telegram-agent"
PLIST_SRC="${DEPLOY_DIR}/com.openclaw.telegram-agent.plist"
PLIST_DST="${AGENT_HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"

echo ""
echo "════════════════════════════════════════════════"
echo "  OpenClaw Telegram Agent 服务安装"
echo "════════════════════════════════════════════════"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 sudo 运行: sudo bash $0"
    exit 1
fi

# ── 1. 复制 Python 脚本 ──
echo ">>> 1/5: 部署 telegram_agent.py"
cp "${DEPLOY_DIR}/telegram_agent.py" "${OPENCLAW_DIR}/telegram_agent.py"
chown ${AGENT_USER}:staff "${OPENCLAW_DIR}/telegram_agent.py"
chmod 644 "${OPENCLAW_DIR}/telegram_agent.py"
echo "[✓] telegram_agent.py"

# ── 2. 部署 .env 配置 ──
echo ""
echo ">>> 2/5: 部署 .env 配置"
if [ -f "${OPENCLAW_DIR}/.env" ]; then
    cp "${OPENCLAW_DIR}/.env" "${OPENCLAW_DIR}/.env.bak"
    echo "[i] 已备份旧 .env → .env.bak"
fi
cp "${DEPLOY_DIR}/telegram.env" "${OPENCLAW_DIR}/.env"
chown ${AGENT_USER}:staff "${OPENCLAW_DIR}/.env"
chmod 600 "${OPENCLAW_DIR}/.env"
echo "[✓] .env (权限 600)"

# ── 3. 安装 LLM Provider 依赖 ──
echo ""
echo ">>> 3/7: 安装 LLM Provider 依赖"
VENV_PIP="${OPENCLAW_DIR}/.venv/bin/pip"
if [ -x "${VENV_PIP}" ]; then
    cd /tmp
    sudo -H -u ${AGENT_USER} bash --norc --noprofile -c \
        "export HOME=${AGENT_HOME}; cd ${OPENCLAW_DIR}; \
         .venv/bin/pip install -q langchain-openai langchain-anthropic langchain-google-genai 2>&1 | tail -5"
    echo "[✓] LLM 依赖已安装 (openai/anthropic/google)"
else
    echo "[!] 未找到 venv, 跳过 LLM 依赖安装"
fi

# ── 4. 创建日志目录 ──
echo ""
echo ">>> 4/7: 创建日志目录"
mkdir -p "${OPENCLAW_DIR}/logs"
chown ${AGENT_USER}:staff "${OPENCLAW_DIR}/logs"
echo "[✓] logs/"

# ── 5. 安装 LaunchAgent ──
echo ""
echo ">>> 5/7: 安装 LaunchAgent"
mkdir -p "${AGENT_HOME}/Library/LaunchAgents"
chown ${AGENT_USER}:staff "${AGENT_HOME}/Library/LaunchAgents"
cp "${PLIST_SRC}" "${PLIST_DST}"
chown ${AGENT_USER}:staff "${PLIST_DST}"
chmod 644 "${PLIST_DST}"
echo "[✓] ${PLIST_DST}"

# ── 6. 尝试加载服务 ──
echo ""
echo ">>> 6/7: 加载服务"

AGENT_UID=$(id -u ${AGENT_USER})
GUI_DOMAIN="gui/${AGENT_UID}"

launchctl bootout "${GUI_DOMAIN}/${SERVICE_LABEL}" 2>/dev/null || true
sleep 1

if launchctl bootstrap "${GUI_DOMAIN}" "${PLIST_DST}" 2>/dev/null; then
    echo "[✓] 服务已加载并启动"
else
    echo "[i] 服务将在 ${AGENT_USER} 下次登录桌面时自动启动"
    echo "    或手动: sudo launchctl bootstrap ${GUI_DOMAIN} ${PLIST_DST}"
fi

echo ""
echo ">>> 7/7: 完成"
echo ""
echo "════════════════════════════════════════════════"
echo "  安装完成"
echo ""
echo "  配置文件 (用 zayl 编辑):"
echo "    ${DEPLOY_DIR}/telegram.env"
echo "    → 部署到: ${OPENCLAW_DIR}/.env"
echo ""
echo "  ⚠️  必须修改: TELEGRAM_BOT_TOKEN"
echo "  ⚠️  建议设置: TELEGRAM_ALLOWED_USERS"
echo ""
echo "  🧠 切换 AI 模型:"
echo "    LLM_PROVIDER=openai / claude / deepseek / gemini / ollama"
echo "    LLM_MODEL=gpt-4o / claude-sonnet-4-20250514 / deepseek-chat ..."
echo "    LLM_API_KEY=sk-..."
echo ""
echo "  日志:"
echo "    tail -f ${OPENCLAW_DIR}/logs/telegram-agent.log"
echo "    tail -f ${OPENCLAW_DIR}/logs/telegram-agent.err"
echo ""
echo "  管理命令:"
echo "    # 查看状态"
echo "    sudo launchctl print ${GUI_DOMAIN}/${SERVICE_LABEL}"
echo ""
echo "    # 重启 (改完 .env 后)"
echo "    sudo launchctl kickstart -k ${GUI_DOMAIN}/${SERVICE_LABEL}"
echo ""
echo "    # 停止"
echo "    sudo launchctl bootout ${GUI_DOMAIN}/${SERVICE_LABEL}"
echo ""
echo "    # 启动"
echo "    sudo launchctl bootstrap ${GUI_DOMAIN} ${PLIST_DST}"
echo "════════════════════════════════════════════════"
