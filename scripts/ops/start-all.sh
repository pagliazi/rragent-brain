#!/bin/bash
# ═══════════════════════════════════════════════════
# OpenClaw Multi-Agent 本地启动脚本
# 用法: bash start-all.sh         — 启动所有 Agent
#       bash start-all.sh stop    — 停止所有 Agent
#       bash start-all.sh status  — 查看状态
# ═══════════════════════════════════════════════════

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${DIR}/.venv"
PYTHON="${VENV}/bin/python3"
LOGS="${DIR}/logs"
PIDS="${DIR}/.pids"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

mkdir -p "${LOGS}" "${PIDS}"

export PYTHONPATH="${DIR}:${PYTHONPATH}"

# 加载环境变量
if [ -f "${DIR}/telegram.env" ]; then
    set -a
    source "${DIR}/telegram.env"
    set +a
fi

AGENTS=(
    "orchestrator:agents.orchestrator"
    "market:agents.market_agent"
    "analysis:agents.analysis_agent"
    "news:agents.news_agent"
    "dev:agents.dev_agent"
    "general:agents.general_agent"
    "apple:agents.apple_agent"
    "monitor:agents.monitor_agent"
)

start_agent() {
    local name="$1"
    local module="$2"
    local pidfile="${PIDS}/${name}.pid"
    local logfile="${LOGS}/${name}.log"

    if [ -f "${pidfile}" ] && kill -0 "$(cat ${pidfile})" 2>/dev/null; then
        echo -e "  ${YELLOW}⚠${NC} ${name} already running (pid: $(cat ${pidfile}))"
        return
    fi

    ${PYTHON} -m ${module} >> "${logfile}" 2>&1 &
    local pid=$!
    echo "${pid}" > "${pidfile}"
    echo -e "  ${GREEN}✓${NC} ${name} started (pid: ${pid}) → ${logfile}"
}

stop_agent() {
    local name="$1"
    local pidfile="${PIDS}/${name}.pid"

    if [ -f "${pidfile}" ]; then
        local pid=$(cat "${pidfile}")
        if kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" 2>/dev/null
            echo -e "  ${RED}✗${NC} ${name} stopped (pid: ${pid})"
        else
            echo -e "  ${YELLOW}⚠${NC} ${name} not running"
        fi
        rm -f "${pidfile}"
    else
        echo -e "  ${YELLOW}⚠${NC} ${name} no pidfile"
    fi
}

status_agent() {
    local name="$1"
    local pidfile="${PIDS}/${name}.pid"

    if [ -f "${pidfile}" ] && kill -0 "$(cat ${pidfile})" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} ${name} running (pid: $(cat ${pidfile}))"
    else
        echo -e "  ${RED}✗${NC} ${name} not running"
    fi
}

case "${1:-start}" in
    start)
        echo -e "${GREEN}=== Starting OpenClaw Agents ===${NC}"
        echo ""

        # 前置检查
        if ! redis-cli ping 2>/dev/null | grep -q PONG; then
            echo -e "${RED}Redis not running! Start with: brew services start redis${NC}"
            exit 1
        fi
        echo -e "  ${GREEN}✓${NC} Redis OK"

        if curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
            echo -e "  ${GREEN}✓${NC} Ollama OK (embedding only)"
        else
            echo -e "  ${YELLOW}⚠${NC} Ollama not running — memory embedding will fallback to cloud"
        fi
        echo ""

        for entry in "${AGENTS[@]}"; do
            IFS=':' read -r name module <<< "${entry}"
            start_agent "${name}" "${module}"
        done

        echo ""
        echo -e "${GREEN}All agents started.${NC}"
        echo "Logs: ${LOGS}/"
        echo "Stop: bash $0 stop"
        ;;

    stop)
        echo -e "${RED}=== Stopping OpenClaw Agents ===${NC}"
        echo ""
        for entry in "${AGENTS[@]}"; do
            IFS=':' read -r name module <<< "${entry}"
            stop_agent "${name}"
        done
        echo ""
        echo "All agents stopped."
        ;;

    status)
        echo -e "${GREEN}=== OpenClaw Agent Status ===${NC}"
        echo ""
        for entry in "${AGENTS[@]}"; do
            IFS=':' read -r name module <<< "${entry}"
            status_agent "${name}"
        done
        echo ""
        redis-cli ping 2>/dev/null | grep -q PONG && echo -e "  ${GREEN}✓${NC} Redis" || echo -e "  ${RED}✗${NC} Redis"
        curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && echo -e "  ${GREEN}✓${NC} Ollama" || echo -e "  ${RED}✗${NC} Ollama"
        ;;

    restart)
        bash "$0" stop
        sleep 2
        bash "$0" start
        ;;

    *)
        echo "Usage: $0 {start|stop|status|restart}"
        ;;
esac
