#!/bin/bash
#=============================================================================
# 重命名用户：claw_tunnel → claw_agent
# 运行用户：zayl (管理员, 需要 sudo)
#=============================================================================
set -euo pipefail

OLD_USER="claw_tunnel"
NEW_USER="claw_agent"
OLD_HOME="/Users/${OLD_USER}"
NEW_HOME="/Users/${NEW_USER}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo "============================================="
echo "  重命名用户: ${OLD_USER} → ${NEW_USER}"
echo "============================================="
echo ""

# 前置检查
if [ "$(id -u)" -ne 0 ]; then
    log_error "需要 root 权限，请使用 sudo 运行"
    exit 1
fi

if ! dscl . -read /Users/${OLD_USER} UniqueID > /dev/null 2>&1; then
    log_error "用户 ${OLD_USER} 不存在"
    exit 1
fi

if dscl . -read /Users/${NEW_USER} UniqueID > /dev/null 2>&1; then
    log_error "用户 ${NEW_USER} 已存在，无法重命名"
    exit 1
fi

# 确保该用户没有活跃进程
PROCS=$(ps -u ${OLD_USER} -o pid= 2>/dev/null | wc -l | tr -d ' ')
if [ "$PROCS" -gt 0 ]; then
    log_error "${OLD_USER} 有 ${PROCS} 个活跃进程，请先注销该用户"
    exit 1
fi

log_info "开始重命名..."

# 1. 修改 RecordName
dscl . -change /Users/${OLD_USER} RecordName "${OLD_USER}" "${NEW_USER}"
log_info "RecordName 已更新"

# 2. 修改 RealName
dscl . -change /Users/${NEW_USER} RealName "" "Claw Agent"
log_info "RealName 已设为 Claw Agent"

# 3. 重命名 Home 目录
if [ -d "${OLD_HOME}" ] && [ ! -d "${NEW_HOME}" ]; then
    mv "${OLD_HOME}" "${NEW_HOME}"
    log_info "Home 目录已移动: ${OLD_HOME} → ${NEW_HOME}"
fi

# 4. 更新 NFSHomeDirectory
dscl . -change /Users/${NEW_USER} NFSHomeDirectory "${OLD_HOME}" "${NEW_HOME}"
log_info "NFSHomeDirectory 已更新"

# 5. 验证
echo ""
log_info "验证结果:"
echo "  用户名:    $(dscl . -read /Users/${NEW_USER} RecordName 2>/dev/null | awk '{print $2}')"
echo "  UID:       $(dscl . -read /Users/${NEW_USER} UniqueID 2>/dev/null | awk '{print $2}')"
echo "  Home:      $(dscl . -read /Users/${NEW_USER} NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
echo "  RealName:  $(dscl . -read /Users/${NEW_USER} RealName 2>/dev/null | tail -1 | xargs)"
echo ""

if [ -d "${NEW_HOME}" ]; then
    log_info "重命名成功: ${OLD_USER} → ${NEW_USER}"
else
    log_error "Home 目录不存在，请检查"
fi
