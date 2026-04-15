#!/bin/bash
#=============================================================================
# 修复: 安装 Python 3.12 + browser-use + Playwright
# 运行方式: sudo bash fix-python-install.sh
#
# 根因:
#   1. 系统 Python 3.9 太旧，browser-use 需要 3.11+
#   2. sudo -u claw_agent 时 cwd 在 zayl home (已 chmod 700)
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
VENV_DIR="${OPENCLAW_DIR}/.venv"

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root: sudo bash $0"; exit 1
fi

# ═════════════════════════════════════════════
# 1. 用 Homebrew 安装 Python 3.12 (以 zayl 身份)
# ═════════════════════════════════════════════
log_step "1/4: 安装 Python 3.12 (Homebrew)"

if /opt/homebrew/bin/python3.12 --version > /dev/null 2>&1; then
    log_info "Python 3.12 已安装: $(/opt/homebrew/bin/python3.12 --version)"
else
    log_info "通过 Homebrew 安装 Python 3.12..."
    sudo -u zayl /opt/homebrew/bin/brew install python@3.12 2>&1 | tail -5
    log_info "Python 3.12 安装完成: $(/opt/homebrew/bin/python3.12 --version)"
fi

PYTHON_BIN="/opt/homebrew/bin/python3.12"
if [ ! -x "$PYTHON_BIN" ]; then
    log_error "Python 3.12 二进制不存在: $PYTHON_BIN"
    exit 1
fi

# ═════════════════════════════════════════════
# 2. 创建 Python venv
# ═════════════════════════════════════════════
log_step "2/4: 创建 Python 虚拟环境"

# 关键: 所有 sudo -u 命令必须先 cd 到 claw_agent 可访问的目录
if [ -d "${VENV_DIR}" ]; then
    log_info "venv 已存在，重建..."
    rm -rf "${VENV_DIR}"
fi

sudo -u ${NEW_USER} bash -c "
    cd ${OPENCLAW_DIR}
    ${PYTHON_BIN} -m venv ${VENV_DIR}
"
log_info "venv 创建完成: ${VENV_DIR}"

# 验证 venv Python 版本
VENV_PY_VER=$(sudo -u ${NEW_USER} bash -c "cd ${AGENT_HOME} && ${VENV_DIR}/bin/python --version" 2>&1)
log_info "venv Python: ${VENV_PY_VER}"

# ═════════════════════════════════════════════
# 3. 安装 browser-use + 依赖
# ═════════════════════════════════════════════
log_step "3/4: 安装 browser-use + 依赖"

sudo -u ${NEW_USER} bash -c "
    cd ${OPENCLAW_DIR}
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152

    echo '>>> 升级 pip...'
    ${VENV_DIR}/bin/pip install --upgrade pip 2>&1 | tail -3

    echo ''
    echo '>>> 安装 browser-use...'
    ${VENV_DIR}/bin/pip install . 2>&1 | tail -15

    echo ''
    echo '>>> 验证安装...'
    ${VENV_DIR}/bin/python -c 'import browser_use; print(\"browser_use 导入成功\")'
" 2>&1

# ═════════════════════════════════════════════
# 4. 安装 Playwright Chromium
# ═════════════════════════════════════════════
log_step "4/4: 安装 Playwright Chromium"

sudo -u ${NEW_USER} bash -c "
    cd ${OPENCLAW_DIR}
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152

    ${VENV_DIR}/bin/playwright install chromium 2>&1
" 2>&1

log_info "Playwright Chromium 安装完成"

# ═════════════════════════════════════════════
# 更新 .env 添加 venv 路径说明
# ═════════════════════════════════════════════
log_step "更新配置"

# 创建启动脚本
tee "${OPENCLAW_DIR}/start.sh" > /dev/null << 'START_EOF'
#!/bin/bash
# OpenClaw 启动脚本
# 使用方式: cd ~/openclaw && bash start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

# 激活 venv
source "${VENV_DIR}/bin/activate"

