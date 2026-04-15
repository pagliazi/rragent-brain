#!/bin/bash
# ═══════════════════════════════════════════════════
# OpenClaw Multi-Agent 一键安装脚本
# 用法: sudo bash install-agents.sh
# ═══════════════════════════════════════════════════

set -e

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAW_USER="clawagent"
CLAW_HOME="/Users/${CLAW_USER}"
OPENCLAW_DIR="${CLAW_HOME}/openclaw"
VENV_DIR="${OPENCLAW_DIR}/.venv"
LOGS_DIR="${CLAW_HOME}/logs"
LA_DIR="${CLAW_HOME}/Library/LaunchAgents"
CLAW_UID=$(id -u ${CLAW_USER} 2>/dev/null || echo "")

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# 在指定用户的主目录下执行命令（解决 getcwd PermissionError）
run_as_claw() {
    sudo -u ${CLAW_USER} bash -c "cd '${CLAW_HOME}' && $*"
}

run_as_zayl() {
    sudo -u zayl bash -c "cd /Users/zayl && $*"
}

if [ "$(id -u)" -ne 0 ]; then
    err "请使用 sudo 运行此脚本"
fi

if [ -z "${CLAW_UID}" ]; then
    err "用户 ${CLAW_USER} 不存在"
fi

# ── 1. 安装 Redis ──────────────────────────────────
log "检查 Redis..."
if ! command -v redis-server &>/dev/null; then
    log "安装 Redis..."
    run_as_zayl "brew install redis"
fi
if ! pgrep -x redis-server &>/dev/null; then
    log "启动 Redis..."
    run_as_zayl "brew services start redis"
    sleep 2
fi
if redis-cli ping 2>/dev/null | grep -q PONG; then
    log "Redis 运行正常 ✓"
else
    warn "Redis 可能未正常启动，请检查: brew services info redis"
fi

# ── 2. 创建目录 ────────────────────────────────────
log "创建目录..."
run_as_claw "mkdir -p '${OPENCLAW_DIR}/agents/souls' '${OPENCLAW_DIR}/agents/skills' '${OPENCLAW_DIR}/agents/memory' '${OPENCLAW_DIR}/agents/data_sources'"
run_as_claw "mkdir -p '${OPENCLAW_DIR}/memory/chroma' '${OPENCLAW_DIR}/memory/graph' '${OPENCLAW_DIR}/memory/embed_cache'"
run_as_claw "mkdir -p '${LOGS_DIR}'"
run_as_claw "mkdir -p '${LA_DIR}'"

# ── 3. 复制文件 ────────────────────────────────────
log "复制 Agent 文件..."
cp "${DEPLOY_DIR}/agents/__init__.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/base.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/orchestrator.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/market_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/analysis_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/dev_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/browser_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/desktop_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/news_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/strategist_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/general_agent.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/apple_agent.py" "${OPENCLAW_DIR}/agents/"

log "复制 SOUL 和 Skills 定义..."
cp "${DEPLOY_DIR}/agents/souls/"*.md "${OPENCLAW_DIR}/agents/souls/" 2>/dev/null || true
cp "${DEPLOY_DIR}/agents/skills/"*.yaml "${OPENCLAW_DIR}/agents/skills/" 2>/dev/null || true

log "复制 Memory 模块..."
cp "${DEPLOY_DIR}/agents/memory/"*.py "${OPENCLAW_DIR}/agents/memory/" 2>/dev/null || true
cp "${DEPLOY_DIR}/agents/memory/"*.yaml "${OPENCLAW_DIR}/agents/memory/" 2>/dev/null || true

log "复制 LLM 路由器..."
cp "${DEPLOY_DIR}/agents/llm_router.py" "${OPENCLAW_DIR}/agents/"

log "复制 MarketTime 模块..."
cp "${DEPLOY_DIR}/agents/market_time.py" "${OPENCLAW_DIR}/agents/"

