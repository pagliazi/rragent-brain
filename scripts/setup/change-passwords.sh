#!/bin/bash
#=============================================================================
# 修改 ollama_runner 和 claw_agent 账号密码
# 运行方式: sudo bash change-passwords.sh
#=============================================================================
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[✓]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }

[ "$(id -u)" -ne 0 ] && { echo "sudo bash $0"; exit 1; }

echo ""
echo -e "${CYAN}修改用户登录密码${NC}"
echo ""

# --- ollama_runner ---
echo "═══ ollama_runner ═══"
read -s -p "请输入 ollama_runner 的新密码: " OL_PASS
echo ""
read -s -p "再次确认密码: " OL_PASS2
echo ""

if [ "$OL_PASS" != "$OL_PASS2" ]; then
    log_error "两次密码不一致，跳过 ollama_runner"
else
    sysadminctl -resetPasswordFor ollama_runner -newPassword "$OL_PASS" -adminUser zayl -adminPassword - 2>/dev/null || \
    dscl . -passwd /Users/ollama_runner "$OL_PASS"
    log_info "ollama_runner 密码已修改"
fi

echo ""

# --- claw_agent ---
echo "═══ claw_agent ═══"
read -s -p "请输入 claw_agent 的新密码: " CA_PASS
echo ""
read -s -p "再次确认密码: " CA_PASS2
echo ""

if [ "$CA_PASS" != "$CA_PASS2" ]; then
    log_error "两次密码不一致，跳过 claw_agent"
else
    sysadminctl -resetPasswordFor claw_agent -newPassword "$CA_PASS" -adminUser zayl -adminPassword - 2>/dev/null || \
    dscl . -passwd /Users/claw_agent "$CA_PASS"
    log_info "claw_agent 密码已修改"
fi

echo ""
log_info "完成。新密码立即生效，下次登录时使用。"
