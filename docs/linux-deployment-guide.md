# OpenClaw 运维智能体 — Linux 部署指南

> 面向基础设施运维场景：网络、系统、VMware/KVM/PVE、信创环境

---

## 1. 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        Linux Server                              │
│                                                                  │
│  ┌──────────┐                                                    │
│  │  Redis    │  ← 消息总线 + 状态存储                              │
│  │  (6379)   │                                                    │
│  └─────┬─────┘                                                    │
│        │  Pub/Sub                                                 │
│        │                                                          │
│  ┌─────┴──────────────────────────────────────────────────┐      │
│  │                    Orchestrator                          │      │
│  │                                                        │      │
│  │  接收用户输入 → LLM 意图识别 → 路由到对应 Agent         │      │
│  │  管理任务队列 → 聚合结果 → 推送飞书/n8n 通知            │      │
│  └─────┬──────────────────────────────────────────────────┘      │
│        │                                                          │
│  ┌─────┴──────────────────────────────────────────────────┐      │
│  │                     Agent 集群                           │      │
│  │                                                        │      │
│  │  每个 Agent 是一个独立进程，通过 Redis 接收指令并返回结果  │      │
│  │  可按需启停，互不依赖，按运维场景自由组合                  │      │
│  │                                                        │      │
│  │  general_agent    ← 通用问答 / 知识库检索 / 文档生成     │      │
│  │  ops_agent        ← 系统巡检 / 资源监控 / 故障诊断       │      │
│  │  network_agent    ← 网络拓扑 / 连通性检测 / 配置审计     │      │
│  │  vm_agent         ← VMware/KVM/PVE 虚拟化管理           │      │
│  │  dev_agent        ← 脚本开发 / SSH 批量执行 / 配置变更   │      │
│  │  browser_agent    ← Web 管理页面自动化操作               │      │
│  │  ...              ← 可自行扩展更多 Agent                  │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐      │
│  │                  前端 & 渠道层                            │      │
│  │                                                        │      │
│  │  webchat_api (:7789)  ← Web Dashboard (对话/管理/监控)  │      │
│  │  feishu_bot           ← 飞书群消息推送 & 接收           │      │
│  │  n8n (:5678)          ← 工作流自动化 / 告警编排          │      │
│  └────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
```

### 1.1 组件说明

| 组件 | 职责 | 进程数 |
|------|------|--------|
| **Redis** | 消息总线 (Pub/Sub) + 状态持久化 (心跳/历史/配置) | 1 |
| **Orchestrator** | 接收所有用户请求，LLM 意图识别，路由到对应 Agent | 1 |
| **Agent × N** | 各领域独立执行器，按需启停 | 每种 1 个 |
| **webchat_api** | Web 管理面板 + REST API (供 n8n/外部系统调用) | 1 |
| **feishu_bot** | 飞书消息通道 (Webhook 推送 + 可选应用机器人接收) | 1 |
| **n8n** | 工作流编排 (定时巡检/告警升级/外部系统对接) | 1 |

### 1.2 通信机制

所有组件通过 **Redis Pub/Sub** 解耦，不存在直接进程间调用。

```
请求链路:
  运维人员输入 → webchat / 飞书 / n8n
    → Redis "openclaw:orchestrator"
    → Orchestrator LLM 意图识别
    → Redis "openclaw:{target_agent}"
    → Agent 执行 → Redis "openclaw:reply:{msg_id}"
    → 结果回传

通知链路:
  Agent 完成 → Orchestrator → NotifyRouter
    → "openclaw:notify:feishu"  → feishu_bot → 飞书群
    → "openclaw:notify:all"     → n8n 订阅 → 告警升级/邮件/工单
