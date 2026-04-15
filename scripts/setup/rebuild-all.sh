#!/bin/bash
#=============================================================================
# 完整重建: 修复 SSH + 部署 OpenClaw (clawagent)
# 运行方式: sudo bash rebuild-all.sh
#=============================================================================
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}${BOLD}>>> $1${NC}"; }

[ "$(id -u)" -ne 0 ] && { echo "sudo bash $0"; exit 1; }

cd /tmp

USER="clawagent"
HOME_DIR="/Users/${USER}"
OPENCLAW="${HOME_DIR}/openclaw"
VENV="${OPENCLAW}/.venv"
DEPLOY="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo -e "${BOLD}  clawagent 完整重建${NC}"
echo -e "${BOLD}══════════════════════════════════════${NC}"

# ═══════════════════════════════════
# 1. 修复 SSH 认证
# ═══════════════════════════════════
log_step "1/6: 修复 SSH 认证"

# 检查底层 plist 是否有 SRP
HAS_SRP=$(plutil -p /var/db/dslocal/nodes/Default/users/${USER}.plist 2>/dev/null | grep -c "SRP" || echo "0")
if [ "$HAS_SRP" = "0" ]; then
    log_info "添加 SRP 认证方式..."
    # 设置完整的 AuthenticationAuthority
    dscl . -create /Users/${USER} AuthenticationAuthority ";ShadowHash;HASHLIST:<SALTED-SHA512-PBKDF2,SRP-RFC5054-4096-SHA512-PBKDF2>"
    # 用 sysadminctl 重设密码，强制生成 SRP 哈希
    sysadminctl -resetPasswordFor ${USER} -newPassword "Clawpwd123" -passwordHint "" 2>&1 || true
    log_info "认证方式已更新"
else
    log_info "SRP 认证已存在"
fi

# 确保在 SSH 组中
dscl . -read /Groups/com.apple.access_ssh GroupMembership 2>/dev/null | grep -qw "${USER}" || \
    dscl . -append /Groups/com.apple.access_ssh GroupMembership ${USER}
log_info "SSH 访问组已确认"

# 安全: 确保非管理员
if dscl . -read /Groups/admin GroupMembership 2>/dev/null | grep -qw "${USER}"; then
    log_error "${USER} 是管理员！"
    exit 1
fi
log_info "${USER} 是标准用户 (非管理员)"

# 加固 zayl home
chmod 700 /Users/zayl 2>/dev/null || true
chmod 700 /Users/ollama_runner 2>/dev/null || true
log_info "zayl/ollama_runner home 已加固 (700)"

# ═══════════════════════════════════
# 2. 配置 Shell 代理
# ═══════════════════════════════════
log_step "2/6: 配置 Shell 代理"

tee "${HOME_DIR}/.zshrc" > /dev/null << 'ZSHRC_EOF'
# clawagent Shell 配置 — OpenClaw Agent

# 网络代理 (Surge)
export PROXY_IP="127.0.0.1"
export http_proxy="http://${PROXY_IP}:6152"
export https_proxy="http://${PROXY_IP}:6152"
export all_proxy="socks5://${PROXY_IP}:6153"
export ALL_PROXY="socks5://${PROXY_IP}:6153"
export no_proxy="localhost,127.0.0.1,::1,*.local"
export NO_PROXY="localhost,127.0.0.1,::1,*.local"

# Homebrew
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# NVM
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

export HISTSIZE=1000
export SAVEHIST=500
ZSHRC_EOF

chown ${USER}:staff "${HOME_DIR}/.zshrc"
log_info ".zshrc 已写入"

# ═══════════════════════════════════
# 3. 安装 NVM + Node.js
# ═══════════════════════════════════
log_step "3/6: 安装 Node.js"

if sudo -H -u ${USER} bash --norc --noprofile -c "
    export HOME=${HOME_DIR}; cd ${HOME_DIR}
    [ -s '${HOME_DIR}/.nvm/nvm.sh' ] && echo EXISTS || echo MISSING
" 2>/dev/null | grep -q "EXISTS"; then
    log_info "NVM 已存在"
else
    log_info "安装 NVM..."
    sudo -H -u ${USER} bash --norc --noprofile -c "
        export HOME=${HOME_DIR}; cd ${HOME_DIR}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
    " 2>&1 | tail -3
fi

