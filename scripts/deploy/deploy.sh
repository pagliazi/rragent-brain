#!/usr/bin/env bash
# ============================================================================
# RRAgent 一键部署脚本
# Usage: ./deploy.sh [--with-docker]
# ============================================================================

set -euo pipefail

# ── 颜色定义 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'  # No Color

# ── 辅助函数 ──
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()    { echo -e "\n${CYAN}${BOLD}==> $*${NC}"; }

die() {
    error "$*"
    exit 1
}

# ── 解析参数 ──
USE_DOCKER=false
for arg in "$@"; do
    case "$arg" in
        --with-docker) USE_DOCKER=true ;;
        --help|-h)
            echo "Usage: ./deploy.sh [--with-docker]"
            echo ""
            echo "Options:"
            echo "  --with-docker    Use docker-compose instead of local Python venv"
            echo "  --help, -h       Show this help message"
            exit 0
            ;;
        *)
            die "Unknown option: $arg (use --help for usage)"
            ;;
    esac
done

# ── 项目根目录 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BOLD}"
echo "  ____  ____   ____ _        ___        __"
echo " |  _ \\|  _ \\ / ___| |      / \\ \\      / /"
echo " | |_) | |_) | |   | |     / _ \\ \\ /\\ / / "
echo " |  _ <|  _ <| |___| |___ / ___ \\ V  V /  "
echo " |_| \\_\\_| \\_\\\\____|_____/_/   \\_\\_/\\_/   "
echo ""
echo "  A股量化智能体 - 一键部署"
echo -e "${NC}"

# ============================================================================
# Docker 模式
# ============================================================================
if [ "$USE_DOCKER" = true ]; then
    step "Docker 模式部署"

    # 检查 docker
    if ! command -v docker &>/dev/null; then
        die "未找到 docker,请先安装: https://docs.docker.com/get-docker/"
    fi

    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
        die "未找到 docker-compose,请先安装: https://docs.docker.com/compose/install/"
    fi

    # 生成 .env(如果不存在)
    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            cp .env.example .env
            info "已从 .env.example 生成 .env,请编辑后重新运行"
        else
            die "未找到 .env.example"
        fi
    fi

    info "启动 Docker 容器..."
    if docker compose version &>/dev/null 2>&1; then
        docker compose -f deploy/docker-compose.yaml up -d
    else
        docker-compose -f deploy/docker-compose.yaml up -d
    fi

    success "Docker 容器已启动！"
    echo ""
    echo -e "${GREEN}${BOLD}部署完成！${NC}"
    echo "  查看日志: docker compose -f deploy/docker-compose.yaml logs -f"
    echo "  停止服务: docker compose -f deploy/docker-compose.yaml down"
    exit 0
fi

# ============================================================================
# 本地模式
# ============================================================================

# ── 第1步:检查前置依赖 ──
step "检查前置依赖"

# Python — 优先找高版本
PY=""
for candidate in python3.12 python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
        if [ "$ver" -ge 11 ]; then
            PY="$candidate"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    die "未找到 Python 3.11+,请安装: https://python.org"
fi

