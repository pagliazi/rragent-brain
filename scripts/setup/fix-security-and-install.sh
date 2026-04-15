#!/bin/bash
#=============================================================================
# 修复: 安全隔离 + pip 升级 + Playwright 安装
# 运行方式: sudo bash fix-security-and-install.sh
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

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root: sudo bash $0"; exit 1
fi

# ═════════════════════════════════════════════
# 修复 1: zayl Home 目录权限加固
# ═════════════════════════════════════════════
log_step "修复 1: zayl Home 目录权限加固"

ZAYL_PERM=$(stat -f "%Lp" /Users/zayl)
echo "  当前权限: ${ZAYL_PERM} ($(stat -f '%Sp' /Users/zayl))"

if [ "$ZAYL_PERM" != "700" ]; then
    # 核心安全修复: 移除 staff 组对 zayl home 的 r-x 权限
    chmod 700 /Users/zayl
    NEW_PERM=$(stat -f "%Lp" /Users/zayl)
    log_info "权限已加固: ${ZAYL_PERM} → ${NEW_PERM} (drwx------)"
    log_info "claw_agent 现在无法进入 /Users/zayl/"
else
    log_info "权限已经是 700，无需修改"
fi

# 同时加固 ollama_runner 的 home
OLLAMA_PERM=$(stat -f "%Lp" /Users/ollama_runner 2>/dev/null || echo "N/A")
if [ "$OLLAMA_PERM" != "700" ] && [ "$OLLAMA_PERM" != "N/A" ]; then
    chmod 700 /Users/ollama_runner
    log_info "ollama_runner home 也已加固为 700"
fi

# ═════════════════════════════════════════════
# 修复 2: 升级 pip + 安装 browser-use
# ═════════════════════════════════════════════
log_step "修复 2: 升级 pip + 安装 browser-use"

sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152

    echo '>>> 升级 pip...'
    python3 -m pip install --user --upgrade pip 2>&1 | tail -3

    echo ''
    echo '>>> 安装 browser-use (非 editable 模式)...'
    cd ${OPENCLAW_DIR}
    python3 -m pip install --user . 2>&1 | tail -10
" 2>&1

log_info "pip + browser-use 安装完成"

# ═════════════════════════════════════════════
# 修复 3: 安装 Playwright Chromium
# ═════════════════════════════════════════════
log_step "修复 3: 安装 Playwright Chromium"

sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152
    export PATH=\"${AGENT_HOME}/.local/bin:\${PATH}\"

    if python3 -c 'import playwright' 2>/dev/null; then
        echo '>>> playwright 已安装，安装 chromium 浏览器内核...'
        python3 -m playwright install chromium 2>&1
    else
        echo '>>> 单独安装 playwright...'
        python3 -m pip install --user playwright 2>&1 | tail -5
        python3 -m playwright install chromium 2>&1
    fi
" 2>&1

log_info "Playwright 安装完成"

# ═════════════════════════════════════════════
# 重新验证安全隔离
# ═════════════════════════════════════════════
log_step "重新验证安全隔离"

PASS=0; FAIL=0
run_check() {
    local result=$1 label=$2
    if [ "$result" = "pass" ]; then ((PASS++)); log_info "$label"
    else ((FAIL++)); log_error "$label"; fi
}

# 测试1: claw_agent 无法进入 zayl home
DIR_TEST=$(sudo -u ${NEW_USER} ls /Users/zayl/ 2>&1 || true)
if echo "$DIR_TEST" | grep -qi "permission denied"; then
    run_check "pass" "目录隔离: claw_agent 无法列出 /Users/zayl/"
elif echo "$DIR_TEST" | grep -qi "operation not permitted"; then
    run_check "pass" "目录隔离: claw_agent 无法访问 /Users/zayl/"
else
    run_check "fail" "目录隔离失败: claw_agent 仍可访问 /Users/zayl/"
    echo "  实际输出: $DIR_TEST"
fi

# 测试2: claw_agent 无法进入 ollama_runner home
DIR_TEST2=$(sudo -u ${NEW_USER} ls /Users/ollama_runner/ 2>&1 || true)
if echo "$DIR_TEST2" | grep -qi "permission denied\|operation not permitted"; then
    run_check "pass" "目录隔离: claw_agent 无法访问 /Users/ollama_runner/"
else
    run_check "fail" "claw_agent 可访问 ollama_runner 目录"
fi

# 测试3: 确认 claw_agent 自己的目录正常
OWN_TEST=$(sudo -u ${NEW_USER} ls ${AGENT_HOME}/ 2>&1)
if echo "$OWN_TEST" | grep -q "openclaw"; then
    run_check "pass" "claw_agent 自身目录正常可访问"
else
    run_check "fail" "claw_agent 自身目录异常"
fi

# 测试4: Ollama 从 claw_agent 可达
OLLAMA_TEST=$(sudo -u ${NEW_USER} bash -c "curl -s --max-time 3 http://127.0.0.1:11434 2>/dev/null" || echo "")
if echo "$OLLAMA_TEST" | grep -q "running"; then
    run_check "pass" "claw_agent → Ollama API 可达"
else
    run_check "fail" "claw_agent → Ollama API 不可达"
fi

# 测试5: Surge 从 claw_agent 可达
SURGE_TEST=$(sudo -u ${NEW_USER} bash -c "curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip 2>/dev/null" || echo "")
if echo "$SURGE_TEST" | grep -q "origin"; then
    run_check "pass" "claw_agent → Surge 代理可用"
else
    run_check "fail" "claw_agent → Surge 代理不可用"
fi

# 测试6: Python + browser-use
PY_TEST=$(sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    python3 -c 'import browser_use; print(\"OK\")' 2>/dev/null || echo 'FAIL'
")
if [ "$PY_TEST" = "OK" ]; then
    run_check "pass" "browser-use Python 包可导入"
else
    run_check "fail" "browser-use Python 包导入失败"
fi

# 测试7: Playwright
PW_TEST=$(sudo -u ${NEW_USER} bash -c "
    export HOME=${AGENT_HOME}
    python3 -c 'from playwright.sync_api import sync_playwright; print(\"OK\")' 2>/dev/null || echo 'FAIL'
")
if [ "$PW_TEST" = "OK" ]; then
    run_check "pass" "Playwright 可用"
else
    run_check "fail" "Playwright 不可用"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}全部修复完成，系统安全且就绪！${NC}"
else
    echo -e "  ${RED}仍有 $FAIL 项问题${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
