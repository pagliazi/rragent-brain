#!/bin/bash
#=============================================================================
# 第三步：claw_agent 用户环境部署
# 运行用户：zayl (管理员, 需要 sudo)
# 目的：为 claw_agent 配置代理、安装 Node.js、部署 OpenClaw
#=============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

AGENT_USER="claw_agent"
AGENT_HOME="/Users/${AGENT_USER}"

echo "============================================="
echo "  第三步：claw_agent 执行环境部署"
echo "============================================="
echo ""

# --- 安全检查 ---
if [ "$(whoami)" != "zayl" ]; then
    log_error "此脚本必须以 zayl 用户身份运行"
    exit 1
fi

# 验证用户存在
if ! dscl . -read /Users/${AGENT_USER} UniqueID > /dev/null 2>&1; then
    log_error "用户 ${AGENT_USER} 不存在！"
    exit 1
fi

# 验证非管理员
if dscl . -read /Groups/admin GroupMembership 2>/dev/null | grep -q "${AGENT_USER}"; then
    log_error "安全警告：${AGENT_USER} 是管理员！必须降级为标准用户。"
    exit 1
fi
log_info "${AGENT_USER} 安全验证通过 (标准用户, 非管理员)"

# --- 3.1 配置 Shell 代理环境 ---
log_step "3.1 - 配置 claw_agent 的 Shell 代理环境"

ZSHRC_CONTENT='# =============================================================================
# claw_agent Shell 配置 - OpenClaw Agent 专用
# 由 openclaw-deploy 自动生成
# =============================================================================

# --- 网络代理 (指向 zayl 的 Surge) ---
export PROXY_IP="127.0.0.1"
export http_proxy="http://${PROXY_IP}:6152"
export https_proxy="http://${PROXY_IP}:6152"
export all_proxy="socks5://${PROXY_IP}:6153"
export ALL_PROXY="socks5://${PROXY_IP}:6153"

# 排除本地服务和 Ollama 不走代理（避免回环）
export no_proxy="localhost,127.0.0.1,::1,*.local"
export NO_PROXY="localhost,127.0.0.1,::1,*.local"

# --- Node.js 环境 ---
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# --- Homebrew (Apple Silicon) ---
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# --- OpenClaw 工作区限制 ---
export OPENCLAW_WORKSPACE="$HOME/openclaw/workspace"

# --- 安全限制：限制历史记录长度防止信息泄露 ---
export HISTSIZE=1000
export SAVEHIST=500
'

sudo tee "${AGENT_HOME}/.zshrc" > /dev/null << 'ZSHRC_EOF'
# =============================================================================
# claw_agent Shell 配置 - OpenClaw Agent 专用
# 由 openclaw-deploy 自动生成
# =============================================================================

# --- 网络代理 (指向 zayl 的 Surge) ---
export PROXY_IP="127.0.0.1"
export http_proxy="http://${PROXY_IP}:6152"
export https_proxy="http://${PROXY_IP}:6152"
export all_proxy="socks5://${PROXY_IP}:6153"
export ALL_PROXY="socks5://${PROXY_IP}:6153"

# 排除本地服务和 Ollama 不走代理（避免回环）
export no_proxy="localhost,127.0.0.1,::1,*.local"
export NO_PROXY="localhost,127.0.0.1,::1,*.local"

# --- Homebrew (Apple Silicon) ---
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# --- NVM (Node Version Manager) ---
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# --- OpenClaw 工作区限制 ---
export OPENCLAW_WORKSPACE="$HOME/openclaw/workspace"

# --- 安全限制：限制历史记录长度 ---
export HISTSIZE=1000
export SAVEHIST=500
ZSHRC_EOF

sudo chown ${AGENT_USER}:staff "${AGENT_HOME}/.zshrc"
sudo chmod 644 "${AGENT_HOME}/.zshrc"
log_info ".zshrc 已写入 ${AGENT_HOME}/.zshrc"

# --- 3.2 安装 NVM 和 Node.js ---
log_step "3.2 - 为 claw_agent 安装 Node.js (通过 NVM)"

if sudo -u ${AGENT_USER} bash -c '[ -d "$HOME/.nvm" ]'; then
    log_info "NVM 目录已存在，跳过下载"
else
    log_info "下载并安装 NVM..."
    sudo -u ${AGENT_USER} bash -c 'export HOME=/Users/claw_agent && curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash'
fi

# 安装 Node.js LTS
log_info "安装 Node.js LTS..."
sudo -u ${AGENT_USER} bash -c '
    export HOME=/Users/claw_agent
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    if command -v node > /dev/null 2>&1; then
        echo "Node.js 已安装: $(node --version)"
    else
        nvm install --lts
        nvm use --lts
        nvm alias default lts/*
        echo "Node.js 安装完成: $(node --version)"
    fi
'

# --- 3.3 创建工作区目录 ---
log_step "3.3 - 创建 OpenClaw 工作区"

sudo -u ${AGENT_USER} mkdir -p "${AGENT_HOME}/openclaw/workspace"
log_info "工作区目录: ${AGENT_HOME}/openclaw/workspace"

# --- 3.4 权限验证 ---
log_step "3.4 - 文件隔离验证"

echo ""
echo "  验证 claw_agent 无法读取 zayl 的文件..."
ISOLATION_TEST=$(sudo -u ${AGENT_USER} ls /Users/zayl/Documents 2>&1 || true)
if echo "$ISOLATION_TEST" | grep -q "Permission denied"; then
    log_info "文件隔离验证通过 ✓ (claw_agent 无法读取 /Users/zayl/Documents)"
else
    log_warn "文件隔离可能存在问题！输出: $ISOLATION_TEST"
fi

echo ""
echo "============================================="
echo "  第三步完成"
echo "============================================="
echo ""
echo "  后续操作 (如需桌面模式):"
echo "  1. 切换到 claw_agent 用户登录桌面"
echo "  2. 在该用户下运行 OpenClaw"
echo "  3. 授予 Terminal 辅助功能和屏幕录制权限"
