#!/bin/bash
#=============================================================================
# 修复: 克隆 OpenClaw 到已存在的目录 + 完成后续部署步骤
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

NEW_USER="claw_agent"
AGENT_HOME="/Users/${NEW_USER}"
OPENCLAW_DIR="${AGENT_HOME}/openclaw"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root: sudo bash $0"
    exit 1
fi

# =============================================
# 修复克隆
# =============================================
log_step "修复: 重新克隆 OpenClaw"

# 保存 workspace，删除目录，重新克隆
if [ -d "${OPENCLAW_DIR}" ] && [ ! -d "${OPENCLAW_DIR}/.git" ]; then
    log_info "移除非 git 目录..."
    mv "${OPENCLAW_DIR}/workspace" "${AGENT_HOME}/_workspace_tmp" 2>/dev/null || true
    rm -rf "${OPENCLAW_DIR}"

    log_info "克隆 browser-use..."
    sudo -u ${NEW_USER} bash -c "
        export HOME=${AGENT_HOME}
        export http_proxy=http://127.0.0.1:6152
        export https_proxy=http://127.0.0.1:6152
        git clone https://github.com/browser-use/browser-use.git ${OPENCLAW_DIR}
    " 2>&1

    # 恢复 workspace
    if [ -d "${AGENT_HOME}/_workspace_tmp" ]; then
        mv "${AGENT_HOME}/_workspace_tmp" "${OPENCLAW_DIR}/workspace"
    else
        sudo -u ${NEW_USER} mkdir -p "${OPENCLAW_DIR}/workspace"
    fi
    log_info "克隆完成，workspace 已恢复"
elif [ -d "${OPENCLAW_DIR}/.git" ]; then
    log_info "Git 仓库已存在，执行 pull..."
    sudo -u ${NEW_USER} bash -c "
        export HOME=${AGENT_HOME}
        cd ${OPENCLAW_DIR} && git pull --rebase
    " 2>&1 | tail -3
fi

# =============================================
# 部署配置文件
# =============================================
log_step "部署配置文件"

cp "${DEPLOY_DIR}/config.json" "${OPENCLAW_DIR}/config.json"
chown ${NEW_USER}:staff "${OPENCLAW_DIR}/config.json"
chmod 600 "${OPENCLAW_DIR}/config.json"
log_info "config.json 已部署 (权限 600)"

tee "${OPENCLAW_DIR}/.env" > /dev/null << 'ENV_EOF'
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

# =============================================
# 安装依赖
# =============================================
log_step "安装项目依赖"

sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152
    cd ${OPENCLAW_DIR}

    if [ -f 'package.json' ]; then
        echo '>>> npm install'
        npm install 2>&1 | tail -10
    fi

    if [ -f 'requirements.txt' ]; then
        echo '>>> pip install -r requirements.txt'
        python3 -m pip install --user -r requirements.txt 2>&1 | tail -10
    fi

    if [ -f 'pyproject.toml' ]; then
        echo '>>> pip install -e .'
        python3 -m pip install --user -e '.[dev]' 2>&1 | tail -10
    fi
" 2>&1

# =============================================
# 安装 Playwright
# =============================================
log_step "安装 Playwright Chromium"

sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152
    cd ${OPENCLAW_DIR}

    if python3 -c 'import playwright' 2>/dev/null; then
        python3 -m playwright install chromium 2>&1
    elif command -v npx > /dev/null 2>&1 && [ -d 'node_modules/playwright' ]; then
        npx playwright install chromium 2>&1
    else
        echo '[跳过] playwright 未安装'
    fi
" 2>&1 | tail -10

# =============================================
# 全链路验证
# =============================================
log_step "全链路安全验证"

PASS=0; FAIL=0; WARN=0
run_check() {
    local result=$1 label=$2
    if [ "$result" = "pass" ]; then ((PASS++)); log_info "$label"
    elif [ "$result" = "warn" ]; then ((WARN++)); log_warn "$label"
    else ((FAIL++)); log_error "$label"; fi
}

# 用户安全
! dscl . -read /Groups/admin GroupMembership 2>/dev/null | grep -qw "${NEW_USER}" && \
    run_check "pass" "claw_agent 非管理员" || run_check "fail" "claw_agent 是管理员"

# 文件隔离
sudo -u ${NEW_USER} cat /Users/zayl/.zshrc 2>&1 | grep -qi "permission denied" && \
    run_check "pass" "文件隔离: 无法读取 zayl 文件" || run_check "fail" "文件隔离失败"

sudo -u ${NEW_USER} ls /Users/zayl/.ssh/ 2>&1 | grep -qi "permission denied" && \
    run_check "pass" "SSH 隔离: 无法读取 zayl/.ssh" || run_check "fail" "SSH 密钥泄露"

# 服务
curl -s --max-time 3 http://127.0.0.1:11434 2>/dev/null | grep -q "running" && \
    run_check "pass" "Ollama 可达" || run_check "fail" "Ollama 不可达"

curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip 2>/dev/null | grep -q "origin" && \
    run_check "pass" "Surge 代理可用" || run_check "warn" "Surge 代理异常"

# 文件
[ -f "${OPENCLAW_DIR}/config.json" ] && run_check "pass" "config.json 存在" || run_check "fail" "config.json 缺失"
[ -f "${OPENCLAW_DIR}/.env" ] && run_check "pass" ".env 存在" || run_check "fail" ".env 缺失"
[ -d "${OPENCLAW_DIR}/.git" ] && run_check "pass" "git 仓库完整" || run_check "fail" "git 仓库不完整"
[ -d "${OPENCLAW_DIR}/workspace" ] && run_check "pass" "workspace 存在" || run_check "fail" "workspace 缺失"

# Node
NODE_CHECK=$(sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}; export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    node --version 2>/dev/null || echo FAIL
")
[ "$NODE_CHECK" != "FAIL" ] && run_check "pass" "Node.js ${NODE_CHECK}" || run_check "fail" "Node.js 未安装"

# 目录内容
FILE_COUNT=$(ls -1 ${OPENCLAW_DIR} 2>/dev/null | wc -l | tr -d ' ')
run_check "pass" "项目文件: ${FILE_COUNT} 个"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${YELLOW}警告: $WARN${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}部署成功！${NC}"
else
    echo -e "  ${RED}有 $FAIL 项失败，请检查上方输出${NC}"
fi
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  下一步:"
echo "  1. 切换到 claw_agent 用户登录桌面"
echo "  2. cd ~/openclaw && npm start"
echo "  3. 首次运行授予 Terminal 辅助功能 + 屏幕录制权限"
echo "  4. 回 zayl 桌面: open vnc://localhost"
echo ""
