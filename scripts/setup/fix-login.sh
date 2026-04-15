#!/bin/bash
#=============================================================================
# 修复 claw_agent 登录: 授予 Secure Token + 重置密码
# 运行方式: sudo bash fix-login.sh
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
echo -e "${CYAN}修复 claw_agent 登录${NC}"
echo ""
echo "需要 zayl 的密码来授予 Secure Token"
echo ""

# 获取 zayl 密码
read -s -p "zayl 管理员密码: " ADMIN_PASS
echo ""

# 获取 claw_agent 新密码
read -s -p "claw_agent 新密码: " AGENT_PASS
echo ""
read -s -p "确认 claw_agent 密码: " AGENT_PASS2
echo ""

if [ "$AGENT_PASS" != "$AGENT_PASS2" ]; then
    log_error "两次密码不一致"
    exit 1
fi

echo ""

# 用 sysadminctl 授予 Secure Token 并设置密码
# 这个命令会同时: 授予 Token + 设置 AuthenticationAuthority + 设置密码
sysadminctl -secureTokenOn claw_agent \
    -password "$AGENT_PASS" \
    -adminUser zayl \
    -adminPassword "$ADMIN_PASS" 2>&1

echo ""

# 验证
TOKEN_STATUS=$(sysadminctl -secureTokenStatus claw_agent 2>&1)
if echo "$TOKEN_STATUS" | grep -q "ENABLED"; then
    log_info "Secure Token 已启用"
else
    log_error "Secure Token 仍未启用: $TOKEN_STATUS"
    echo ""
    echo "  备选方案: 在系统设置 → 用户与群组 中删除 claw_agent 并重新创建"
    exit 1
fi

AUTH=$(dscl . -read /Users/claw_agent AuthenticationAuthority 2>&1)
if echo "$AUTH" | grep -q "ShadowHash\|SecureToken"; then
    log_info "AuthenticationAuthority 已恢复"
else
    log_error "AuthenticationAuthority 异常: $AUTH"
fi

echo ""
log_info "修复完成。现在可以用新密码登录 claw_agent。"
echo ""
echo "  登录方式:"
echo "  - 菜单栏右上角切换用户 → Claw Agent"
echo "  - 或 锁屏后选择 Claw Agent"
echo ""