```

### 1.3 运维场景下各组件的角色

| 组件 | 运维场景举例 |
|------|------------|
| **Orchestrator** | "检查所有 ESXi 主机的 CPU 使用率" → 自动识别为 vm_agent 任务 |
| **general_agent** | 运维知识问答："KVM 热迁移的前提条件是什么" |
| **ops_agent** | SSH 批量巡检、磁盘/CPU/内存告警分析、日志关键字提取 |
| **network_agent** | ping/traceroute/端口扫描、交换机配置备份、DNS 解析检查 |
| **vm_agent** | vCenter API / libvirt / PVE API 调用，虚拟机生命周期管理 |
| **dev_agent** | 编写 Ansible playbook、Shell 脚本、Python 自动化工具 |
| **browser_agent** | 自动登录 vCenter Web、PVE 管理页、信创云平台截图取证 |
| **feishu_bot** | 故障告警推送、值班通知、审批消息 |
| **n8n** | Zabbix/Prometheus 告警接入 → AI 分析 → 飞书升级 → 自动工单 |

### 1.4 n8n 的角色

n8n 不是核心组件，是**外挂的自动化编排层**，没有它系统照常运行。

| 能力 | 运维场景 |
|------|---------|
| **定时触发** | 每天 8:00 巡检所有主机 → 结果推送飞书 |
| **事件驱动** | 接收 Zabbix Webhook → 调 AI 分析根因 → 飞书告警 |
| **告警升级** | 5 分钟未确认 → 电话通知 → 30 分钟未处理 → 升级主管 |
| **外部对接** | 对接 CMDB / 工单系统 / Prometheus / Grafana |
| **Webhook 入口** | 让其他运维平台触发 OpenClaw 的 AI 能力 |

---

## 2. 环境准备

### 2.1 系统要求

```bash
# Ubuntu 22.04+ / Debian 12+ / CentOS Stream 9+ / 信创 UOS/Kylin
# Python 3.11+, Redis 7+

sudo apt update && sudo apt install -y \
    python3.11 python3.11-venv python3-pip \
    redis-server git curl nginx
```

### 2.2 创建工作用户

```bash
sudo useradd -m -s /bin/bash clawagent
sudo -u clawagent -i

mkdir -p ~/openclaw ~/logs
cd ~/openclaw
git clone <your-repo-url> .
```

### 2.3 Python 虚拟环境

```bash
python3.11 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install \
    redis[hiredis] python-dotenv pyyaml httpx aiohttp \
    fastapi "uvicorn[standard]" \
    "langchain-openai>=1.1.9" langchain-anthropic langchain-google-genai \
    "langchain-core>=1.2.14" \
    "browser-use>=0.11.11" playwright \
    chromadb networkx

# 运维场景可选依赖
pip install \
    paramiko          # SSH 连接管理
    ansible-runner    # Ansible 集成
    pyVmomi           # VMware vCenter API
    libvirt-python    # KVM/QEMU 管理
    proxmoxer         # Proxmox VE API
    netmiko           # 网络设备 SSH (交换机/防火墙)
    nmap              # 端口扫描

playwright install chromium
```

### 2.4 n8n

```bash
# Docker (推荐)
docker run -d --name n8n \
    -p 5678:5678 \
    -v ~/.n8n:/home/node/.n8n \
    --restart unless-stopped \
    n8nio/n8n
```

---

## 3. 配置文件

### 3.1 `openclaw.env` — 主配置

```bash
cat > ~/openclaw/openclaw.env << 'EOF'
# ═══ LLM ═══
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=sk-xxxx

# 可选多 Provider
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# ═══ Redis ═══
REDIS_URL=redis://127.0.0.1:6379/0

# ═══ SSH (ops_agent / dev_agent 使用) ═══
SHELL_TIMEOUT=30
SSH_TIMEOUT=15

# ═══ 虚拟化平台 (vm_agent 使用) ═══
VCENTER_HOST=
VCENTER_USER=
VCENTER_PASS=
PVE_HOST=
PVE_TOKEN_ID=
PVE_TOKEN_SECRET=
KVM_HOSTS=

# ═══ 网络设备 (network_agent 使用) ═══
NETMIKO_DEVICE_TYPE=cisco_ios
SNMP_COMMUNITY=public
EOF
chmod 600 ~/openclaw/openclaw.env
```

### 3.2 `webchat.env` — Web Dashboard

```bash
cat > ~/openclaw/webchat.env << 'EOF'
WEBCHAT_PORT=7789
WEBCHAT_HOST=0.0.0.0
WEBCHAT_AUTH_USER=admin
WEBCHAT_AUTH_PASS=your_password
REDIS_URL=redis://127.0.0.1:6379/0
REPLY_TIMEOUT=120
EOF
chmod 600 ~/openclaw/webchat.env
```

### 3.3 `feishu.env` — 飞书通道

```bash
cat > ~/openclaw/feishu.env << 'EOF'
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_HOOK_ID

# [可选] 应用机器人
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=

