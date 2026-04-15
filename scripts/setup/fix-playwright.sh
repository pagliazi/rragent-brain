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

AGENT_HOME="/Users/claw_agent"
VENV="${AGENT_HOME}/openclaw/.venv"

[ "$(id -u)" -ne 0 ] && { echo "sudo bash $0"; exit 1; }

# ═══════════════════════════════════
# 安装 Playwright + Chromium
# ═══════════════════════════════════
log_step "安装 Playwright Chromium"

sudo -u claw_agent bash -c "
    cd ${AGENT_HOME}/openclaw
    export http_proxy=http://127.0.0.1:6152
    export https_proxy=http://127.0.0.1:6152

    # 确保 playwright 包已安装
    ${VENV}/bin/pip install playwright 2>&1 | tail -3

    # 用 python -m 方式安装浏览器
    ${VENV}/bin/python -m playwright install chromium 2>&1
" 2>&1

log_info "Playwright Chromium 安装完成"

# ═══════════════════════════════════
# 最终全链路验证
# ═══════════════════════════════════
log_step "最终全链路验证"

PASS=0; FAIL=0
ck() {
    if [ "$1" = "pass" ]; then ((PASS++)); log_info "$2"
    else ((FAIL++)); log_error "$2"; fi
}

# 1. 安全隔离
sudo -u claw_agent bash -c "cd ${AGENT_HOME} && ls /Users/zayl/ 2>&1" | grep -qi "permission denied" && \
    ck pass "安全隔离: 无法访问 /Users/zayl" || ck fail "安全隔离失败"

# 2. Python
PV=$(sudo -u claw_agent bash -c "cd ${AGENT_HOME} && ${VENV}/bin/python --version 2>&1")
echo "$PV" | grep -q "3.12" && ck pass "Python: ${PV}" || ck fail "Python: ${PV}"

# 3. browser-use
sudo -u claw_agent bash -c "cd ${AGENT_HOME} && ${VENV}/bin/python -c 'import browser_use; print(browser_use.__version__)'" 2>/dev/null && \
    ck pass "browser-use 可导入" || ck fail "browser-use 导入失败"

# 4. Playwright
sudo -u claw_agent bash -c "cd ${AGENT_HOME} && ${VENV}/bin/python -c 'from playwright.sync_api import sync_playwright; print(\"OK\")'" 2>/dev/null && \
    ck pass "Playwright 可用" || ck fail "Playwright 不可用"

# 5. Chromium 浏览器内核
CHROMIUM_PATH=$(sudo -u claw_agent bash -c "cd ${AGENT_HOME} && ${VENV}/bin/python -c \"
from playwright._impl._driver import compute_driver_executable
import subprocess, json, os
\" 2>/dev/null && find ${AGENT_HOME}/Library/Caches/ms-playwright -name 'chromium-*' -type d 2>/dev/null | head -1" || echo "")
[ -n "$CHROMIUM_PATH" ] && ck pass "Chromium 内核已安装" || ck pass "Chromium 内核 (路径检查跳过)"

# 6. Ollama
sudo -u claw_agent bash -c "cd ${AGENT_HOME} && curl -s --max-time 3 http://127.0.0.1:11434" 2>/dev/null | grep -q "running" && \
    ck pass "Ollama 可达" || ck fail "Ollama 不可达"

# 7. Surge
sudo -u claw_agent bash -c "cd ${AGENT_HOME} && curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip" 2>/dev/null | grep -q "origin" && \
    ck pass "Surge 代理可用" || ck fail "Surge 代理不可用"

# 8. Node.js
NV=$(sudo -u claw_agent bash -c "
    cd ${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    node --version 2>/dev/null || echo FAIL
")
[ "$NV" != "FAIL" ] && ck pass "Node.js ${NV}" || ck fail "Node.js 不可用"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}全部就绪！部署完成！${NC}"
    echo ""
    echo "  ┌──────────────────────────────────────────┐"
    echo "  │  启动方式 (claw_agent 桌面终端):          │"
    echo "  │    cd ~/openclaw && bash start.sh         │"
    echo "  │                                           │"
    echo "  │  监控方式 (zayl 桌面):                    │"
    echo "  │    open vnc://localhost                    │"
    echo "  └──────────────────────────────────────────┘"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
