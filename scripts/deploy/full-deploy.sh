#!/bin/bash
#=============================================================================
# OpenClaw 一键完整部署脚本
# 运行方式: sudo bash /Users/zayl/openclaw-deploy/full-deploy.sh
#=============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}${BOLD}>>> $1${NC}"; }

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
OLD_USER="claw_tunnel"
NEW_USER="claw_agent"
AGENT_HOME="/Users/${NEW_USER}"

# =============================================
# 权限检查
# =============================================
if [ "$(id -u)" -ne 0 ]; then
    log_error "需要 root 权限: sudo bash $0"
    exit 1
fi

echo ""
echo -e "${BOLD}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   OpenClaw 完整部署 — Mac Mini M4                     ║${NC}"
echo -e "${BOLD}║   zayl (Surge) + ollama_runner (AI) + claw_agent (Agent)║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

# =============================================
# 步骤 0: 重命名用户 claw_tunnel → claw_agent
# =============================================
log_step "步骤 0/5: 重命名用户 ${OLD_USER} → ${NEW_USER}"

if dscl . -read /Users/${NEW_USER} UniqueID > /dev/null 2>&1; then
    log_info "用户 ${NEW_USER} 已存在，跳过重命名"
    AGENT_HOME="/Users/${NEW_USER}"
elif dscl . -read /Users/${OLD_USER} UniqueID > /dev/null 2>&1; then
    # 终止旧用户进程
    PROCS=$(ps -u ${OLD_USER} -o pid= 2>/dev/null | wc -l | tr -d ' ')
    if [ "$PROCS" -gt 0 ]; then
        log_info "终止 ${OLD_USER} 的 ${PROCS} 个进程..."
        pkill -u ${OLD_USER} 2>/dev/null || true
        sleep 3
        # 强制终止残余
        pkill -9 -u ${OLD_USER} 2>/dev/null || true
        sleep 1
    fi

    # 执行重命名
    dscl . -change /Users/${OLD_USER} RecordName "${OLD_USER}" "${NEW_USER}"
    log_info "RecordName 已更新"

    dscl . -change /Users/${NEW_USER} RealName "" "Claw Agent" 2>/dev/null || \
    dscl . -create /Users/${NEW_USER} RealName "Claw Agent"
    log_info "RealName 设为 Claw Agent"

    if [ -d "/Users/${OLD_USER}" ] && [ ! -d "${AGENT_HOME}" ]; then
        mv "/Users/${OLD_USER}" "${AGENT_HOME}"
        log_info "Home 目录: /Users/${OLD_USER} → ${AGENT_HOME}"
    fi

    dscl . -change /Users/${NEW_USER} NFSHomeDirectory "/Users/${OLD_USER}" "${AGENT_HOME}"
    log_info "NFSHomeDirectory 已更新"

    # 验证
    VERIFY_NAME=$(dscl . -read /Users/${NEW_USER} RecordName 2>/dev/null | awk '{print $2}')
    VERIFY_HOME=$(dscl . -read /Users/${NEW_USER} NFSHomeDirectory 2>/dev/null | awk '{print $2}')
    log_info "重命名完成: 用户=${VERIFY_NAME}, Home=${VERIFY_HOME}"
else
    log_error "用户 ${OLD_USER} 和 ${NEW_USER} 都不存在！请先创建用户。"
    exit 1
fi

# 安全验证: 确保非管理员
if dscl . -read /Groups/admin GroupMembership 2>/dev/null | grep -qw "${NEW_USER}"; then
    log_error "${NEW_USER} 是管理员！安全风险，中止部署。"
    exit 1
fi
log_info "${NEW_USER} 安全验证通过 (标准用户)"

# =============================================
# 步骤 1: Surge 网络验证
# =============================================
log_step "步骤 1/5: Surge 网络验证"

if ! pgrep -x "Surge" > /dev/null 2>&1; then
    log_warn "Surge 未运行，浏览器代理功能将不可用"