log "复制 DataSources 模块..."
cp "${DEPLOY_DIR}/agents/data_sources/"*.py "${OPENCLAW_DIR}/agents/data_sources/" 2>/dev/null || true

log "复制 NotifyRouter..."
cp "${DEPLOY_DIR}/agents/notify_router.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/task_manager.py"  "${OPENCLAW_DIR}/agents/"

cp "${DEPLOY_DIR}/telegram_bot.py" "${OPENCLAW_DIR}/telegram_bot.py"
cp "${DEPLOY_DIR}/feishu_bot.py" "${OPENCLAW_DIR}/feishu_bot.py"
cp "${DEPLOY_DIR}/webchat_agent.py" "${OPENCLAW_DIR}/webchat_agent.py"
cp "${DEPLOY_DIR}/webchat_app.py" "${OPENCLAW_DIR}/webchat_app.py"
cp "${DEPLOY_DIR}/webchat_api.py" "${OPENCLAW_DIR}/webchat_api.py"
cp "${DEPLOY_DIR}/webchat_frontend.html" "${OPENCLAW_DIR}/webchat_frontend.html"
cp "${DEPLOY_DIR}/rules.yaml" "${OPENCLAW_DIR}/rules.yaml"

cp "${DEPLOY_DIR}/telegram.env" "${OPENCLAW_DIR}/telegram.env" 2>/dev/null || true
cp "${DEPLOY_DIR}/feishu.env" "${OPENCLAW_DIR}/feishu.env" 2>/dev/null || true
cp "${DEPLOY_DIR}/webchat.env" "${OPENCLAW_DIR}/webchat.env" 2>/dev/null || true

chown -R ${CLAW_USER}:staff "${OPENCLAW_DIR}"
chmod 600 "${OPENCLAW_DIR}/telegram.env" 2>/dev/null || true
chmod 600 "${OPENCLAW_DIR}/feishu.env" 2>/dev/null || true
chmod 600 "${OPENCLAW_DIR}/webchat.env" 2>/dev/null || true

# ── 4. Python 虚拟环境 + 依赖 ─────────────────────
log "检查 Python 虚拟环境..."
if [ ! -d "${VENV_DIR}" ]; then
    log "创建虚拟环境..."
    run_as_claw "/opt/homebrew/bin/python3 -m venv '${VENV_DIR}'"
fi

log "安装 Python 依赖 (2026-02-22 latest)..."
run_as_claw "'${VENV_DIR}/bin/pip' install --quiet --upgrade pip"
run_as_claw "'${VENV_DIR}/bin/pip' install --quiet --upgrade \
    'redis[hiredis]' \
    python-dotenv \
    pyyaml \
    httpx \
    python-telegram-bot \
    'httpx[socks]' \
    'browser-use>=0.11.11' \
    playwright \
    'langchain-openai>=1.1.9' \
    langchain-anthropic \
    langchain-google-genai \
    'langchain-core>=1.2.14' \
    chainlit \
    'fastapi>=0.115' \
    'uvicorn[standard]>=0.34' \
    'chromadb>=1.5.1' \
    networkx \
    aiohttp \
    akshare \
    'openclaw-sdk>=2.0.1'"

log "确认 Playwright 浏览器..."
run_as_claw "'${VENV_DIR}/bin/playwright' install chromium" 2>/dev/null || true

log "确认 bge-m3 embedding 模型..."
if command -v ollama &>/dev/null; then
    ollama pull bge-m3 2>/dev/null || warn "bge-m3 拉取失败，请手动执行: ollama pull bge-m3"
else
    warn "ollama 未安装，记忆系统将以降级模式运行"
fi

# ── 5. 停止旧服务 ─────────────────────────────────
log "停止旧的单体服务（如果有）..."
launchctl bootout gui/${CLAW_UID}/com.openclaw.telegram-agent 2>/dev/null || true

