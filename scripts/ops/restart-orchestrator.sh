#!/bin/bash
# 仅重启 orchestrator + 被误杀的服务
# 用法: sudo bash restart-orchestrator.sh
set -e

CLAW_USER="clawagent"
OPENCLAW_DIR="/Users/${CLAW_USER}/openclaw"
LOGS="/Users/${CLAW_USER}/logs"
PIDS="${OPENCLAW_DIR}/.pids"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log() { echo -e "${GREEN}[+]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

[ "$(id -u)" -ne 0 ] && err "请使用 sudo 运行: sudo bash $0"

sudo -u ${CLAW_USER} bash << 'INNER'
set -e
cd /Users/clawagent/openclaw
source .venv/bin/activate 2>/dev/null || true
export PYTHONPATH="/Users/clawagent/openclaw:${PYTHONPATH}"
if [ -f .env ]; then set -a; source .env; set +a; fi
if [ -f telegram.env ]; then set -a; source telegram.env; set +a; fi

LOGS="/Users/clawagent/logs"
PIDS="/Users/clawagent/openclaw/.pids"
mkdir -p "$LOGS" "$PIDS"

start_one() {
    local name="$1" module="$2"
    local pidfile="${PIDS}/${name}.pid"
    if [ -f "$pidfile" ] && kill -0 "$(cat $pidfile)" 2>/dev/null; then
        echo "  ⚠ ${name} already running (pid: $(cat $pidfile))"
        return
    fi
    /Users/clawagent/openclaw/.venv/bin/python -m ${module} >> "${LOGS}/${name}.log" 2>&1 &
    echo $! > "$pidfile"
    echo "  ✓ ${name} started (pid: $!)"
}

echo "=== Restarting killed services ==="
start_one orchestrator agents.orchestrator
start_one webchat_api agents.webchat_api 2>/dev/null || true

sleep 1

# 重启 telegram/feishu (独立脚本，不是 -m 模块)
for svc in telegram_bot telegram_agent feishu_bot; do
    script="/Users/clawagent/openclaw/${svc}.py"
    pidfile="${PIDS}/${svc}.pid"
    if [ -f "$script" ]; then
        if [ -f "$pidfile" ] && kill -0 "$(cat $pidfile)" 2>/dev/null; then
            echo "  ⚠ ${svc} already running"
        else
            /Users/clawagent/openclaw/.venv/bin/python "$script" >> "${LOGS}/${svc}.log" 2>&1 &
            echo $! > "$pidfile"
            echo "  ✓ ${svc} started (pid: $!)"
        fi
    fi
done

# webchat_api (独立脚本)
script="/Users/clawagent/openclaw/webchat_api.py"
pidfile="${PIDS}/webchat_api.pid"
if [ -f "$script" ]; then
    if [ -f "$pidfile" ] && kill -0 "$(cat $pidfile)" 2>/dev/null; then
        echo "  ⚠ webchat_api already running"
    else
        /Users/clawagent/openclaw/.venv/bin/python "$script" >> "${LOGS}/webchat_api.log" 2>&1 &
        echo $! > "$pidfile"
        echo "  ✓ webchat_api started (pid: $!)"
    fi
fi

echo ""
echo "=== Status check ==="
sleep 2
for name in orchestrator webchat_api telegram_bot telegram_agent feishu_bot; do
    pidfile="${PIDS}/${name}.pid"
    if [ -f "$pidfile" ] && kill -0 "$(cat $pidfile)" 2>/dev/null; then
        echo "  ✓ ${name} running (pid: $(cat $pidfile))"
    else
        echo "  ✗ ${name} NOT running"
    fi
done
INNER