else
    log_info "Surge 运行中 (PID: $(pgrep -x 'Surge'))"
fi

# App Nap 防护
sudo -u zayl defaults write com.nssurge.surge-mac NSAppSleepDisabled -bool YES 2>/dev/null || true
log_info "Surge App Nap 防护已设置"

# 代理连通性
HTTP_TEST=$(curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip 2>/dev/null || echo "FAIL")
if echo "$HTTP_TEST" | grep -q "origin"; then
    log_info "Surge HTTP 代理正常 (6152)"
else
    log_warn "Surge HTTP 代理异常 — 请确认 Surge 中 allow-wifi-access = true"
fi

SOCKS_TEST=$(curl -s --max-time 5 --socks5 127.0.0.1:6153 https://httpbin.org/ip 2>/dev/null || echo "FAIL")
if echo "$SOCKS_TEST" | grep -q "origin"; then
    log_info "Surge SOCKS5 代理正常 (6153)"
else
    log_warn "Surge SOCKS5 代理异常"
fi

# =============================================
# 步骤 2: Ollama 服务验证
# =============================================
log_step "步骤 2/5: Ollama 服务验证"

OLLAMA_RESP=$(curl -s --max-time 5 http://127.0.0.1:11434 2>/dev/null || echo "")
if [ "$OLLAMA_RESP" = "Ollama is running" ]; then
    log_info "Ollama API 正常 (127.0.0.1:11434)"
else
    log_warn "Ollama 未运行，尝试启动..."
    launchctl kickstart system/com.local.ollama 2>/dev/null || \
    launchctl load /Library/LaunchDaemons/com.local.ollama.plist 2>/dev/null || true
    sleep 3
    OLLAMA_RESP2=$(curl -s --max-time 5 http://127.0.0.1:11434 2>/dev/null || echo "")
    if [ "$OLLAMA_RESP2" = "Ollama is running" ]; then
        log_info "Ollama 已启动"
    else
        log_error "Ollama 启动失败，请手动检查"
    fi
fi

# OpenAI 兼容接口
OAI_TEST=$(curl -s --max-time 5 http://127.0.0.1:11434/v1/models 2>/dev/null || echo "")
if echo "$OAI_TEST" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d.get('data',[]))>0" 2>/dev/null; then
    MODEL_COUNT=$(echo "$OAI_TEST" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
    log_info "OpenAI 兼容接口正常，${MODEL_COUNT} 个模型可用"
else
    log_warn "OpenAI 兼容接口异常"
fi

# =============================================
# 步骤 3: claw_agent 环境部署
# =============================================
log_step "步骤 3/5: claw_agent 环境部署"

# 3.1 写入 .zshrc
tee "${AGENT_HOME}/.zshrc" > /dev/null << 'ZSHRC_EOF'
# =============================================================================
# claw_agent Shell 配置 — OpenClaw Agent 专用
# =============================================================================

# --- 网络代理 (指向 zayl 的 Surge) ---
export PROXY_IP="127.0.0.1"
export http_proxy="http://${PROXY_IP}:6152"
export https_proxy="http://${PROXY_IP}:6152"
export all_proxy="socks5://${PROXY_IP}:6153"
export ALL_PROXY="socks5://${PROXY_IP}:6153"
export no_proxy="localhost,127.0.0.1,::1,*.local"
export NO_PROXY="localhost,127.0.0.1,::1,*.local"

# --- Homebrew (Apple Silicon) ---
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# --- NVM ---
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# --- 安全限制 ---
export HISTSIZE=1000
export SAVEHIST=500
ZSHRC_EOF

chown ${NEW_USER}:staff "${AGENT_HOME}/.zshrc"
chmod 644 "${AGENT_HOME}/.zshrc"
log_info ".zshrc 代理配置已写入"

# 3.2 安装 NVM + Node.js
if sudo -u ${NEW_USER} bash -c "[ -s '${AGENT_HOME}/.nvm/nvm.sh' ]"; then
    log_info "NVM 已存在"
else
    log_info "安装 NVM..."
    sudo -u ${NEW_USER} bash -c "
        export HOME=${AGENT_HOME}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
    " 2>&1 | tail -3
fi

# 安装 Node.js LTS
log_info "安装/检查 Node.js LTS..."
sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    if command -v node > /dev/null 2>&1; then
        echo \"Node.js 已安装: \$(node --version)\"
    else
        nvm install --lts
        nvm alias default lts/*
        echo \"Node.js 安装完成: \$(node --version)\"
    fi
" 2>&1 | tail -3

# 验证 Node
NODE_VER=$(sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    node --version 2>/dev/null || echo 'NOT_FOUND'
")
if [ "$NODE_VER" != "NOT_FOUND" ]; then
    log_info "Node.js ${NODE_VER} 就绪"
else
    log_error "Node.js 安装失败"
fi

# =============================================
# 步骤 4: OpenClaw 代码克隆与配置
# =============================================
log_step "步骤 4/5: OpenClaw 代码克隆与配置"

OPENCLAW_DIR="${AGENT_HOME}/openclaw"

# 创建工作区
sudo -u ${NEW_USER} mkdir -p "${OPENCLAW_DIR}/workspace"

# 克隆代码
if [ -d "${OPENCLAW_DIR}/.git" ]; then
    log_info "OpenClaw 仓库已存在，更新..."
    sudo -u ${NEW_USER} bash -c "
        export HOME=${AGENT_HOME}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        cd ${OPENCLAW_DIR} && git pull --rebase 2>&1 || true
    " | tail -3
else
    log_info "克隆 OpenClaw (browser-use)..."
    sudo -u ${NEW_USER} bash -c "
        export HOME=${AGENT_HOME}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        git clone https://github.com/browser-use/browser-use.git ${OPENCLAW_DIR} 2>&1
    " | tail -5
    log_info "克隆完成"
fi

# 部署 config.json
cp "${DEPLOY_DIR}/config.json" "${OPENCLAW_DIR}/config.json"
chown ${NEW_USER}:staff "${OPENCLAW_DIR}/config.json"
chmod 600 "${OPENCLAW_DIR}/config.json"
log_info "config.json 已部署 (权限 600)"

# 部署 .env
tee "${OPENCLAW_DIR}/.env" > /dev/null << 'ENV_EOF'
# OpenClaw 环境配置
OPENAI_API_BASE=http://127.0.0.1:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=qwen2.5-coder:14b
BROWSER_PROXY=http://127.0.0.1:6152
SANDBOX_ENABLED=true
WORKSPACE_DIR=/Users/claw_agent/openclaw/workspace
ENV_EOF

chown ${NEW_USER}:staff "${OPENCLAW_DIR}/.env"
chmod 600 "${OPENCLAW_DIR}/.env"
log_info ".env 已部署 (权限 600)"

# 安装项目依赖
log_info "安装项目依赖..."
sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152
    cd ${OPENCLAW_DIR}

    if [ -f 'package.json' ]; then
        echo '>>> npm install'
        npm install 2>&1 | tail -5
    fi

    if [ -f 'requirements.txt' ]; then
        echo '>>> pip install'
        python3 -m pip install --user -r requirements.txt 2>&1 | tail -5
    fi

    if [ -f 'pyproject.toml' ]; then
        echo '>>> pip install -e .'
        python3 -m pip install --user -e '.' 2>&1 | tail -5
    fi
" 2>&1 | tail -10

# 安装 Playwright chromium
log_info "安装 Playwright Chromium..."
sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152
    cd ${OPENCLAW_DIR}

    if command -v npx > /dev/null 2>&1 && [ -f 'node_modules/.package-lock.json' ]; then
        npx playwright install chromium 2>&1 | tail -3
    elif command -v playwright > /dev/null 2>&1; then
        playwright install chromium 2>&1 | tail -3
    elif python3 -c 'import playwright' 2>/dev/null; then
        python3 -m playwright install chromium 2>&1 | tail -3
    else
        echo '[跳过] playwright 将在依赖安装完成后安装'
    fi
" 2>&1 | tail -5

# =============================================
# 步骤 5: 全链路验证
# =============================================
log_step "步骤 5/5: 全链路安全验证"

PASS=0; FAIL=0; WARN=0

run_check() {
    local result=$1 label=$2
    if [ "$result" = "pass" ]; then
        ((PASS++)); log_info "$label"
    elif [ "$result" = "warn" ]; then
        ((WARN++)); log_warn "$label"
    else
        ((FAIL++)); log_error "$label"
    fi
}

# 用户安全
if ! dscl . -read /Groups/admin GroupMembership 2>/dev/null | grep -qw "${NEW_USER}"; then
    run_check "pass" "claw_agent 非管理员"
else
    run_check "fail" "claw_agent 是管理员！"
fi

# 文件隔离
ISO=$(sudo -u ${NEW_USER} cat /Users/zayl/.zshrc 2>&1 || true)
if echo "$ISO" | grep -qi "permission denied"; then
    run_check "pass" "文件隔离: claw_agent 无法读取 zayl 文件"
else
    run_check "fail" "文件隔离失败"
fi

ISO2=$(sudo -u ${NEW_USER} ls /Users/zayl/.ssh/ 2>&1 || true)
if echo "$ISO2" | grep -qi "permission denied"; then
    run_check "pass" "SSH 密钥隔离: claw_agent 无法读取 zayl/.ssh"
else
    run_check "fail" "SSH 密钥泄露风险"
fi

# Ollama
if curl -s --max-time 3 http://127.0.0.1:11434 2>/dev/null | grep -q "running"; then
    run_check "pass" "Ollama 可达"
else
    run_check "fail" "Ollama 不可达"
fi

# Surge
if curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip 2>/dev/null | grep -q "origin"; then
    run_check "pass" "Surge HTTP 代理可用"
else
    run_check "warn" "Surge HTTP 代理异常"
fi

# 系统稳定性
SLEEP_VAL=$(pmset -g | grep "^ sleep" | awk '{print $2}')
[ "$SLEEP_VAL" = "0" ] && run_check "pass" "系统不休眠 (sleep=0)" || run_check "warn" "系统休眠=${SLEEP_VAL}分钟"

pgrep caffeinate > /dev/null 2>&1 && run_check "pass" "caffeinate 运行中" || run_check "warn" "caffeinate 未运行"

# Node.js
NODE_CHECK=$(sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}; export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    node --version 2>/dev/null || echo FAIL
")
if [ "$NODE_CHECK" != "FAIL" ]; then
    run_check "pass" "claw_agent Node.js ${NODE_CHECK}"
else
    run_check "fail" "claw_agent Node.js 未安装"
fi

# 配置文件
[ -f "${OPENCLAW_DIR}/config.json" ] && run_check "pass" "config.json 存在" || run_check "fail" "config.json 缺失"
[ -f "${OPENCLAW_DIR}/.env" ] && run_check "pass" ".env 存在" || run_check "fail" ".env 缺失"
[ -d "${OPENCLAW_DIR}/workspace" ] && run_check "pass" "workspace 目录存在" || run_check "fail" "workspace 缺失"

# =============================================
# 最终报告
# =============================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${YELLOW}警告: $WARN${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}部署成功！${NC}"
else
    echo -e "  ${RED}有 $FAIL 项失败，请检查上方输出${NC}"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  下一步操作:"
echo "  1. 切换到 claw_agent 用户登录桌面"
echo "  2. 打开终端执行:"
echo "     cd ~/openclaw && npm start"
echo "  3. 首次运行授予 Terminal 辅助功能 + 屏幕录制权限"
echo "  4. 回到 zayl 桌面，用屏幕共享监控:"
echo "     open vnc://localhost"
echo ""