# ── 6. 安装 LaunchAgents ──────────────────────────
AGENTS=(
    "com.openclaw.orchestrator"
    "com.openclaw.market-agent"
    "com.openclaw.analysis-agent"
    "com.openclaw.dev-agent"
    "com.openclaw.browser-agent"
    "com.openclaw.desktop-agent"
    "com.openclaw.news-agent"
    "com.openclaw.strategist-agent"
    "com.openclaw.general-agent"
    "com.openclaw.apple-agent"
    "com.openclaw.telegram-bot"
    "com.openclaw.feishu-bot"
)

log "部署 LaunchAgent plist..."
for agent in "${AGENTS[@]}"; do
    plist_src="${DEPLOY_DIR}/launchagents/${agent}.plist"
    plist_dst="${LA_DIR}/${agent}.plist"
    if [ -f "${plist_src}" ]; then
        launchctl bootout gui/${CLAW_UID}/${agent} 2>/dev/null || true
        cp "${plist_src}" "${plist_dst}"
        chown ${CLAW_USER}:staff "${plist_dst}"
        chmod 644 "${plist_dst}"
        log "  → ${agent}"
    else
        warn "  ⚠ plist 不存在: ${plist_src}"
    fi
done

# ── 7. 启动所有服务 ───────────────────────────────
log "启动所有 Agent..."
for agent in "${AGENTS[@]}"; do
    plist_dst="${LA_DIR}/${agent}.plist"
    if [ -f "${plist_dst}" ]; then
        launchctl bootstrap gui/${CLAW_UID} "${plist_dst}" 2>/dev/null || true
        log "  ✓ ${agent}"
    fi
done

# WebChat Dashboard (Gradio)
log "部署 WebChat Dashboard..."
WEBCHAT_PLIST="${LA_DIR}/com.openclaw.webchat.plist"
cat > "/tmp/com.openclaw.webchat.plist" <<PEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.openclaw.webchat</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/python</string>
        <string>${OPENCLAW_DIR}/webchat_api.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${OPENCLAW_DIR}</string>
    <key>UserName</key>
    <string>${CLAW_USER}</string>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>${LOGS_DIR}/webchat.log</string>
    <key>StandardErrorPath</key>
    <string>${LOGS_DIR}/webchat.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PEOF
cp /tmp/com.openclaw.webchat.plist "${WEBCHAT_PLIST}"
chown ${CLAW_USER}:staff "${WEBCHAT_PLIST}"
launchctl bootout gui/${CLAW_UID}/com.openclaw.webchat 2>/dev/null || true
launchctl bootstrap gui/${CLAW_UID} "${WEBCHAT_PLIST}" 2>/dev/null || true
log "  ✓ com.openclaw.webchat (Gradio Dashboard)"

# ── 8. 验证 ───────────────────────────────────────
sleep 3
log "验证服务状态..."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
for agent in "${AGENTS[@]}"; do
    pid=$(launchctl print gui/${CLAW_UID}/${agent} 2>/dev/null | grep "pid = " | awk '{print $3}')
    if [ -n "${pid}" ] && [ "${pid}" != "0" ]; then
        echo -e "  ${GREEN}✓${NC} ${agent} (pid: ${pid})"
    else
        echo -e "  ${RED}✗${NC} ${agent}"
    fi
done
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
log "安装完成"
echo ""
echo "日志: ${LOGS_DIR}/"
echo "配置: ${OPENCLAW_DIR}/telegram.env"
echo "      ${OPENCLAW_DIR}/feishu.env"
echo "      ${OPENCLAW_DIR}/webchat.env"
echo "规则: ${OPENCLAW_DIR}/rules.yaml"
echo ""
echo "管理命令:"
echo "  查看全部:  sudo launchctl print gui/${CLAW_UID} | grep openclaw"
echo "  重启某个:  sudo launchctl kickstart -k gui/${CLAW_UID}/com.openclaw.<name>"
echo "  查看日志:  tail -f ${LOGS_DIR}/<name>.log"
