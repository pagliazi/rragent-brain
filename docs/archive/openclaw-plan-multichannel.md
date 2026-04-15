# OpenClaw — 多渠道消息架构: Telegram 频道管理 + 飞书冗余 + 双向健康检查

> **项目**: OpenClaw A 股多智能体协作系统
> **状态: ✅ 代码全部完成, TG Bot 已验证启动**
> 最后更新: 2026-02-25

## 实施进度

### 阶段 1: 修复 TG Bot 稳定性 — ✅ Done
- 重写 `telegram_bot.py` 为 asyncio 自管理模式 (不再使用 `run_polling()`)
- 手动管理 `Application.initialize/start/updater.start_polling` 生命周期
- 信号处理完全自控 (`SIGTERM/SIGINT` → `stop_event.set()` → graceful shutdown)
- 添加心跳上报 (每 10s → `openclaw:channel_heartbeats`)
- 添加投递确认机制 (`_ack_delivery`)
- 修复 `os.getcwd()` PermissionError: 脚本顶部 `os.chdir()` 确保 CWD 正确
- **验证**: Bot 在 launchd 下稳定运行, `getUpdates` 正常轮询

### 阶段 2: NotifyRouter 统一消息路由 — ✅ Done
- 创建 `agents/notify_router.py`
- 4 级消息优先级: critical(广播+确认) / high(广播) / normal(主优先+转移) / low(仅主)
- 投递确认机制: Bot 发送成功后 publish ACK → NotifyRouter 接收
- pending 消息队列: 所有渠道离线时暂存 Redis List, 恢复后自动 flush
- 渠道状态管理: `ChannelState` 对象, 基于心跳数据判断 online/degraded

### 阶段 2b: Orchestrator 集成 — ✅ Done
- 所有 `r.publish("openclaw:notify:telegram", ...)` → `self._notify(text, topic, priority)`
- 新增 `_channel_health_loop` (30s 检测渠道离线/恢复, 自动 flush pending)
- 新增 `_ack_listener` (监听投递确认)
- 新增 `channel_status` / `channel_flush` 动作
- `/channel` 命令路由到本地处理, 显示渠道健康状态
- rules.yaml 新增 "渠道健康状态推送" 规则 (每 4 小时)
- rules.yaml 所有旧 `channel: telegram` 参数已清理, 改为 topic 分流

### 阶段 3: 飞书 Bot — ✅ Done (代码完成, 待配置)
- 创建 `feishu_bot.py` — 与 TG Bot 对称设计 (asyncio 自管理 + 心跳 + 投递确认)
- HTTP Webhook 接收飞书事件 (端口 9090, `/feishu/event`)
- 多群映射 (market/query/strategy/system → 4 个独立飞书群)
- `_peer_health_check` 检查 TG 渠道, 离线时 kickstart
- 创建 `feishu.env` 配置模板 (APP_ID/APP_SECRET/ENCRYPT_KEY/群 chat_id)
- 创建 `com.openclaw.feishu-bot.plist` LaunchAgent
- **待用户操作**: 在飞书开放平台创建应用, 获取凭据, 配置事件订阅, 填入 feishu.env

### 阶段 4: 双向健康检查 — ✅ Done
- TG Bot `_peer_health_check` → 每 30s 检查飞书心跳, 离线 >60s 自动 kickstart
- Feishu Bot `_peer_health_check` → 每 30s 检查 TG 心跳, 离线 >60s 自动 kickstart
- Orchestrator `_channel_health_loop` → 渠道离线时通知活跃渠道, 恢复时 flush pending
- NotifyRouter pending 队列: 容量 500 条, FIFO

---

## 架构图

```
Orchestrator / Rules / Agents
           │
           ▼
   ┌─────────────────┐
   │  NotifyRouter    │  agents/notify_router.py
   │  4 级优先级       │  pending 队列 + ACK 确认
   └────┬────────┬────┘
        │        │
        ▼        ▼
  ┌──────────┐ ┌──────────┐
  │ TG Bot   │ │ Feishu   │
  │ (主通道)  │ │ Bot      │
  │          │ │ (冗余)    │
  │ 心跳 10s │ │ 心跳 10s │
  │ 4 Topics │ │ 4 群映射  │
  └──────────┘ └──────────┘
        ↕          ↕
     Health Check (30s 互检)
     离线 >60s → launchctl kickstart
```

## 涉及文件

### 新建
| 文件 | 说明 |
|------|------|
| `agents/notify_router.py` | 统一消息路由 + 优先级 + ACK + pending |
| `feishu_bot.py` | 飞书 Bot 前端 (asyncio + webhook) |
| `feishu.env` | 飞书配置模板 |
| `launchagents/com.openclaw.feishu-bot.plist` | LaunchAgent |

### 修改
| 文件 | 改动 |
|------|------|
| `telegram_bot.py` | 全面重写: asyncio 自管理 + 心跳 + ACK + 渠道互检 |
| `agents/orchestrator.py` | 通知走 NotifyRouter + _channel_health_loop + _ack_listener |
| `rules.yaml` | 清理旧 channel 参数 + 新增渠道健康规则 |
| `install-agents.sh` | 新增 feishu-bot 部署 + aiohttp 依赖 |
| `agents/skills/orchestrator_skills.yaml` | v5.0 多渠道描述 |

## 已知问题

1. **飞书需要公网**: Webhook 模式需要飞书服务器能访问 Mac Mini 的 9090 端口, 内网需 frp/ngrok
2. **TG Bot getcwd**: 已通过脚本顶部 `os.chdir()` 修复, 但 install-agents.sh 中 `run_as_claw` 仍有 getcwd 警告 (不影响功能)
