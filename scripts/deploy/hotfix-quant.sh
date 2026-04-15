#!/bin/bash
# OpenClaw 全量部署脚本 v7 — 量化爬山优化架构 + core_engine 139 同步
# 用法: sudo bash hotfix-quant.sh
set -e

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAW_USER="clawagent"
OPENCLAW_DIR="/Users/${CLAW_USER}/openclaw"
SANDBOX_DIR="/Users/${CLAW_USER}/openclaw/quant-sandbox"
REMOTE_139="192.168.1.139"
REMOTE_USER="root"
REMOTE_KEY="/Users/zayl/.ssh/id_ed25519"
REMOTE_SANDBOX="/opt/quant_sandbox"
REMOTE_RR="/data/jupyter/ReachRich"
SCP_OPTS="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -i ${REMOTE_KEY}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'
log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

[ "$(id -u)" -ne 0 ] && err "请使用 sudo 运行: sudo bash $0"

log "=== 部署: 量化爬山优化架构 v7 ==="

ts=$(date +%Y%m%d_%H%M%S)
for f in quant_pipeline.py quant_coder_prompt.yaml llm_router.py base.py bridge_client.py orchestrator.py; do
    cp "${OPENCLAW_DIR}/agents/${f}" "${OPENCLAW_DIR}/agents/${f}.bak.${ts}" 2>/dev/null || true
done
cp "${OPENCLAW_DIR}/telegram_agent.py" "${OPENCLAW_DIR}/telegram_agent.py.bak.${ts}" 2>/dev/null || true

cp "${DEPLOY_DIR}/agents/quant_pipeline.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/bridge_client.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/quant_coder_prompt.yaml" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/llm_router.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/base.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/agents/orchestrator.py" "${OPENCLAW_DIR}/agents/"
cp "${DEPLOY_DIR}/telegram_agent.py" "${OPENCLAW_DIR}/" 2>/dev/null || true
cp "${DEPLOY_DIR}/telegram.env" "${OPENCLAW_DIR}/" 2>/dev/null || true
cp "${DEPLOY_DIR}/webchat_api.py" "${OPENCLAW_DIR}/" 2>/dev/null || true
cp "${DEPLOY_DIR}/usage_monitor.html" "${OPENCLAW_DIR}/" 2>/dev/null || true
cp "${DEPLOY_DIR}/webchat_frontend.html" "${OPENCLAW_DIR}/" 2>/dev/null || true

mkdir -p "${SANDBOX_DIR}"
cp "${DEPLOY_DIR}/quant-sandbox/openclaw_quant_lib.py" "${SANDBOX_DIR}/" 2>/dev/null || true
chown ${CLAW_USER}:staff "${SANDBOX_DIR}/openclaw_quant_lib.py" 2>/dev/null || true

chown -R ${CLAW_USER}:staff "${OPENCLAW_DIR}/agents/quant_pipeline.py" \
    "${OPENCLAW_DIR}/agents/bridge_client.py" \
    "${OPENCLAW_DIR}/agents/quant_coder_prompt.yaml" \
    "${OPENCLAW_DIR}/agents/llm_router.py" \
    "${OPENCLAW_DIR}/agents/base.py" \
    "${OPENCLAW_DIR}/agents/orchestrator.py"
for f in webchat_api.py usage_monitor.html webchat_frontend.html telegram_agent.py telegram.env; do
    [ -f "${OPENCLAW_DIR}/${f}" ] && chown ${CLAW_USER}:staff "${OPENCLAW_DIR}/${f}"
done
log "Mac Mini 文件已部署"

# ── 同步到 139 服务器 (root 用户, ssh key 认证) ──
log "同步文件到 139 (${REMOTE_USER}@${REMOTE_139})..."
SRC_DIR="${DEPLOY_DIR}/../OpenClaw-Universe"

if [ -f "${SRC_DIR}/quant-sandbox/core_engine.py" ]; then
    scp ${SCP_OPTS} "${SRC_DIR}/quant-sandbox/core_engine.py" "${REMOTE_USER}@${REMOTE_139}:${REMOTE_SANDBOX}/core_engine.py" && log "  core_engine.py → 139:${REMOTE_SANDBOX}/ ✓" || warn "  core_engine.py SCP 失败"
    scp ${SCP_OPTS} "${SRC_DIR}/quant-sandbox/openclaw_quant_lib.py" "${REMOTE_USER}@${REMOTE_139}:${REMOTE_SANDBOX}/venv/lib/python3.12/site-packages/openclaw_quant_lib.py" && log "  openclaw_quant_lib.py → 139 site-packages ✓" || warn "  openclaw_quant_lib.py SCP 失败"
else
    warn "未找到 core_engine.py，跳过沙箱同步"
fi

if [ -f "${SRC_DIR}/ReachRich/fastapi_backend/api/backtest_executor.py" ]; then
    scp ${SCP_OPTS} "${SRC_DIR}/ReachRich/fastapi_backend/api/backtest_executor.py" "${REMOTE_USER}@${REMOTE_139}:${REMOTE_RR}/fastapi_backend/api/backtest_executor.py" && log "  backtest_executor.py → 139 ✓" || warn "  backtest_executor.py SCP 失败"
    scp ${SCP_OPTS} "${SRC_DIR}/ReachRich/fastapi_backend/api/openclaw_bridge.py" "${REMOTE_USER}@${REMOTE_139}:${REMOTE_RR}/fastapi_backend/api/openclaw_bridge.py" && log "  openclaw_bridge.py → 139 ✓" || warn "  openclaw_bridge.py SCP 失败"
    # 重启 139 FastAPI 服务使新端点生效
    ssh ${SCP_OPTS} "${REMOTE_USER}@${REMOTE_139}" "systemctl restart reachrich-fastapi 2>/dev/null || true" && log "  reachrich-fastapi 已重启 ✓" || warn "  139 FastAPI 重启失败 (手动: systemctl restart reachrich-fastapi)"