log_info "安装 Node.js LTS..."
sudo -H -u ${USER} bash --norc --noprofile -c "
    export HOME=${HOME_DIR}; cd ${HOME_DIR}
    export NVM_DIR='${HOME_DIR}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    if command -v node > /dev/null 2>&1; then
        echo \"已安装: \$(node --version)\"
    else
        nvm install --lts
        nvm alias default lts/*
        echo \"安装完成: \$(node --version)\"
    fi
" 2>&1 | tail -3

# ═══════════════════════════════════
# 4. 克隆 OpenClaw + 创建 venv
# ═══════════════════════════════════
log_step "4/6: 克隆 OpenClaw + Python venv"

PYTHON_BIN="/opt/homebrew/bin/python3.12"
if [ ! -x "$PYTHON_BIN" ]; then
    log_info "安装 Python 3.12..."
    sudo -u zayl /opt/homebrew/bin/brew install python@3.12 2>&1 | tail -3
fi
log_info "Python: $($PYTHON_BIN --version)"

# 克隆
if [ -d "${OPENCLAW}/.git" ]; then
    log_info "OpenClaw 仓库已存在"
else
    sudo -H -u ${USER} mkdir -p "${OPENCLAW}"
    log_info "克隆 browser-use..."
    sudo -H -u ${USER} bash --norc --noprofile -c "
        export HOME=${HOME_DIR}; cd ${HOME_DIR}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        rm -rf ${OPENCLAW}
        git clone https://github.com/browser-use/browser-use.git ${OPENCLAW}
    " 2>&1 | tail -5
    log_info "克隆完成"
fi

# workspace
sudo -H -u ${USER} mkdir -p "${OPENCLAW}/workspace"

# venv
log_info "创建 Python venv..."
sudo -H -u ${USER} bash --norc --noprofile -c "
    cd ${OPENCLAW}
    ${PYTHON_BIN} -m venv ${VENV}
" 2>&1
log_info "venv: $(sudo -H -u ${USER} bash --norc --noprofile -c "cd ${HOME_DIR}; ${VENV}/bin/python --version" 2>&1)"

# ═══════════════════════════════════
# 5. 安装依赖 + Playwright
# ═══════════════════════════════════
log_step "5/6: 安装 browser-use + Playwright"

sudo -H -u ${USER} bash --norc --noprofile -c "
    export HOME=${HOME_DIR}; cd ${OPENCLAW}
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152

    echo '>>> pip upgrade'
    ${VENV}/bin/pip install --upgrade pip 2>&1 | tail -1

    echo '>>> browser-use install'
    ${VENV}/bin/pip install . 2>&1 | tail -5

    echo '>>> playwright install'
    ${VENV}/bin/python -m playwright install chromium 2>&1 | tail -5
" 2>&1

log_info "依赖安装完成"

# ═══════════════════════════════════
# 6. 部署配置文件 + 启动脚本
# ═══════════════════════════════════
log_step "6/6: 部署配置文件"

# config.json
cat > "${OPENCLAW}/config.json" << 'CONF_EOF'
{
  "llm": {
    "provider": "openai",
    "baseUrl": "http://127.0.0.1:11434/v1",
    "apiKey": "ollama",
    "model": "qwen2.5-coder:14b",
    "contextWindow": 16384
  },
  "agent": {
    "name": "Mac_Claw_Worker",
    "role": "Desktop Automation Engineer",
    "autoUpdate": false
  },
  "browser": {
    "headless": false,
    "executablePath": "",
    "args": [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-blink-features=AutomationControlled",
      "--proxy-server=http://127.0.0.1:6152"
    ]
  },
  "sandbox": {
    "enabled": true,
    "workspace": "/Users/clawagent/openclaw/workspace",
    "network": "allow"
  }
}
CONF_EOF
chown ${USER}:staff "${OPENCLAW}/config.json"
chmod 600 "${OPENCLAW}/config.json"
log_info "config.json"

# .env
cat > "${OPENCLAW}/.env" << 'ENV_EOF'
OPENAI_API_BASE=http://127.0.0.1:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=qwen2.5-coder:14b
BROWSER_PROXY=http://127.0.0.1:6152
SANDBOX_ENABLED=true
WORKSPACE_DIR=/Users/clawagent/openclaw/workspace
ENV_EOF
chown ${USER}:staff "${OPENCLAW}/.env"
chmod 600 "${OPENCLAW}/.env"
log_info ".env"

# start.sh
cat > "${OPENCLAW}/start.sh" << 'START_EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/.venv/bin/activate"
export http_proxy="http://127.0.0.1:6152"
export https_proxy="http://127.0.0.1:6152"
export all_proxy="socks5://127.0.0.1:6153"
export no_proxy="localhost,127.0.0.1,::1,*.local"
echo "Python:  $(python --version)"
echo "Proxy:   ${http_proxy}"
echo "Ollama:  http://127.0.0.1:11434"
echo ""
if [ -f "main.py" ]; then
    python main.py
elif [ -f "package.json" ]; then
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    npm start
else
    echo "可用方式: python -m browser_use 或 npm start"
fi
START_EOF
chown ${USER}:staff "${OPENCLAW}/start.sh"
chmod 755 "${OPENCLAW}/start.sh"
log_info "start.sh"

# ═══════════════════════════════════
# 最终验证
# ═══════════════════════════════════
log_step "最终验证"

PASS=0; FAIL=0
ck() { if [ "$1" = "pass" ]; then ((PASS++)); log_info "$2"; else ((FAIL++)); log_error "$2"; fi; }

run() { sudo -H -u ${USER} bash --norc --noprofile -c "export HOME=${HOME_DIR}; cd ${HOME_DIR}; $1"; }

# 安全隔离
run "ls /Users/zayl/ >/dev/null 2>&1" && ck fail "隔离失败" || ck pass "安全隔离"

# Python
run "${VENV}/bin/python --version 2>&1" | grep -q "3.12" && ck pass "Python 3.12" || ck fail "Python"

# browser-use
run "${VENV}/bin/python -c 'import browser_use; print(1)' 2>/dev/null" | grep -q "1" && ck pass "browser-use" || ck fail "browser-use"

# Playwright
run "${VENV}/bin/python -c 'from playwright.sync_api import sync_playwright; print(1)' 2>/dev/null" | grep -q "1" && ck pass "Playwright" || ck fail "Playwright"

# Ollama
run "curl -s --max-time 3 http://127.0.0.1:11434 2>/dev/null" | grep -q "running" && ck pass "Ollama" || ck fail "Ollama"

# Surge
run "curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip 2>/dev/null" | grep -q "origin" && ck pass "Surge" || ck fail "Surge"

# 配置完整
[ -f "${OPENCLAW}/config.json" ] && [ -f "${OPENCLAW}/.env" ] && [ -f "${OPENCLAW}/start.sh" ] && [ -d "${OPENCLAW}/workspace" ] && \
    ck pass "配置文件完整" || ck fail "配置文件"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}全部就绪！${NC}"
    echo ""
    echo "  启动: 登录 clawagent 桌面 (VNC)"
    echo "    cd ~/openclaw && bash start.sh"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
