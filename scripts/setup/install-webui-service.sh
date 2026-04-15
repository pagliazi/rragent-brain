#!/bin/bash
set -e

AGENT_USER="clawagent"
AGENT_HOME="/Users/${AGENT_USER}"
OPENCLAW_DIR="${AGENT_HOME}/openclaw"
WEBUI_DIR="${AGENT_HOME}/openclaw-webui"
DEPLOY_DIR="/Users/zayl/openclaw-deploy"

SERVICE_LABEL="com.openclaw.webui"
PLIST_DST="${AGENT_HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"

WEBUI_PORT="${1:-7788}"
WEBUI_HOST="0.0.0.0"

RUN_AS="sudo -H -u ${AGENT_USER} bash --norc --noprofile -c"

echo ""
echo "════════════════════════════════════════════════"
echo "  OpenClaw Web UI 服务安装"
echo "  端口: ${WEBUI_HOST}:${WEBUI_PORT}"
echo "════════════════════════════════════════════════"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 sudo 运行: sudo bash $0 [端口号]"
    exit 1
fi

cd /tmp

# ── 1. 克隆 web-ui ──
echo ">>> 1/5: 克隆 browser-use/web-ui"
if [ -d "${WEBUI_DIR}/.git" ]; then
    echo "[i] 已存在，执行 git pull"
    ${RUN_AS} "export HOME=${AGENT_HOME}; cd ${WEBUI_DIR} && git pull"
else
    if [ -d "${WEBUI_DIR}" ]; then
        rm -rf "${WEBUI_DIR}"
    fi
    ${RUN_AS} "export HOME=${AGENT_HOME}; cd ${AGENT_HOME} && git clone https://github.com/browser-use/web-ui.git openclaw-webui"
fi
echo "[✓] web-ui 代码就绪: ${WEBUI_DIR}"

# ── 2. Python 虚拟环境 + 依赖 ──
echo ""
echo ">>> 2/5: 安装 Python 依赖"
if [ ! -d "${WEBUI_DIR}/.venv" ]; then
    ${RUN_AS} "export HOME=${AGENT_HOME}; cd ${WEBUI_DIR} && /opt/homebrew/bin/python3.12 -m venv .venv"
    echo "[✓] 创建 venv"
fi

${RUN_AS} "export HOME=${AGENT_HOME}; cd ${WEBUI_DIR} && .venv/bin/pip install --upgrade pip"
echo "[✓] pip 已更新"

${RUN_AS} "export HOME=${AGENT_HOME}; cd ${WEBUI_DIR} && .venv/bin/pip install -r requirements.txt"
echo "[✓] Python 依赖安装完成"

${RUN_AS} "export HOME=${AGENT_HOME}; cd ${WEBUI_DIR} && .venv/bin/python -m playwright install chromium"
echo "[✓] Playwright Chromium 安装完成"

# ── 3. 配置 .env ──
echo ""
echo ">>> 3/5: 配置 .env"
if [ ! -f "${WEBUI_DIR}/.env" ]; then
    if [ -f "${WEBUI_DIR}/.env.example" ]; then
        cp "${WEBUI_DIR}/.env.example" "${WEBUI_DIR}/.env"
    else
        touch "${WEBUI_DIR}/.env"
    fi
fi

EXISTING_ENV="${WEBUI_DIR}/.env"

add_env_if_missing() {
    local key="$1" val="$2"
    if ! grep -q "^${key}=" "${EXISTING_ENV}" 2>/dev/null; then
        echo "${key}=${val}" >> "${EXISTING_ENV}"
    fi
}

add_env_if_missing "OLLAMA_BASE_URL" "http://127.0.0.1:11434"
add_env_if_missing "OLLAMA_MODEL" "qwen2.5-coder:14b"
add_env_if_missing "BROWSER_USE_LOGGING_LEVEL" "info"
add_env_if_missing "ANONYMIZED_TELEMETRY" "false"

chown ${AGENT_USER}:staff "${EXISTING_ENV}"
chmod 600 "${EXISTING_ENV}"
echo "[✓] .env 已配置"

# ── 4. 安装 LaunchAgent ──
echo ""
echo ">>> 4/5: 安装 LaunchAgent"
mkdir -p "${AGENT_HOME}/Library/LaunchAgents"

cat > "${PLIST_DST}" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${SERVICE_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${WEBUI_DIR}/.venv/bin/python</string>
        <string>${WEBUI_DIR}/webui.py</string>
        <string>--ip</string>
        <string>${WEBUI_HOST}</string>
        <string>--port</string>
        <string>${WEBUI_PORT}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${WEBUI_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${AGENT_HOME}</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>no_proxy</key>
        <string>localhost,127.0.0.1,::1,*.local</string>
        <key>NO_PROXY</key>
        <string>localhost,127.0.0.1,::1,*.local</string>
        <key>http_proxy</key>
        <string></string>
        <key>https_proxy</key>
        <string></string>
        <key>all_proxy</key>
        <string></string>
        <key>ALL_PROXY</key>
        <string></string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>${WEBUI_DIR}/logs/webui.log</string>
    <key>StandardErrorPath</key>
    <string>${WEBUI_DIR}/logs/webui.err</string>

    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
PLISTEOF

chown ${AGENT_USER}:staff "${PLIST_DST}"
chmod 644 "${PLIST_DST}"

mkdir -p "${WEBUI_DIR}/logs"
chown ${AGENT_USER}:staff "${WEBUI_DIR}/logs"
echo "[✓] LaunchAgent 已安装"

# ── 5. 加载服务 ──
echo ""
echo ">>> 5/5: 加载服务"

AGENT_UID=$(id -u ${AGENT_USER})
GUI_DOMAIN="gui/${AGENT_UID}"

launchctl bootout "${GUI_DOMAIN}/${SERVICE_LABEL}" 2>/dev/null || true
sleep 1

if launchctl bootstrap "${GUI_DOMAIN}" "${PLIST_DST}" 2>/dev/null; then
    echo "[✓] 服务已启动"
else
    echo "[i] 服务将在 ${AGENT_USER} 下次登录桌面时自动启动"
    echo "    或手动: sudo launchctl bootstrap ${GUI_DOMAIN} ${PLIST_DST}"
fi

echo ""
echo "════════════════════════════════════════════════"
echo "  Web UI 安装完成"
echo ""
echo "  访问地址:"
echo "    http://192.168.1.188:${WEBUI_PORT}"
echo "    http://localhost:${WEBUI_PORT}"
echo ""
echo "  配置文件:"
echo "    ${WEBUI_DIR}/.env"
echo ""
echo "  日志:"
echo "    tail -f ${WEBUI_DIR}/logs/webui.log"
echo "    tail -f ${WEBUI_DIR}/logs/webui.err"
echo ""
echo "  管理:"
echo "    sudo launchctl kickstart -k ${GUI_DOMAIN}/${SERVICE_LABEL}   # 重启"
echo "    sudo launchctl bootout ${GUI_DOMAIN}/${SERVICE_LABEL}         # 停止"
echo "════════════════════════════════════════════════"
