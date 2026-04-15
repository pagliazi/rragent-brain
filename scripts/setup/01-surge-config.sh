#!/bin/bash
#=============================================================================
# 第一步：Surge 网络基石配置
# 运行用户：zayl (管理员)
# 目的：开放 Surge 本机端口给其他用户，设置防休眠
#=============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo "============================================="
echo "  第一步：Surge 网络基石配置"
echo "============================================="
echo ""

# --- 1.1 检测 Surge 是否运行 ---
if ! pgrep -x "Surge" > /dev/null 2>&1; then
    log_error "Surge 未运行！请先启动 Surge 再执行此脚本。"
    exit 1
fi
log_info "Surge 检测通过 (PID: $(pgrep -x 'Surge'))"

# --- 1.2 设置 Surge 防 App Nap ---
CURRENT_SETTING=$(defaults read com.nssurge.surge-mac NSAppSleepDisabled 2>/dev/null || echo "NOT_SET")
if [ "$CURRENT_SETTING" != "1" ]; then
    log_info "设置 Surge NSAppSleepDisabled = YES (防止 App Nap 休眠)"
    defaults write com.nssurge.surge-mac NSAppSleepDisabled -bool YES
    log_info "Surge App Nap 防护已启用"
else
    log_info "Surge App Nap 防护已存在，跳过"
fi

# --- 1.3 验证 Surge 代理端口 ---
echo ""
log_info "验证 Surge 代理端口连通性..."

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:6152 2>/dev/null || echo "FAIL")
SOCKS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 --socks5 127.0.0.1:6153 http://httpbin.org/ip 2>/dev/null || echo "FAIL")

echo ""
echo "  HTTP  代理 (127.0.0.1:6152): $HTTP_STATUS"
echo "  SOCKS5代理 (127.0.0.1:6153): $SOCKS_STATUS"
echo ""

if [ "$HTTP_STATUS" = "FAIL" ] || [ "$HTTP_STATUS" = "000" ]; then
    log_warn "HTTP 代理端口 6152 不可用！"
    log_warn "请在 Surge 中执行以下操作："
    echo ""
    echo "  ┌─────────────────────────────────────────────┐"
    echo "  │  1. 打开 Surge → 更多 → 配置文件 → 编辑     │"
    echo "  │  2. 在 [General] 段中确保包含:               │"
    echo "  │                                              │"
    echo "  │  allow-wifi-access = true                    │"
    echo "  │  http-listen = 0.0.0.0:6152                  │"
    echo "  │  socks5-listen = 0.0.0.0:6153                │"
    echo "  │  skip-proxy = 127.0.0.1, 192.168.0.0/16,    │"
    echo "  │    10.0.0.0/8, localhost, *.local             │"
    echo "  │                                              │"
    echo "  │  3. 保存并重新加载配置                         │"
    echo "  └─────────────────────────────────────────────┘"
    echo ""
    log_warn "配置完成后，请重新运行此脚本验证。"
else
    log_info "HTTP 代理端口正常"
fi

# --- 1.4 系统保活检查 ---
echo ""
log_info "检查系统保活状态..."
SLEEP_VAL=$(pmset -g | grep "^ sleep" | awk '{print $2}')
if [ "$SLEEP_VAL" = "0" ]; then
    log_info "系统睡眠已禁用 (sleep = 0)"
else
    log_warn "系统睡眠设置为 $SLEEP_VAL 分钟，建议设为 0"
    log_info "执行: sudo pmset -a sleep 0 disablesleep 1"
fi

if pgrep caffeinate > /dev/null 2>&1; then
    log_info "caffeinate 正在运行，Mac 不会休眠"
else
    log_warn "caffeinate 未运行。建议在后台运行："
    echo "  sudo caffeinate -s -m &"
fi

echo ""
echo "============================================="
echo "  第一步完成"
echo "============================================="