REDIS_URL=redis://127.0.0.1:6379/0
EOF
chmod 600 ~/openclaw/feishu.env
```

---

## 4. systemd 服务

### 4.1 Agent 通用模板

```bash
sudo tee /etc/systemd/system/openclaw@.service << 'EOF'
[Unit]
Description=OpenClaw %i
After=redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=clawagent
Group=clawagent
WorkingDirectory=/home/clawagent/openclaw
Environment=PYTHONPATH=/home/clawagent/openclaw
Environment=REDIS_URL=redis://127.0.0.1:6379/0
ExecStart=/home/clawagent/openclaw/.venv/bin/python -m agents.%i
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/clawagent/logs/%i.log
StandardError=append:/home/clawagent/logs/%i.err

[Install]
WantedBy=multi-user.target
EOF
```

### 4.2 前端服务

```bash
# Web Dashboard
sudo tee /etc/systemd/system/openclaw-webchat.service << 'EOF'
[Unit]
Description=OpenClaw Web Dashboard
After=redis-server.service

[Service]
Type=simple
User=clawagent
WorkingDirectory=/home/clawagent/openclaw
ExecStart=/home/clawagent/openclaw/.venv/bin/python webchat_api.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/clawagent/logs/webchat.log
StandardError=append:/home/clawagent/logs/webchat.err

[Install]
WantedBy=multi-user.target
EOF

# 飞书
sudo tee /etc/systemd/system/openclaw-feishu.service << 'EOF'
[Unit]
Description=OpenClaw Feishu Bot
After=redis-server.service

[Service]
Type=simple
User=clawagent
WorkingDirectory=/home/clawagent/openclaw
ExecStart=/home/clawagent/openclaw/.venv/bin/python feishu_bot.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/clawagent/logs/feishu.log
StandardError=append:/home/clawagent/logs/feishu.err

[Install]
WantedBy=multi-user.target
EOF
```

### 4.3 启动

```bash
sudo systemctl daemon-reload

# 核心
for svc in orchestrator general_agent ops_agent network_agent vm_agent dev_agent browser_agent; do
    sudo systemctl enable --now openclaw@${svc}
done

# 前端 & 渠道
sudo systemctl enable --now openclaw-webchat openclaw-feishu
```

### 4.4 运维

```bash
systemctl list-units 'openclaw*' --no-pager        # 全部状态
journalctl -u openclaw@orchestrator -f              # 实时日志
sudo systemctl restart openclaw@ops_agent           # 重启单个
```

---

## 5. 运维 Agent 设计参考

以下为按运维场景设计的 Agent，均继承 `BaseAgent`，注册到 Orchestrator 即可使用。

### 5.1 ops_agent — 系统运维

```
职责: 主机巡检、资源监控、日志分析、故障诊断
触发: "检查一下 web01 的磁盘" / "分析 /var/log/syslog 最近的报错"

核心 action:
  host_check     → SSH 连接目标主机，采集 CPU/内存/磁盘/进程
  log_analyze    → 抓取指定日志，LLM 提取关键事件和根因
  batch_exec     → 批量 SSH 执行命令，汇总结果
  health_report  → 生成多主机健康报告

依赖: paramiko / subprocess
```

### 5.2 network_agent — 网络运维

```
职责: 连通性检测、端口扫描、配置审计、拓扑发现
触发: "ping 一下 10.0.0.0/24 段" / "检查核心交换机配置"

核心 action:
  ping_sweep     → 批量 ping，返回存活/丢包/延迟
  port_scan      → nmap 扫描指定主机端口
  config_backup  → SSH 登录网络设备，备份 running-config
  route_trace    → traceroute + MTR 链路分析

依赖: nmap / netmiko / subprocess
```

### 5.3 vm_agent — 虚拟化管理

```
职责: 虚拟机生命周期管理、资源查询、快照、迁移
触发: "列出 vCenter 上所有关机的虚拟机" / "给 db-server 做个快照"

核心 action (按平台分):

  VMware (pyVmomi):
    vm_list        → 列出 VM 及资源使用
    vm_power       → 开机/关机/重启
    vm_snapshot    → 创建/恢复/删除快照
    vm_migrate     → vMotion 迁移
    host_status    → ESXi 主机状态

  KVM (libvirt):
    vm_list        → virsh list --all
    vm_power       → virsh start/shutdown/reboot
    vm_snapshot    → virsh snapshot-create-as
    vm_clone       → virt-clone

  PVE (proxmoxer):
    vm_list        → GET /api2/json/cluster/resources
    vm_power       → POST .../status/{start|stop|reboot}
    vm_snapshot    → POST .../snapshot
    vm_migrate     → POST .../migrate