PY_VERSION=$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$($PY -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PY -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    die "Python 版本过低: $PY_VERSION (需要 3.11+)"
fi
success "Python $PY_VERSION"

# Redis
if command -v redis-cli &>/dev/null; then
    success "Redis CLI 已安装"
else
    warn "未找到 redis-cli,请安装 Redis 7+"
    warn "  macOS: brew install redis"
    warn "  Linux: sudo apt install redis-server"
fi

# git
if command -v git &>/dev/null; then
    success "git 已安装"
else
    die "未找到 git,请先安装 git"
fi

# ── 第2步:创建虚拟环境 ──
step "创建 Python 虚拟环境"

if [ -d .venv ]; then
    info "虚拟环境已存在,跳过创建"
else
    $PY -m venv .venv
    success "虚拟环境已创建: .venv/"
fi

# 激活虚拟环境
# shellcheck disable=SC1091
source .venv/bin/activate
success "虚拟环境已激活"

# ── 第3步:安装依赖 ──
step "安装项目依赖"

pip install --upgrade pip -q
pip install -e ".[dev]" -q
success "依赖安装完成"

# ── 第4步:生成配置文件 ──
step "生成配置文件"

if [ -f rragent.yaml ]; then
    info "rragent.yaml 已存在,保留现有配置"
else
    if [ -f config.example.yaml ]; then
        cp config.example.yaml rragent.yaml
        success "已生成 rragent.yaml"
    else
        warn "未找到 config.example.yaml,跳过"
    fi
fi

if [ -f .env ]; then
    info ".env 已存在,保留现有配置"
    EXISTING_ENV=true
else
    if [ -f .env.example ]; then
        cp .env.example .env
        success "已生成 .env"
    else
        warn "未找到 .env.example,创建空 .env"
        touch .env
    fi
    EXISTING_ENV=false
fi

# ── 第5步:交互式配置 ──
step "配置必要参数"

# 辅助函数:读取 .env 中的值
get_env_val() {
    local key="$1"
    if [ -f .env ]; then
        grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d'=' -f2- || true
    fi
}

# 辅助函数:设置 .env 中的值
set_env_val() {
    local key="$1"
    local val="$2"
    if grep -qE "^${key}=" .env 2>/dev/null; then
        # 跨平台 sed:macOS 和 Linux 兼容
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^${key}=.*|${key}=${val}|" .env
        else
            sed -i "s|^${key}=.*|${key}=${val}|" .env
        fi
    else
        echo "${key}=${val}" >> .env
    fi
}

echo ""
info "以下配置将写入 .env 文件(直接回车使用默认值或保留现有值)"
echo ""

# --- LLM API Key ---
echo -e "${BOLD}LLM 提供商(至少配置一个):${NC}"

CURRENT_ANTHROPIC=$(get_env_val "ANTHROPIC_API_KEY")
if [ -n "$CURRENT_ANTHROPIC" ]; then
    MASKED="${CURRENT_ANTHROPIC:0:10}...${CURRENT_ANTHROPIC: -4}"
    echo -e "  当前 ANTHROPIC_API_KEY: ${CYAN}${MASKED}${NC}"
fi
read -rp "  ANTHROPIC_API_KEY [回车保留现有值]: " INPUT_ANTHROPIC
if [ -n "$INPUT_ANTHROPIC" ]; then
    set_env_val "ANTHROPIC_API_KEY" "$INPUT_ANTHROPIC"
    success "ANTHROPIC_API_KEY 已更新"
fi

CURRENT_DASHSCOPE=$(get_env_val "DASHSCOPE_API_KEY")
if [ -n "$CURRENT_DASHSCOPE" ]; then
    MASKED="${CURRENT_DASHSCOPE:0:6}...${CURRENT_DASHSCOPE: -4}"
    echo -e "  当前 DASHSCOPE_API_KEY: ${CYAN}${MASKED}${NC}"
fi
read -rp "  DASHSCOPE_API_KEY (可选) [回车跳过]: " INPUT_DASHSCOPE
if [ -n "$INPUT_DASHSCOPE" ]; then
    set_env_val "DASHSCOPE_API_KEY" "$INPUT_DASHSCOPE"
    success "DASHSCOPE_API_KEY 已更新"
fi

# 验证至少有一个 LLM key
FINAL_ANTHROPIC=$(get_env_val "ANTHROPIC_API_KEY")
FINAL_DASHSCOPE=$(get_env_val "DASHSCOPE_API_KEY")
if [ -z "$FINAL_ANTHROPIC" ] && [ -z "$FINAL_DASHSCOPE" ]; then
    warn "未配置任何 LLM API Key,RRAgent 将无法调用大模型"
    warn "运行后请在 .env 中填入 ANTHROPIC_API_KEY 或 DASHSCOPE_API_KEY"
fi

echo ""

# --- ReachRich ---
echo -e "${BOLD}ReachRich 行情 API:${NC}"

CURRENT_RR_URL=$(get_env_val "REACHRICH_URL")
DEFAULT_RR_URL="https://rr.zayl.net/api"
read -rp "  REACHRICH_URL [${CURRENT_RR_URL:-$DEFAULT_RR_URL}]: " INPUT_RR_URL
RR_URL="${INPUT_RR_URL:-${CURRENT_RR_URL:-$DEFAULT_RR_URL}}"
set_env_val "REACHRICH_URL" "$RR_URL"
success "REACHRICH_URL = $RR_URL"

CURRENT_RR_TOKEN=$(get_env_val "REACHRICH_TOKEN")
if [ -n "$CURRENT_RR_TOKEN" ]; then
    MASKED="${CURRENT_RR_TOKEN:0:6}...${CURRENT_RR_TOKEN: -4}"
    echo -e "  当前 REACHRICH_TOKEN: ${CYAN}${MASKED}${NC}"
fi
echo -e "  ${YELLOW}获取方式: 登录 https://rr.zayl.net → 设置 → API Key → 生成密钥(格式: rk_...)${NC}"
read -rp "  REACHRICH_TOKEN [回车保留现有值]: " INPUT_RR_TOKEN
if [ -n "$INPUT_RR_TOKEN" ]; then
    set_env_val "REACHRICH_TOKEN" "$INPUT_RR_TOKEN"
    success "REACHRICH_TOKEN 已更新"
fi

echo ""

# --- Redis ---
echo -e "${BOLD}Redis:${NC}"

CURRENT_REDIS=$(get_env_val "REDIS_URL")
DEFAULT_REDIS="redis://127.0.0.1:6379/0"
read -rp "  REDIS_URL [${CURRENT_REDIS:-$DEFAULT_REDIS}]: " INPUT_REDIS
REDIS_URL="${INPUT_REDIS:-${CURRENT_REDIS:-$DEFAULT_REDIS}}"
set_env_val "REDIS_URL" "$REDIS_URL"
success "REDIS_URL = $REDIS_URL"

# ── 第6步:检查 Redis 连接 ──
step "检查 Redis 连接"

if command -v redis-cli &>/dev/null; then
    # 从 REDIS_URL 提取主机和端口
    REDIS_HOST=$(echo "$REDIS_URL" | sed -E 's|redis://([^:@]+:)?([^:@]+)@?||; s|redis://||; s|:.*||; s|/.*||')
    REDIS_PORT=$(echo "$REDIS_URL" | sed -E 's|.*:([0-9]+)/.*|\1|')
    REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
    REDIS_PORT="${REDIS_PORT:-6379}"

    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" PING 2>/dev/null | grep -q PONG; then
        success "Redis 连接正常 ($REDIS_HOST:$REDIS_PORT)"
    else
        warn "Redis 连接失败 ($REDIS_HOST:$REDIS_PORT)"
        warn "请确认 Redis 已启动:"
        warn "  macOS: brew services start redis"
        warn "  Linux: sudo systemctl start redis"
        warn "  手动:  redis-server &"
    fi
else
    warn "未安装 redis-cli,跳过连接测试"
fi

# ── 第7步:测试 ReachRich API ──
step "测试 ReachRich API 连通性"

FINAL_RR_TOKEN=$(get_env_val "REACHRICH_TOKEN")
FINAL_RR_URL=$(get_env_val "REACHRICH_URL")

if [ -n "$FINAL_RR_TOKEN" ] && [ -n "$FINAL_RR_URL" ]; then
    if command -v curl &>/dev/null; then
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
            -H "Authorization: Bearer $FINAL_RR_TOKEN" \
            "${FINAL_RR_URL}/bridge/snapshot/" \
            --connect-timeout 10 --max-time 15 2>/dev/null || echo "000")

        case "$HTTP_CODE" in
            200|201)
                success "ReachRich API 连接成功 (HTTP $HTTP_CODE)"
                ;;
            401|403)
                warn "ReachRich API 认证失败 (HTTP $HTTP_CODE) — 请检查 REACHRICH_TOKEN"
                ;;
            000)
                warn "ReachRich API 连接超时 — 请检查 REACHRICH_URL 和网络"
                ;;
            *)
                warn "ReachRich API 返回 HTTP $HTTP_CODE"
                ;;
        esac
    else
        warn "未安装 curl,跳过 API 测试"
    fi