else
    warn "未找到 ReachRich API 文件，跳过 Bridge 同步"
fi

# 只杀 orchestrator（精确匹配）
ORCH_PID=$(ps aux | grep "clawagent.*python.*agents.orchestrator" | grep -v grep | awk '{print $2}' | head -1)
if [ -n "${ORCH_PID}" ]; then
    log "停止旧 orchestrator (PID: ${ORCH_PID})..."
    kill "${ORCH_PID}" 2>/dev/null || true
    sleep 2
    kill -9 "${ORCH_PID}" 2>/dev/null || true
    sleep 1
fi

cd /tmp

log "启动新 orchestrator..."
sudo -u ${CLAW_USER} -H bash --norc --noprofile -c 'cd /Users/clawagent/openclaw && source .venv/bin/activate 2>/dev/null; export PYTHONPATH="/Users/clawagent/openclaw:${PYTHONPATH}"; if [ -f .env ]; then set -a; source .env; set +a; fi; mkdir -p /Users/clawagent/logs .pids; /Users/clawagent/openclaw/.venv/bin/python -m agents.orchestrator >> /Users/clawagent/logs/orchestrator.log 2>&1 & echo $! > .pids/orchestrator.pid; echo "  orchestrator started (PID: $!)"'

sleep 2

# 重启 webchat_api
WEB_PID=$(ps aux | grep "clawagent.*webchat_api" | grep -v grep | awk '{print $2}' | head -1)
if [ -n "${WEB_PID}" ]; then
    log "停止旧 webchat_api (PID: ${WEB_PID})..."
    kill "${WEB_PID}" 2>/dev/null || true
    sleep 2
    kill -9 "${WEB_PID}" 2>/dev/null || true
    sleep 1
fi

log "启动新 webchat_api..."
sudo -u ${CLAW_USER} -H bash --norc --noprofile -c 'cd /Users/clawagent/openclaw && source .venv/bin/activate 2>/dev/null; export PYTHONPATH="/Users/clawagent/openclaw:${PYTHONPATH}"; if [ -f .env ]; then set -a; source .env; set +a; fi; mkdir -p /Users/clawagent/logs .pids; /Users/clawagent/openclaw/.venv/bin/python webchat_api.py >> /Users/clawagent/logs/webchat_api.log 2>&1 & echo $! > .pids/webchat_api.pid; echo "  webchat_api started (PID: $!)"'

# 重启 telegram_agent
TG_AGENT_PID=$(ps aux | grep "clawagent.*telegram_agent" | grep -v grep | awk '{print $2}' | head -1)
if [ -n "${TG_AGENT_PID}" ]; then
    log "停止旧 telegram_agent (PID: ${TG_AGENT_PID})..."
    kill "${TG_AGENT_PID}" 2>/dev/null || true
    sleep 2
    kill -9 "${TG_AGENT_PID}" 2>/dev/null || true
    sleep 1
fi

log "启动新 telegram_agent..."
sudo -u ${CLAW_USER} -H bash --norc --noprofile -c 'cd /Users/clawagent/openclaw && source .venv/bin/activate 2>/dev/null; export PYTHONPATH="/Users/clawagent/openclaw:${PYTHONPATH}"; if [ -f telegram.env ]; then set -a; source telegram.env; set +a; fi; if [ -f .env ]; then set -a; source .env; set +a; fi; mkdir -p /Users/clawagent/logs .pids; /Users/clawagent/openclaw/.venv/bin/python telegram_agent.py >> /Users/clawagent/logs/telegram_agent.log 2>&1 & echo $! > .pids/telegram_agent.pid; echo "  telegram_agent started (PID: $!)"'

sleep 3
NEW_PID=$(ps aux | grep "clawagent.*python.*agents.orchestrator" | grep -v grep | awk '{print $2}' | head -1)
NEW_WEB=$(ps aux | grep "clawagent.*webchat_api" | grep -v grep | awk '{print $2}' | head -1)
NEW_TG=$(ps aux | grep "clawagent.*telegram_agent" | grep -v grep | awk '{print $2}' | head -1)

log "=== 进程状态 ==="
[ -n "${NEW_PID}" ] && log "  orchestrator:    PID ${NEW_PID}" || log "  orchestrator:    ❌ 未启动"
[ -n "${NEW_WEB}" ] && log "  webchat_api:     PID ${NEW_WEB}" || log "  webchat_api:     ❌ 未启动"
[ -n "${NEW_TG}" ]  && log "  telegram_agent:  PID ${NEW_TG}"  || log "  telegram_agent:  ❌ 未启动"

if [ -n "${NEW_PID}" ] && [ -n "${NEW_WEB}" ]; then
    log "✅ 核心服务部署成功！"
else
    err "部分服务启动失败。检查: /Users/clawagent/logs/"
fi
