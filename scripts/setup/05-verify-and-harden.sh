#!/bin/bash
#=============================================================================
# 第五步：启动验证与安全加固
# 运行用户：zayl (管理员, 需要 sudo)
# 目的：全链路验证 + 安全加固 + 监控配置
#=============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

AGENT_USER="claw_agent"
AGENT_HOME="/Users/${AGENT_USER}"
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

check_pass() { ((PASS_COUNT++)); log_info "$1"; }
check_fail() { ((FAIL_COUNT++)); log_error "$1"; }
check_warn() { ((WARN_COUNT++)); log_warn "$1"; }

echo "============================================="
echo "  第五步：全链路验证与安全加固"
echo "============================================="

# ═════════════════════════════════════════════
# A. 服务连通性验证
# ═════════════════════════════════════════════
echo ""
echo "━━━ A. 服务连通性验证 ━━━"
echo ""

# A1: Ollama 可达
OLLAMA_RESP=$(curl -s --max-time 3 http://127.0.0.1:11434 2>/dev/null || echo "")
if [ "$OLLAMA_RESP" = "Ollama is running" ]; then
    check_pass "Ollama API 可达 (127.0.0.1:11434)"
else
    check_fail "Ollama API 不可达"
fi

# A2: Ollama OpenAI 兼容接口
OPENAI_RESP=$(curl -s --max-time 3 http://127.0.0.1:11434/v1/models 2>/dev/null || echo "")
if echo "$OPENAI_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d.get('data',[]))>0" 2>/dev/null; then
    check_pass "Ollama OpenAI 兼容接口正常 (/v1/models)"
else
    check_warn "Ollama OpenAI 兼容接口异常"
fi

# A3: Surge HTTP 代理
SURGE_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 -x http://127.0.0.1:6152 http://httpbin.org/ip 2>/dev/null || echo "FAIL")
if [ "$SURGE_HTTP" = "200" ]; then
    check_pass "Surge HTTP 代理可用 (6152)"
elif [ "$SURGE_HTTP" = "503" ] || [ "$SURGE_HTTP" = "FAIL" ]; then
    check_warn "Surge HTTP 代理异常 (状态: $SURGE_HTTP) - 请检查 allow-wifi-access 配置"
else
    check_warn "Surge HTTP 代理返回: $SURGE_HTTP"
fi

# A4: Surge SOCKS5 代理
SURGE_SOCKS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 --socks5 127.0.0.1:6153 http://httpbin.org/ip 2>/dev/null || echo "FAIL")
if [ "$SURGE_SOCKS" = "200" ]; then
    check_pass "Surge SOCKS5 代理可用 (6153)"
else
    check_warn "Surge SOCKS5 代理异常 (状态: $SURGE_SOCKS)"
fi

# ═════════════════════════════════════════════
# B. 安全隔离验证
# ═════════════════════════════════════════════
echo ""
echo "━━━ B. 安全隔离验证 ━━━"
echo ""

# B1: claw_agent 非管理员
if ! dscl . -read /Groups/admin GroupMembership 2>/dev/null | grep -qw "${AGENT_USER}"; then
    check_pass "${AGENT_USER} 不在 admin 组 (无 sudo 权限)"
else
    check_fail "${AGENT_USER} 在 admin 组！严重安全风险！"
fi

# B2: 文件隔离 - 无法读取 zayl 的文件
ISOLATION_1=$(sudo -u ${AGENT_USER} cat /Users/zayl/.zshrc 2>&1 || true)
if echo "$ISOLATION_1" | grep -qi "permission denied"; then
    check_pass "文件隔离: ${AGENT_USER} 无法读取 /Users/zayl/.zshrc"
else
    check_fail "文件隔离失败: ${AGENT_USER} 可读取 zayl 的文件！"
fi

# B3: 文件隔离 - 无法读取 .ssh
ISOLATION_2=$(sudo -u ${AGENT_USER} ls /Users/zayl/.ssh/ 2>&1 || true)
if echo "$ISOLATION_2" | grep -qi "permission denied"; then
    check_pass "文件隔离: ${AGENT_USER} 无法读取 /Users/zayl/.ssh/"
else
    check_fail "SSH 密钥泄露风险: ${AGENT_USER} 可读取 zayl 的 .ssh 目录！"
fi

# B4: 无法杀死主用户进程
ISOLATION_3=$(sudo -u ${AGENT_USER} kill -0 1141 2>&1 || true)
if echo "$ISOLATION_3" | grep -qi "not permitted\|operation not permitted"; then
    check_pass "进程隔离: ${AGENT_USER} 无法干扰 zayl 的进程"
else
    check_warn "进程隔离检查不确定"
fi

# B5: /Users/Shared 无敏感文件
SHARED_FILES=$(ls -la /Users/Shared/ 2>/dev/null | grep -v "^\." | grep -v "total" | grep -v "Relocated" | grep -v "SC Info" | wc -l | tr -d ' ')
if [ "$SHARED_FILES" -le 1 ]; then
    check_pass "/Users/Shared 无敏感文件"
else
    check_warn "/Users/Shared 中有额外文件，请检查"
fi

# ═════════════════════════════════════════════
# C. 系统稳定性验证
# ═════════════════════════════════════════════
echo ""
echo "━━━ C. 系统稳定性验证 ━━━"
echo ""

# C1: 系统不休眠
SLEEP_VAL=$(pmset -g | grep "^ sleep" | awk '{print $2}')
if [ "$SLEEP_VAL" = "0" ]; then
    check_pass "系统睡眠已禁用 (sleep = 0)"
else
    check_warn "系统睡眠设为 ${SLEEP_VAL} 分钟，建议设为 0"
fi

# C2: caffeinate 运行
if pgrep caffeinate > /dev/null 2>&1; then
    check_pass "caffeinate 正在运行 (防休眠)"
else
    check_warn "caffeinate 未运行"
fi

# C3: Surge App Nap 防护
APPNAP=$(defaults read com.nssurge.surge-mac NSAppSleepDisabled 2>/dev/null || echo "0")
if [ "$APPNAP" = "1" ]; then
    check_pass "Surge App Nap 防护已启用"
else
    check_warn "Surge App Nap 防护未设置"
fi

# C4: Ollama KeepAlive
if [ -f /Library/LaunchDaemons/com.local.ollama.plist ]; then
    check_pass "Ollama LaunchDaemon 存在 (自动重启)"
else
    check_fail "Ollama LaunchDaemon 不存在"
fi

# C5: 屏幕共享
if pgrep screensharingd > /dev/null 2>&1; then
    check_pass "屏幕共享服务运行中 (可远程监控 claw_agent)"
else
    check_warn "屏幕共享未启用"
fi

# ═════════════════════════════════════════════
# D. 配置文件验证
# ═════════════════════════════════════════════
echo ""
echo "━━━ D. 配置文件验证 ━━━"
echo ""

# D1: .zshrc
if [ -f "${AGENT_HOME}/.zshrc" ]; then
    if grep -q "http_proxy" "${AGENT_HOME}/.zshrc" 2>/dev/null; then
        check_pass "${AGENT_USER} .zshrc 代理配置已就绪"
    else
        check_warn "${AGENT_USER} .zshrc 缺少代理配置"
    fi
else
    check_fail "${AGENT_USER} .zshrc 不存在"
fi

# D2: config.json
if [ -f "${AGENT_HOME}/openclaw/config.json" ]; then
    if python3 -c "import json; json.load(open('${AGENT_HOME}/openclaw/config.json'))" 2>/dev/null; then
        check_pass "config.json 存在且 JSON 格式有效"
    else
        check_fail "config.json JSON 格式无效"
    fi
else
    check_warn "config.json 尚未部署 (请先运行第四步)"
fi

# D3: .env
if [ -f "${AGENT_HOME}/openclaw/.env" ]; then
    PERM=$(stat -f "%Lp" "${AGENT_HOME}/openclaw/.env" 2>/dev/null || echo "")
    if [ "$PERM" = "600" ]; then
        check_pass ".env 存在且权限正确 (600)"
    else
        check_warn ".env 权限为 $PERM，建议设为 600"
    fi
else
    check_warn ".env 尚未部署 (请先运行第四步)"
fi

# ═════════════════════════════════════════════
# 总结报告
# ═════════════════════════════════════════════
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "  验证结果:  ${GREEN}通过 $PASS_COUNT${NC}  |  ${YELLOW}警告 $WARN_COUNT${NC}  |  ${RED}失败 $FAIL_COUNT${NC}"
echo ""

if [ $FAIL_COUNT -eq 0 ] && [ $WARN_COUNT -le 2 ]; then
    echo -e "  ${GREEN}系统状态良好，可以启动 OpenClaw${NC}"
elif [ $FAIL_COUNT -eq 0 ]; then
    echo -e "  ${YELLOW}系统基本就绪，建议处理警告项后启动${NC}"
else
    echo -e "  ${RED}存在 $FAIL_COUNT 个失败项，必须修复后才能安全运行${NC}"
fi
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
