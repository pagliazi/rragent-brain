#!/bin/bash
#=============================================================================
# OpenClaw "三位一体" 主控部署脚本
# Mac Mini M4 - macOS 15.3.1 (Sequoia)
#
# 架构：zayl (管理员) + ollama_runner (AI) + claw_agent (Agent)
#
# 使用方式：
#   sudo bash master-deploy.sh
#
# 或逐步执行：
#   bash 01-surge-config.sh          # 无需 sudo
#   bash 02-ollama-verify.sh         # 无需 sudo
#   sudo bash 03-claw-agent-setup.sh  # 需要 sudo
#   sudo bash 04-deploy-openclaw.sh    # 需要 sudo
#   sudo bash 05-verify-and-harden.sh  # 需要 sudo
#=============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   OpenClaw 三位一体 部署系统                          ║${NC}"
echo -e "${BOLD}║   Mac Mini M4 · macOS 15.3.1                         ║${NC}"
echo -e "${BOLD}╠═══════════════════════════════════════════════════════╣${NC}"
echo -e "${BOLD}║   ${CYAN}zayl${NC}${BOLD}          →  Surge 网络 + 系统管理              ║${NC}"
echo -e "${BOLD}║   ${CYAN}ollama_runner${NC}${BOLD} →  Ollama AI 大模型服务               ║${NC}"
echo -e "${BOLD}║   ${CYAN}claw_agent${NC}${BOLD}   →  OpenClaw Agent 执行者              ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════════════════════╝${NC}"
echo ""

# 检查 sudo
if [ "$(id -u)" -ne 0 ] && [ "$(whoami)" != "root" ]; then
    echo -e "${YELLOW}[提示]${NC} 部分步骤需要 sudo 权限"
    echo ""
    echo "  推荐逐步执行："
    echo "  1. bash ${SCRIPT_DIR}/01-surge-config.sh"
    echo "  2. bash ${SCRIPT_DIR}/02-ollama-verify.sh"
    echo "  3. sudo bash ${SCRIPT_DIR}/03-claw-agent-setup.sh"
    echo "  4. sudo bash ${SCRIPT_DIR}/04-deploy-openclaw.sh"
    echo "  5. sudo bash ${SCRIPT_DIR}/05-verify-and-harden.sh"
    echo ""
    exit 0
fi

echo -e "${CYAN}[1/5]${NC} Surge 网络基石配置..."
bash "${SCRIPT_DIR}/01-surge-config.sh"
echo ""

echo -e "${CYAN}[2/5]${NC} Ollama 服务验证..."
bash "${SCRIPT_DIR}/02-ollama-verify.sh"
echo ""

echo -e "${CYAN}[3/5]${NC} claw_agent 环境部署..."
bash "${SCRIPT_DIR}/03-claw-agent-setup.sh"
echo ""

echo -e "${CYAN}[4/5]${NC} OpenClaw 代码部署..."
bash "${SCRIPT_DIR}/04-deploy-openclaw.sh"
echo ""

echo -e "${CYAN}[5/5]${NC} 验证与安全加固..."
bash "${SCRIPT_DIR}/05-verify-and-harden.sh"
echo ""

echo -e "${GREEN}${BOLD}部署完成！${NC}"
echo ""
echo "  下一步操作："
echo "  1. 在 Surge 中确认 allow-wifi-access = true"
echo "  2. 切换到 claw_agent 用户登录桌面"
echo "  3. 在 claw_agent 桌面终端中执行："
echo "     cd ~/openclaw && npm start"
echo "  4. 首次运行时，授予 Terminal 辅助功能和屏幕录制权限"
echo "  5. 回到 zayl 桌面，通过'屏幕共享.app'监控 claw_agent"
echo "     连接地址: vnc://localhost (登录 claw_agent)"
