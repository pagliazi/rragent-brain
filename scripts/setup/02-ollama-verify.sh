#!/bin/bash
#=============================================================================
# 第二步：Ollama 服务验证与安全审查
# 运行用户：zayl (管理员)
# 目的：验证 Ollama LaunchDaemon 配置正确且安全
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
echo "  第二步：Ollama 服务验证"
echo "============================================="
echo ""

# --- 2.1 检测 Ollama 运行 ---
OLLAMA_RESP=$(curl -s --max-time 5 http://127.0.0.1:11434 2>/dev/null || echo "FAIL")
if [ "$OLLAMA_RESP" = "Ollama is running" ]; then
    log_info "Ollama 正在运行 ✓"
else
    log_error "Ollama 未运行或无响应: $OLLAMA_RESP"
    echo ""
    echo "  修复方案 (需要 sudo):"
    echo "  sudo launchctl load /Library/LaunchDaemons/com.local.ollama.plist"
    exit 1
fi

# --- 2.2 安全性检查：绑定地址 ---
echo ""
log_info "安全性检查..."
PLIST_PATH="/Library/LaunchDaemons/com.local.ollama.plist"

if [ -f "$PLIST_PATH" ]; then
    OLLAMA_HOST_VAL=$(defaults read "$PLIST_PATH" EnvironmentVariables 2>/dev/null | grep OLLAMA_HOST | awk -F'"' '{print $2}' || echo "UNKNOWN")
    echo "  LaunchDaemon 绑定地址: $OLLAMA_HOST_VAL"
    
    if echo "$OLLAMA_HOST_VAL" | grep -q "0.0.0.0"; then
        log_warn "Ollama 绑定 0.0.0.0 - 对整个网络开放！"
        log_warn "建议改为 127.0.0.1:11434 (仅本机访问，更安全)"
    elif echo "$OLLAMA_HOST_VAL" | grep -q "127.0.0.1"; then
        log_info "Ollama 仅绑定 127.0.0.1 - 安全 ✓ (本机用户均可通过 localhost 访问)"
    fi
else
    log_warn "无法读取 LaunchDaemon 配置文件"
fi

# --- 2.3 检查可用模型 ---
echo ""
log_info "已安装模型:"
ollama list 2>/dev/null | while IFS= read -r line; do
    echo "  $line"
done

# --- 2.4 API 连通性测试 ---
echo ""
log_info "API 连通性测试..."

TAGS_RESP=$(curl -s --max-time 5 http://127.0.0.1:11434/api/tags 2>/dev/null)
if echo "$TAGS_RESP" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    MODEL_COUNT=$(echo "$TAGS_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null)
    log_info "API /api/tags 响应正常，共 $MODEL_COUNT 个模型 ✓"
else
    log_error "API 响应异常"
fi

# --- 2.5 OpenAI 兼容接口测试 (OpenClaw 依赖) ---
MODELS_RESP=$(curl -s --max-time 5 http://127.0.0.1:11434/v1/models 2>/dev/null)
if echo "$MODELS_RESP" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    log_info "OpenAI 兼容接口 /v1/models 可用 ✓"
else
    log_warn "OpenAI 兼容接口可能不可用"
fi

echo ""
echo "============================================="
echo "  第二步完成 - Ollama 服务状态健康"
echo "============================================="
