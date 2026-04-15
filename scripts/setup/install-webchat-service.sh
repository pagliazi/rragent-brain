#!/bin/bash
set -e

AGENT_USER="clawagent"
AGENT_HOME="/Users/${AGENT_USER}"
OPENCLAW_DIR="${AGENT_HOME}/openclaw"
DEPLOY_DIR="/Users/zayl/openclaw-deploy"

SERVICE_LABEL="com.openclaw.webchat"
PLIST_SRC="${DEPLOY_DIR}/com.openclaw.webchat.plist"
PLIST_DST="${AGENT_HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"

echo ""
echo "════════════════════════════════════════════════"
echo "  OpenClaw Web Chat Agent 服务安装 (Chainlit)"
echo "  端口: 0.0.0.0:7789"
echo "════════════════════════════════════════════════"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 sudo 运行: sudo bash $0"
    exit 1
fi

# ── 1. 部署 Python 脚本 ──
echo ">>> 1/6: 部署 webchat_agent.py"
cp "${DEPLOY_DIR}/webchat_agent.py" "${OPENCLAW_DIR}/webchat_agent.py"
chown ${AGENT_USER}:staff "${OPENCLAW_DIR}/webchat_agent.py"
chmod 644 "${OPENCLAW_DIR}/webchat_agent.py"
echo "[✓] webchat_agent.py"

# ── 2. 部署 .env 配置 ──
echo ""
echo ">>> 2/6: 部署 webchat.env"
ENV_DST="${OPENCLAW_DIR}/webchat.env"
if [ -f "${ENV_DST}" ]; then
    cp "${ENV_DST}" "${ENV_DST}.bak"
    echo "[i] 已备份旧配置 → webchat.env.bak"
fi
cp "${DEPLOY_DIR}/webchat.env" "${ENV_DST}"
chown ${AGENT_USER}:staff "${ENV_DST}"
chmod 600 "${ENV_DST}"
echo "[✓] webchat.env (权限 600)"

# ── 3. 安装 Python 依赖 ──
echo ""
echo ">>> 3/6: 安装 Python 依赖"
VENV_PIP="${OPENCLAW_DIR}/.venv/bin/pip"
if [ -x "${VENV_PIP}" ]; then
    cd /tmp
    sudo -H -u ${AGENT_USER} bash --norc --noprofile -c \
        "export HOME=${AGENT_HOME}; cd ${OPENCLAW_DIR}; \
         .venv/bin/pip install -q chainlit langchain-openai langchain-anthropic langchain-google-genai langchain-core 2>&1 | tail -8"
    echo "[✓] 依赖已安装 (chainlit / langchain-*)"
else
    echo "[!] 未找到 venv, 跳过依赖安装"
    echo "    请先确保 ${OPENCLAW_DIR}/.venv 存在"
    exit 1
fi

# ── 4. 安装 LaunchAgent ──
echo ""
echo ">>> 4/6: 安装 LaunchAgent"
mkdir -p "${AGENT_HOME}/Library/LaunchAgents"
chown ${AGENT_USER}:staff "${AGENT_HOME}/Library/LaunchAgents"
cp "${PLIST_SRC}" "${PLIST_DST}"
chown ${AGENT_USER}:staff "${PLIST_DST}"
chmod 644 "${PLIST_DST}"
echo "[✓] ${PLIST_DST}"

# ── 5. 确保日志目录 ──
echo ""
echo ">>> 5/6: 确保日志目录"
mkdir -p "${OPENCLAW_DIR}/logs"
chown ${AGENT_USER}:staff "${OPENCLAW_DIR}/logs"
echo "[✓] logs/"

# ── 6. 加载服务 ──
echo ""
echo ">>> 6/6: 加载服务"

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

sleep 5
echo ""

if lsof -i :7789 >/dev/null 2>&1; then
    echo "[✓] 端口 7789 已监听"
else
    echo "[i] 端口未就绪，查看日志:"
    echo "    tail -30 ${OPENCLAW_DIR}/logs/webchat.err"
fi

echo ""
echo "════════════════════════════════════════════════"
echo "  安装完成"
echo ""
echo "  访问: http://<Mac-Mini-IP>:7789"
echo ""
echo "  配置文件 (用 zayl 编辑):"
echo "    ${DEPLOY_DIR}/webchat.env"
echo "    → 部署到: ${ENV_DST}"
echo ""
echo "  日志:"
echo "    tail -f ${OPENCLAW_DIR}/logs/webchat.log"
echo "    tail -f ${OPENCLAW_DIR}/logs/webchat.err"
echo ""
echo "  管理命令:"
echo "    # 重启"
echo "    sudo launchctl kickstart -k ${GUI_DOMAIN}/${SERVICE_LABEL}"
echo ""
echo "    # 停止"
echo "    sudo launchctl bootout ${GUI_DOMAIN}/${SERVICE_LABEL}"
echo ""
echo "    # 启动"
echo "    sudo launchctl bootstrap ${GUI_DOMAIN} ${PLIST_DST}"
echo "════════════════════════════════════════════════"
