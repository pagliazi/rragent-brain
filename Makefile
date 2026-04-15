# ═══════════════════════════════════════════════════════
# OpenClaw Brain — 构建 / 同步 / 部署
# 用法:
#   make sync      — 同步到 openclaw-deploy (暂存)
#   make deploy    — sync + 部署到 clawagent/openclaw + 重启
#   make verify    — 检查运行实例健康状态
#   make full      — deploy + verify
# ═══════════════════════════════════════════════════════

DEPLOY_DIR  := /Users/zayl/openclaw-deploy
CLAW_HOST   := clawagent@localhost
CLAW_DIR    := /Users/clawagent/openclaw

RSYNC_BASE  := rsync -av --delete
EXCLUDES    := --exclude '.git' \
               --exclude '.venv' \
               --exclude '__pycache__' \
               --exclude 'memory/chroma' \
               --exclude 'memory/embed_cache' \
               --exclude 'memory/graph' \
               --exclude 'memory/kg_backup' \
               --exclude 'logs/' \
               --exclude '.pids/' \
               --exclude '*.env' \
               --exclude '.env' \
               --exclude '.DS_Store' \
               --exclude 'DO_NOT_EDIT.md'

DEPLOY_EXTRA := --exclude 'quant-sandbox/results' \
                --exclude 'quant-sandbox/cache' \
                --exclude 'quant-sandbox/data' \
                --exclude 'launchagents/'

.PHONY: sync deploy restart verify full diff status

sync:
	@echo "▸ 同步 brain → openclaw-deploy ..."
	$(RSYNC_BASE) $(EXCLUDES) ./ $(DEPLOY_DIR)/
	@echo "✓ openclaw-deploy 已同步"

deploy: sync
	@echo "▸ 部署 openclaw-deploy → clawagent/openclaw ..."
	rsync -rlpv --delete --omit-dir-times $(EXCLUDES) $(DEPLOY_EXTRA) \
		-e ssh $(DEPLOY_DIR)/ $(CLAW_HOST):$(CLAW_DIR)/
	@echo "✓ 代码已部署到 $(CLAW_DIR)"
	@$(MAKE) restart

restart:
	@echo "▸ 重启核心服务 (orchestrator + webchat) ..."
	ssh $(CLAW_HOST) 'launchctl kickstart -k gui/$$(id -u)/com.openclaw.orchestrator 2>/dev/null || true'
	@sleep 2
	ssh $(CLAW_HOST) 'launchctl kickstart -k gui/$$(id -u)/com.openclaw.webchat 2>/dev/null || true'
	@sleep 2
	@echo "✓ 核心服务已重启"

restart-all:
	@echo "▸ 重启全部 OpenClaw 服务 ..."
	ssh $(CLAW_HOST) '\
		for svc in orchestrator webchat general-agent analysis-agent market-agent news-agent \
		           strategist-agent desktop-agent browser-agent apple-agent dev-agent; do \
			launchctl kickstart -k gui/$$(id -u)/com.openclaw.$$svc 2>/dev/null || true; \
			sleep 1; \
		done'
	@echo "✓ 全部服务已重启"

verify:
	@echo "═══ OpenClaw 健康检查 ═══"
	@echo "--- API Health ---"
	@curl -sf http://localhost:7789/api/health && echo "" || echo "FAIL: webchat_api 未响应"
	@echo "--- Auth Endpoint ---"
	@curl -sf http://localhost:7789/api/auth/me -w "\n" 2>/dev/null | head -c 120 || echo "OK (需要登录)"
	@echo "--- Plan API ---"
	@curl -sf http://localhost:7789/api/plan/history -w "\n" 2>/dev/null | head -c 120 || echo "OK (需要认证)"
	@echo "--- Orchestrator PID ---"
	@ssh $(CLAW_HOST) 'cat $(CLAW_DIR)/.pids/orchestrator.pid 2>/dev/null && echo " (running)" || echo "NOT RUNNING"'
	@echo "═══════════════════════"

full: deploy verify

diff:
	@echo "▸ brain vs deploy 差异:"
	@diff -rq --exclude='.git' --exclude='__pycache__' --exclude='.venv' \
		--exclude='*.env' --exclude='.env' --exclude='.pids' \
		--exclude='logs' --exclude='memory/chroma' --exclude='memory/embed_cache' \
		--exclude='memory/graph' --exclude='DO_NOT_EDIT.md' \
		--exclude='.DS_Store' --exclude='launchagents' \
		. $(DEPLOY_DIR)/ 2>/dev/null || true
	@echo "▸ deploy vs clawagent 差异:"
	@ssh $(CLAW_HOST) 'diff -rq --exclude="__pycache__" --exclude=".venv" \
		--exclude="*.env" --exclude=".env" --exclude=".pids" \
		--exclude="logs" --exclude="memory/chroma" --exclude="memory/embed_cache" \
		--exclude="memory/graph" --exclude="memory/kg_backup" \
		--exclude="DO_NOT_EDIT.md" --exclude=".DS_Store" \
		--exclude="quant-sandbox/results" --exclude="quant-sandbox/cache" \
		--exclude="quant-sandbox/data" --exclude="browser_use" \
		--exclude="examples" --exclude=".github" --exclude="bin" \
		--exclude="docker" --exclude="tests/ci" --exclude="tests/agent_tasks" \
		--exclude="scripts" --exclude=".git" --exclude=".chainlit" \
		$(CLAW_DIR)/ $(CLAW_DIR)/' 2>/dev/null || echo "(需要 SSH 访问)"

status:
	@echo "▸ Git 状态:"
	@git status --short
	@echo "▸ 最近提交:"
	@git log --oneline -3