# 代理配置
export http_proxy="http://127.0.0.1:6152"
export https_proxy="http://127.0.0.1:6152"
export all_proxy="socks5://127.0.0.1:6153"
export no_proxy="localhost,127.0.0.1,::1,*.local"

echo "Python:    $(python --version)"
echo "Venv:      ${VENV_DIR}"
echo "Proxy:     ${http_proxy}"
echo "Ollama:    http://127.0.0.1:11434"
echo ""

# 根据项目类型启动
if [ -f "package.json" ]; then
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    npm start
elif [ -f "main.py" ]; then
    python main.py
else
    echo "请手动启动项目"
    echo "  Python: python -m browser_use"
    echo "  Node:   npm start"
fi
START_EOF

chown ${NEW_USER}:staff "${OPENCLAW_DIR}/start.sh"
chmod 755 "${OPENCLAW_DIR}/start.sh"
log_info "启动脚本: ${OPENCLAW_DIR}/start.sh"

# ═════════════════════════════════════════════
# 最终验证
# ═════════════════════════════════════════════
log_step "最终验证"

PASS=0; FAIL=0
run_check() {
    local result=$1 label=$2
    if [ "$result" = "pass" ]; then ((PASS++)); log_info "$label"
    else ((FAIL++)); log_error "$label"; fi
}

# 安全隔离
DIR_TEST=$(sudo -u ${NEW_USER} bash -c "cd ${AGENT_HOME} && ls /Users/zayl/ 2>&1" || true)
echo "$DIR_TEST" | grep -qi "permission denied\|operation not permitted" && \
    run_check "pass" "安全隔离: claw_agent 无法访问 /Users/zayl/" || \
    run_check "fail" "安全隔离失败"

# Python
PY_VER=$(sudo -u ${NEW_USER} bash -c "cd ${AGENT_HOME} && ${VENV_DIR}/bin/python --version 2>&1" || echo "FAIL")
echo "$PY_VER" | grep -q "3.12" && run_check "pass" "Python ${PY_VER}" || run_check "fail" "Python 异常: ${PY_VER}"

# browser-use
BU_TEST=$(sudo -u ${NEW_USER} bash -c "cd ${AGENT_HOME} && ${VENV_DIR}/bin/python -c 'import browser_use; print(\"OK\")' 2>/dev/null" || echo "FAIL")
[ "$BU_TEST" = "OK" ] && run_check "pass" "browser-use 可导入" || run_check "fail" "browser-use 导入失败"

# Playwright
PW_TEST=$(sudo -u ${NEW_USER} bash -c "cd ${AGENT_HOME} && ${VENV_DIR}/bin/python -c 'from playwright.sync_api import sync_playwright; print(\"OK\")' 2>/dev/null" || echo "FAIL")
[ "$PW_TEST" = "OK" ] && run_check "pass" "Playwright 可用" || run_check "fail" "Playwright 不可用"

# Ollama
OL_TEST=$(sudo -u ${NEW_USER} bash -c "cd ${AGENT_HOME} && curl -s --max-time 3 http://127.0.0.1:11434 2>/dev/null" || echo "")
echo "$OL_TEST" | grep -q "running" && run_check "pass" "Ollama 可达" || run_check "fail" "Ollama 不可达"

# Surge
SG_TEST=$(sudo -u ${NEW_USER} bash -c "cd ${AGENT_HOME} && curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip 2>/dev/null" || echo "")
echo "$SG_TEST" | grep -q "origin" && run_check "pass" "Surge 代理可用" || run_check "fail" "Surge 代理不可用"

# Node
NODE_V=$(sudo -u ${NEW_USER} bash -c "
    cd ${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    node --version 2>/dev/null || echo FAIL
")
[ "$NODE_V" != "FAIL" ] && run_check "pass" "Node.js ${NODE_V}" || run_check "fail" "Node.js 不可用"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}全部就绪！部署完成！${NC}"
    echo ""
    echo "  启动方式 (在 claw_agent 桌面终端):"
    echo "    cd ~/openclaw && bash start.sh"
else
    echo -e "  ${RED}仍有 $FAIL 项失败${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