依赖: pyVmomi / libvirt-python / proxmoxer
```

### 5.4 自然语言交互示例

```
运维人员: "web 集群最近有没有异常"
  → Orchestrator 意图识别 → ops_agent.batch_exec
  → SSH 到 web01~web05，采集 load/mem/disk/dmesg
  → LLM 汇总: "web03 磁盘使用 92%，web05 有 OOM Kill 记录"
  → 推送飞书

运维人员: "把测试环境的 db-test 虚拟机关掉"
  → Orchestrator → vm_agent.vm_power {name:"db-test", action:"shutdown"}
  → pyVmomi 调用 vCenter API
  → 返回: "db-test 已关机"

运维人员: "核心交换机最近有没有端口 flapping"
  → Orchestrator → network_agent.config_backup + ops_agent.log_analyze
  → SSH 登录交换机，抓日志 → LLM 分析
  → 返回: "Gi0/12 在过去 2 小时 flap 了 7 次，建议检查线缆"
```

---

## 6. n8n 运维工作流

### 6.1 对接方式

n8n 通过 REST API 调用 OpenClaw，通过 Redis 订阅异步事件。

**对话**: `POST http://localhost:7789/api/chat`

```json
{"message": "检查所有 ESXi 主机状态", "target": "manager"}
```

**命令**: `POST http://localhost:7789/api/command`

```json
{"cmd": "host_check", "args": "web01,web02,web03"}
```

**事件订阅**: n8n Redis Trigger → `openclaw:notify:all`

### 6.2 典型运维工作流

**每日巡检 → 飞书早报**:

```
Cron 每天 8:00
  → HTTP: POST /api/command {cmd:"health_report"}
  → HTTP: POST /api/command {cmd:"vm_list"}
  → Merge 合并结果
  → HTTP: POST /api/chat {message:"根据以上数据生成运维日报"}
  → 飞书 Webhook: 推送到运维群
```

**Zabbix 告警 → AI 分析 → 飞书升级**:

```
Webhook: 接收 Zabbix 告警 JSON
  → HTTP: POST /api/chat {message:"分析这个告警: {{$json.body}}"}
  → Switch: 按严重程度分流
      ├─ Warning  → 飞书: 运维群
      ├─ High     → 飞书: 运维群 + 值班群
      └─ Disaster → 飞书: 全员群 + 邮件 + 电话通知
```

**证书到期检查**:

```
Cron 每周一 9:00
  → HTTP: POST /api/command {cmd:"batch_exec", args:"检查所有域名SSL证书到期时间"}
  → IF: 有 30 天内到期的
      → 飞书: "以下证书即将到期: ..."
      → HTTP: 自动创建工单
```

**虚拟机资源巡检**:

```
Cron 每天 10:00
  → HTTP: POST /api/command {cmd:"vm_list"}
  → Code: 筛选 CPU>80% 或 内存>90% 的 VM
  → IF: 有异常
      → HTTP: POST /api/chat {message:"这些虚拟机资源紧张，给出优化建议: ..."}
      → 飞书: 推送分析结果
```

---

## 7. 飞书集成

### 7.1 推送方式

| 方式 | 说明 | 适用场景 |
|------|------|---------|
| **feishu_bot** | 独立进程，自动推送 Agent 结果 | 简单直推 |
| **n8n** | 工作流驱动，灵活编排 | 条件过滤/多群分发/告警升级 |

### 7.2 配置 Webhook

1. 飞书群 → 设置 → 群机器人 → 添加 → **自定义机器人**
2. 复制 Webhook URL → 填入 `feishu.env`
3. `sudo systemctl restart openclaw-feishu`

### 7.3 接收消息 (可选)

配置飞书应用机器人后，运维人员可在飞书群内直接提问：

```
飞书群消息: "检查 db01 的磁盘"
  → feishu_bot → Orchestrator → ops_agent
  → 执行 → 结果回复到飞书群
```

配置步骤：飞书开放平台 → 创建自建应用 → 事件订阅 `http://YOUR_SERVER:9090/feishu/event`

---

## 8. Web Dashboard