else
    warn "REACHRICH_TOKEN 或 REACHRICH_URL 未配置,跳过 API 测试"
fi

# ── 完成 ──
echo ""
echo -e "${GREEN}${BOLD}============================================${NC}"
echo -e "${GREEN}${BOLD}  RRAgent 部署完成！${NC}"
echo -e "${GREEN}${BOLD}============================================${NC}"
echo ""
echo -e "  ${BOLD}启动步骤:${NC}"
echo ""
echo -e "  1. 确保 Redis 正在运行:"
echo -e "     ${CYAN}redis-server &${NC}"
echo ""
echo -e "  2. 激活虚拟环境:"
echo -e "     ${CYAN}source .venv/bin/activate${NC}"
echo ""
echo -e "  3. 启动 RRAgent:"
echo -e "     ${CYAN}python -m rragent --config rragent.yaml${NC}"
echo ""
echo -e "  ${BOLD}其他启动方式:${NC}"
echo -e "     ${CYAN}rragent-mcp --backend pyagent${NC}     # MCP 服务(PyAgent 工具)"
echo -e "     ${CYAN}rragent-market${NC}                     # MCP 服务(行情数据)"
echo ""
echo -e "  ${BOLD}配置文件:${NC}"
echo -e "     .env          — 环境变量(API Key 等敏感信息)"
echo -e "     rragent.yaml   — 系统配置(模型、超时等)"
echo ""
echo -e "  ${BOLD}可选组件(接入 IM 通道时需要):${NC}"
echo -e "     IM Gateway — Telegram/飞书/WebChat 接入"
echo -e "       安装: ${CYAN}pip install rragent${NC}  或  ${CYAN}docker pull ghcr.io/rragent/rragent${NC}"
echo -e "     Hermes Agent — 扩展工具集"
echo -e "       安装: ${CYAN}pip install hermes-agent${NC}"
echo -e "     如果只用 API 调数据,不需要安装以上组件。"
echo ""
