#!/bin/bash
#=============================================================================
# 第四步：OpenClaw 代码克隆与配置部署
# 运行用户：zayl (管理员, 需要 sudo)
# 目的：在 claw_agent 下克隆 OpenClaw 并配置
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
OPENCLAW_DIR="${AGENT_HOME}/openclaw"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================="
echo "  第四步：OpenClaw 部署"
echo "============================================="
echo ""

# --- 4.1 克隆 OpenClaw 仓库 ---
log_step "4.1 - 克隆 OpenClaw 代码"

if [ -d "${OPENCLAW_DIR}/.git" ]; then
    log_info "OpenClaw 仓库已存在，执行 git pull 更新..."
    sudo -u ${AGENT_USER} bash -c "
        export HOME=${AGENT_HOME}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        cd ${OPENCLAW_DIR}
        git pull --rebase 2>&1 || echo 'git pull failed, continuing...'
    "
elif [ -d "${OPENCLAW_DIR}" ]; then
    log_info "openclaw 目录存在但非 git 仓库，在其中克隆..."
    sudo -u ${AGENT_USER} bash -c "
        export HOME=${AGENT_HOME}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        cd ${AGENT_HOME}
        git clone https://github.com/browser-use/browser-use.git ${OPENCLAW_DIR}_tmp 2>&1 || true
        if [ -d '${OPENCLAW_DIR}_tmp' ]; then
            cp -r ${OPENCLAW_DIR}_tmp/.git ${OPENCLAW_DIR}/
            rm -rf ${OPENCLAW_DIR}_tmp
        fi
    "
else
    log_info "克隆 OpenClaw 仓库..."
    sudo -u ${AGENT_USER} bash -c "
        export HOME=${AGENT_HOME}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        git clone https://github.com/browser-use/browser-use.git ${OPENCLAW_DIR} 2>&1
    "
fi

# --- 4.2 部署配置文件 ---
log_step "4.2 - 部署 OpenClaw 配置文件"

# 确保工作区目录存在
sudo -u ${AGENT_USER} mkdir -p "${OPENCLAW_DIR}/workspace"

# 复制配置文件
if [ -f "${DEPLOY_DIR}/config.json" ]; then
    sudo cp "${DEPLOY_DIR}/config.json" "${OPENCLAW_DIR}/config.json"
    sudo chown ${AGENT_USER}:staff "${OPENCLAW_DIR}/config.json"
    sudo chmod 600 "${OPENCLAW_DIR}/config.json"
    log_info "config.json 已部署 (权限: 600 - 仅 claw_agent 可读写)"
else
    log_error "找不到 ${DEPLOY_DIR}/config.json"
fi

# --- 4.3 创建 .env 文件 (备选配置方式) ---
log_step "4.3 - 创建 .env 环境变量文件"

sudo tee "${OPENCLAW_DIR}/.env" > /dev/null << 'ENV_EOF'
# OpenClaw 环境配置
# 由 openclaw-deploy 自动生成

# LLM 配置 - 指向本机 Ollama
OPENAI_API_BASE=http://127.0.0.1:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=qwen2.5-coder:14b

# 浏览器代理 - 指向 zayl 的 Surge
BROWSER_PROXY=http://127.0.0.1:6152

# 安全限制
SANDBOX_ENABLED=true
WORKSPACE_DIR=/Users/claw_agent/openclaw/workspace
ENV_EOF

sudo chown ${AGENT_USER}:staff "${OPENCLAW_DIR}/.env"
sudo chmod 600 "${OPENCLAW_DIR}/.env"
log_info ".env 已部署 (权限: 600)"

# --- 4.4 安装依赖 ---
log_step "4.4 - 安装 Node.js/Python 依赖"

sudo -u ${AGENT_USER} bash -c '
    export HOME=/Users/claw_agent
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    
    cd /Users/claw_agent/openclaw
    
    # 根据项目类型安装依赖
    if [ -f "package.json" ]; then
        echo "[INFO] 检测到 Node.js 项目，执行 npm install..."
        npm install 2>&1 | tail -5
    fi
    
    if [ -f "requirements.txt" ]; then
        echo "[INFO] 检测到 Python 项目，安装 pip 依赖..."
        python3 -m pip install -r requirements.txt 2>&1 | tail -5
    fi
    
    if [ -f "pyproject.toml" ]; then
        echo "[INFO] 检测到 pyproject.toml，安装 Python 项目..."
        python3 -m pip install -e "." 2>&1 | tail -5
    fi
' || log_warn "依赖安装需要在代码克隆完成后执行"

# --- 4.5 安装 Playwright 浏览器内核 ---
log_step "4.5 - 安装 Playwright Chromium 浏览器内核"

sudo -u ${AGENT_USER} bash -c '
    export HOME=/Users/claw_agent
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    
    cd /Users/claw_agent/openclaw
    
    # 安装 playwright
    if command -v npx > /dev/null 2>&1; then
        npx playwright install chromium 2>&1 | tail -3
    elif command -v playwright > /dev/null 2>&1; then
        playwright install chromium 2>&1 | tail -3
    else
        echo "[WARN] playwright 未找到，将在 npm install 后自动安装"
    fi
' || log_warn "Playwright 安装将在项目依赖安装后进行"

echo ""
echo "============================================="
echo "  第四步完成 - OpenClaw 已部署"
echo "============================================="
echo ""
echo "  部署位置: ${OPENCLAW_DIR}"
echo "  配置文件: ${OPENCLAW_DIR}/config.json"
echo "  环境变量: ${OPENCLAW_DIR}/.env"
echo "  工作区:   ${OPENCLAW_DIR}/workspace"
