#!/bin/bash
# ReachRich API 盘中验证脚本
# 由 cron 在交易日 9:35 自动运行

KEY="rk_AWMNoOmm_MaPvqHzBuZ-yw6hg5bLn6deIYJCkGuHGWObmCR6"
BASE="http://192.168.1.138/api"
LOG="/Users/zayl/logs/reachrich_verify.log"
PASS=0
FAIL=0
TS=$(date '+%Y-%m-%d %H:%M:%S')

log() { echo "$TS [$1] $2" >> "$LOG"; }

check() {
  local name="$1" url="$2" jq_expr="$3"
  local body code
  body=$(curl -s -w '\n%{http_code}' -H "Authorization: Bearer $KEY" "$url" 2>/dev/null)
  code=$(echo "$body" | tail -1)
  body=$(echo "$body" | sed '$d')

  if [ "$code" != "200" ]; then
    log "FAIL" "$name HTTP=$code"
    FAIL=$((FAIL+1))
    return
  fi

  local val
  val=$(echo "$body" | python3 -c "$jq_expr" 2>/dev/null)
  if [ $? -ne 0 ] || [ -z "$val" ] || [ "$val" = "0" ] || [ "$val" = "None" ]; then
    log "FAIL" "$name HTTP=200 但数据为空"
    FAIL=$((FAIL+1))
  else
    log "OK" "$name $val"
    PASS=$((PASS+1))
  fi
}

log "INFO" "========== 盘中验证开始 =========="

check "全市场快照" \
  "$BASE/fast/realtime/?page=1&page_size=3&order_by=-pct_chg" \
  "import sys,json; d=json.load(sys.stdin); print(f'count={d[\"count\"]} top={d[\"results\"][0][\"name\"]}')"

check "市场概览" \
  "$BASE/fast/market-overview/" \
  "import sys,json; d=json.load(sys.stdin); print(f'up={d.get(\"up_count\",\"?\")} down={d.get(\"down_count\",\"?\")}')"

check "热门股票" \
  "$BASE/fast/hot/" \
  "import sys,json; d=json.load(sys.stdin); r=d.get('results',d.get('data',[])); print(f'count={len(r)}')"

check "个股实时" \
  "$BASE/realtime/000001.SZ" \
  "import sys,json; d=json.load(sys.stdin); print(f'price={d[\"price\"]} pct={d.get(\"pct_chg\",\"?\")}')"

check "实时状态" \
  "$BASE/realtime/status/" \
  "import sys,json; d=json.load(sys.stdin); print(f'is_open={d[\"is_open\"]}')"

# SSE 推送（需要 timeout）
sse_body=$(timeout 3 curl -s -N -H "Authorization: Bearer $KEY" "$BASE/sse/realtime/" 2>/dev/null | head -3)
if [ -n "$sse_body" ]; then
  log "OK" "SSE推送 收到数据"
  PASS=$((PASS+1))
else
  log "FAIL" "SSE推送 3秒内无数据（盘后正常）"
  FAIL=$((FAIL+1))
fi

check "Bridge快照" \
  "$BASE/bridge/snapshot/" \
  "import sys,json; d=json.load(sys.stdin); print(f'date={d[\"trade_date\"]} stocks={d[\"total_stocks\"]}')"

check "Bridge涨停" \
  "$BASE/bridge/limitup/" \
  "import sys,json; d=json.load(sys.stdin); print(f'up={d.get(\"limit_up_count\",\"?\")}')"

check "Bridge概念" \
  "$BASE/bridge/concepts/" \
  "import sys,json; d=json.load(sys.stdin); print(f'count={d[\"count\"]}')"

check "Bridge龙虎" \
  "$BASE/bridge/dragon-tiger/" \
  "import sys,json; d=json.load(sys.stdin); print(f'count={d[\"count\"]}')"

log "INFO" "========== 验证完成: $PASS 通过, $FAIL 失败 =========="
echo "$TS 验证完成: $PASS 通过, $FAIL 失败"
