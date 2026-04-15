#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }

[ "$(id -u)" -ne 0 ] && { echo "sudo bash $0"; exit 1; }

cd /tmp

AGENT_HOME="/Users/claw_agent"
VENV="${AGENT_HOME}/openclaw/.venv"
PASS=0; FAIL=0

ck() { if [ "$1" = "pass" ]; then ((PASS++)); log_info "$2"; else ((FAIL++)); log_error "$2"; fi; }

# 用 exit code 判断而非 grep 文本
agent_run() {
    sudo -H -u claw_agent bash --norc --noprofile -c "export HOME=${AGENT_HOME}; cd ${AGENT_HOME}; $1"
}

echo ""
echo -e "${CYAN}${BOLD}>>> 最终全链路验证 (10项)${NC}"
echo ""

# 1 安全隔离 (用 exit code)
agent_run "ls /Users/zayl/ >/dev/null 2>&1" && ck fail "安全隔离: claw_agent 可访问 zayl" || ck pass "安全隔离: claw_agent 无法访问 /Users/zayl"

# 2 SSH 隔离
agent_run "ls /Users/zayl/.ssh/ >/dev/null 2>&1" && ck fail "SSH 泄露" || ck pass "SSH 隔离: 无法访问 /Users/zayl/.ssh"

# 3 Python
PV=$(agent_run "${VENV}/bin/python --version 2>&1" || echo "FAIL")
echo "$PV" | grep -q "3.12" && ck pass "Python: ${PV}" || ck fail "Python: ${PV}"

# 4 browser-use
BU=$(agent_run "${VENV}/bin/python -c 'import browser_use; print(browser_use.__version__)' 2>&1" || echo "FAIL")
[ "$BU" != "FAIL" ] && ck pass "browser-use ${BU}" || ck fail "browser-use 导入失败"

# 5 Playwright
PW=$(agent_run "${VENV}/bin/python -c 'from playwright.sync_api import sync_playwright; print(\"OK\")' 2>&1" || echo "FAIL")
[ "$PW" = "OK" ] && ck pass "Playwright 可用" || ck fail "Playwright 不可用"

# 6 Chromium 内核
agent_run "test -d ${AGENT_HOME}/Library/Caches/ms-playwright/chromium-1208" && ck pass "Chromium 浏览器内核已安装" || ck fail "Chromium 内核缺失"

# 7 Ollama
OL=$(agent_run "curl -s --max-time 3 http://127.0.0.1:11434 2>/dev/null" || echo "")
echo "$OL" | grep -q "running" && ck pass "Ollama API 可达 (127.0.0.1:11434)" || ck fail "Ollama 不可达"

# 8 Surge 代理
SG=$(agent_run "curl -s --max-time 5 -x http://127.0.0.1:6152 https://httpbin.org/ip 2>/dev/null" || echo "")
echo "$SG" | grep -q "origin" && ck pass "Surge HTTP 代理可用 (6152)" || ck fail "Surge 代理不可用"

# 9 Node.js
NV=$(sudo -H -u claw_agent bash --norc --noprofile -c "
    export HOME=${AGENT_HOME}; cd ${AGENT_HOME}
    export NVM_DIR='${AGENT_HOME}/.nvm'
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    node --version
" 2>/dev/null || echo "FAIL")
[ "$NV" != "FAIL" ] && ck pass "Node.js ${NV}" || ck fail "Node.js 不可用"

# 10 配置文件完整性
CONF_OK=true
[ -f "${AGENT_HOME}/openclaw/config.json" ] || CONF_OK=false
[ -f "${AGENT_HOME}/openclaw/.env" ] || CONF_OK=false
[ -d "${AGENT_HOME}/openclaw/workspace" ] || CONF_OK=false
[ -f "${AGENT_HOME}/openclaw/start.sh" ] || CONF_OK=false
$CONF_OK && ck pass "配置文件完整 (config.json + .env + workspace + start.sh)" || ck fail "配置文件不完整"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}通过: $PASS${NC}  |  ${RED}失败: $FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}全部通过！部署完成！${NC}"
    echo ""
    echo "  ┌─────────────────────────────────────────────┐"
    echo "  │  启动 (切换到 claw_agent 桌面终端):          │"
    echo "  │    cd ~/openclaw && bash start.sh            │"
    echo "  │                                              │"
    echo "  │  监控 (zayl 桌面):                           │"
    echo "  │    open vnc://localhost                       │"
    echo "  │                                              │"
    echo "  │  首次运行需授予权限:                          │"
    echo "  │    系统设置 → 隐私与安全性 → 辅助功能         │"
    echo "  │    系统设置 → 隐私与安全性 → 屏幕录制         │"
    echo "  └─────────────────────────────────────────────┘"
else
    echo -e "  ${RED}有 $FAIL 项失败，请检查上方输出${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
