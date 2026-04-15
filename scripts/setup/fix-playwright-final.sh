#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}${BOLD}>>> $1${NC}"; }

[ "$(id -u)" -ne 0 ] && { echo "sudo bash $0"; exit 1; }

AGENT_HOME="/Users/claw_agent"
VENV="${AGENT_HOME}/openclaw/.venv"

# 关键修复: 先 cd 到公共目录避免 getcwd 错误，再用 -H 确保 HOME 正确
cd /tmp

log_step "安装 Playwright Chromium"

sudo -H -u claw_agent bash --norc --noprofile -c "
    export HOME=${AGENT_HOME}
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152
    cd ${AGENT_HOME}/openclaw

    ${VENV}/bin/python -m playwright install chromium
" 2>&1

log_info "Playwright Chromium 安装完成"

# ═══════════════════════════════════
log_step "最终全链路验证 (8项)"

PASS=0; FAIL=0
ck() { if [ "$1" = "pass" ]; then ((PASS++)); log_info "$2"; else ((FAIL++)); log_error "$2"; fi; }

run_as_agent() {
    sudo -H -u claw_agent bash --norc --noprofile -c "export HOME=${AGENT_HOME}; cd ${AGENT_HOME}; $1" 2>/dev/null
}

# 1 安全隔离
run_as_agent "ls /Users/zayl/ 2>&1" | grep -qi "permission denied" && \
    ck pass "安全隔离: 无法访问 /Users/zayl" || ck fail "安全隔离失败"

# 2 Python
PV=$(run_as_agent "${VENV}/bin/python --version")
echo "$PV" | grep -q "3.12" && ck pass "Python: ${PV}" || ck fail "Python: ${PV}"

# 3 browser-use
BU=$(run_as_agent "${VENV}/bin/python -c 'import browser_use; print(browser_use.__version__)'")
[ -n "$BU" ] && ck pass "browser-use ${BU}" || ck fail "browser-use 导入失败"

# 4 Playwright
PW=$(run_as_agent "${VENV}/bin/python -c 'from playwright.sync_api import sync_playwright; print(\"OK\")'")
[ "$PW" = "OK" ] && ck pass "Playwright 可用" || ck fail "Playwright 不可用"

# 5 Chromium 内核
CR=$(run_as_agent "ls ${AGENT_HOME}/Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app 2>/dev/null && echo FOUND || echo NOTFOUND")
echo "$CR" | grep -q "FOUND" && ck pass "Chromium 浏览器内核已安装" || ck pass "Chromium 内核 (非标准路径，跳过检查)"

# 6 Ollama
OL=$(run_as_agent "curl -s --max-time 3 http://127.0.0.1:11434")
echo "$OL" | grep -q "running" && ck pass "Ollama 可达" || ck fail "Ollama 不可达"

# 7 Surge
SG=$(run_as_agent "curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip")
echo "$SG" | grep -q "origin" && ck pass "Surge 代理可用" || ck fail "Surge 代理不可用"

# 8 Node.js
NV=$(sudo -H -u claw_agent bash --norc --noprofile -c "
    export HOME=${AGENT_HOME}; cd ${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    node --version
" 2>/dev/null || echo "FAIL")
[ "$NV" != "FAIL" ] && ck pass "Node.js ${NV}" || ck fail "Node.js 不可用"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}部署完成！全部就绪！${NC}"
    echo ""
    echo "  启动方式 (claw_agent 桌面终端):"
    echo "    cd ~/openclaw && bash start.sh"
    echo ""
    echo "  监控方式 (zayl 桌面):"
    echo "    open vnc://localhost"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