FastAPI (`webchat_api.py`) + 单文件 React 前端 (`webchat_frontend.html`)。

```
技术栈: React 18 (CDN) + Tailwind CSS / FastAPI + uvicorn / SSE 流式
认证:   HTTP Basic Auth
端口:   7789
```

### API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 主页面 |
| GET | `/api/overview` | Agent + 渠道状态 |
| POST | `/api/chat` | AI 对话 (SSE 流式) |
| POST | `/api/command` | 执行指定命令 |
| GET | `/api/history` | 共享聊天记录 |
| GET | `/api/daily-log` | 每日工作日志 (跨渠道) |
| GET | `/api/llm/config` | LLM 配置 |
| POST | `/api/llm/config` | LLM 偏好设置 |
| GET | `/api/usage` | LLM 用量统计 |

### Nginx 反代

```nginx
server {
    listen 80;
    server_name ops.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:7789;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

    location /n8n/ {
        proxy_pass http://127.0.0.1:5678/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## 9. 扩展 Agent

以 `vm_agent` 为例，新增一个 Agent 只需 3 步。

**1) 创建 `agents/vm_agent.py`**:

```python
from agents.base import BaseAgent, AgentMessage, run_agent

class VMAgent(BaseAgent):
    name = "vm_agent"

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "vm_list":
            result = await self._list_vms(params)
            await self.reply(msg, result={"text": result})
        elif action == "vm_power":
            result = await self._power_action(params)
            await self.reply(msg, result={"text": result})
        elif action == "vm_snapshot":
            result = await self._snapshot(params)
            await self.reply(msg, result={"text": result})
        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    async def _list_vms(self, params):
        platform = params.get("platform", "pve")
        if platform == "pve":
            from proxmoxer import ProxmoxAPI
            # ... PVE API 调用
        elif platform == "vmware":
            from pyVim.connect import SmartConnect
            # ... vCenter API 调用
        elif platform == "kvm":
            import libvirt
            # ... libvirt 调用

if __name__ == "__main__":
    run_agent(VMAgent())
```

**2) 注册路由** (`orchestrator.py`):

```python
COMMAND_ROUTES = {
    ...
    "vm_list":     ("vm_agent", "vm_list"),
    "vm_power":    ("vm_agent", "vm_power"),
    "vm_snapshot": ("vm_agent", "vm_snapshot"),
}
```

并在意图路由 prompt 中添加规则:

```
"涉及虚拟机/ESXi/vCenter/KVM/PVE/快照/迁移 → 路由到 vm_agent"
```

**3) 启动**:

```bash
sudo systemctl enable --now openclaw@vm_agent
```

Agent 自动注册心跳，Web/飞书/n8n 立即可用。

---

## 10. Redis 键值

| 键 | 类型 | 说明 |
|----|------|------|
| `openclaw:heartbeats` | Hash | Agent 心跳 |
| `openclaw:channel_heartbeats` | Hash | 渠道心跳 (feishu) |
| `openclaw:{agent_name}` | Pub/Sub | Agent 消息通道 |
| `openclaw:reply:{msg_id}` | Pub/Sub | 请求-响应临时通道 |
| `openclaw:notify:feishu` | Pub/Sub | 飞书推送 |
| `openclaw:notify:all` | Pub/Sub | 广播 (n8n 订阅) |
| `openclaw:chat_history` | List | 共享聊天记录 (500条) |
| `openclaw:daily_log:{date}` | List | 每日日志 (30天过期) |
| `openclaw:llm_pref` | String | LLM 偏好 |
| `openclaw:llm_usage` | List | LLM 调用记录 |
| `openclaw:tasks` | SortedSet | 任务索引 |

---

## 11. 验证清单

```bash
# Redis
redis-cli ping                                       # → PONG

# Agent 心跳
redis-cli hgetall openclaw:heartbeats                 # → 各 Agent 在线

# Web Dashboard
curl -u admin:pwd http://localhost:7789/api/overview   # → Agent 状态

# 飞书
redis-cli publish openclaw:notify:feishu \
  '{"text":"运维平台部署验证","topic":"system"}'        # → 飞书群收到

# n8n
curl http://localhost:5678/healthz                     # → {"status":"ok"}

# 端到端: 自然语言运维
curl -u admin:pwd -X POST http://localhost:7789/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"检查一下系统负载","target":"manager"}'  # → AI 回复
```
