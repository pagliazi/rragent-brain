# RRCLAW 完整重构设计 — 基于 5 个基础 Repo 的 Harness 架构

## Context

5 个基础参考项目：
- **pagliazi/claude-code**: Claude Code 完整源码（TypeScript, 512K+ 行，1900文件）
- **pagliazi/claw-code**: 基于 Claude Code 的 Rust 重写（136K 行, 9 crate，Worker Boot + Recovery Recipes）
- **pagliazi/autoresearch**: Karpathy 自主实验循环（modify→execute→evaluate→keep/discard）
- **openclaw/openclaw**: OpenClaw 平台（TS monorepo，Gateway WS+HTTP，110扩展，53 skills，MCP client+server）
- **NousResearch/hermes-agent**: Hermes 智能体（Python 9600行核心，PTC，Background Review，Credential Pool）

当前 RRCLAW 系统三个组件分散运行，缺乏统一 harness，无容错，无自学习闭环。

**目标**：构建一个架构稳定性和业务可用性远超现有系统的统一智能体平台，核心突破在自学习纠错能力。

---

## 零、核心架构决策 — MCP 定位、实现路线、claw-code 关系

### 问题：继续使用 MCP 是否落后？

**结论：MCP 不落后，但把 MCP 当核心架构是错的。**

从 5 个 repo 的源码中看 MCP 的实际定位：

| 项目 | MCP 的角色 | 核心工具怎么调用 | 谁控制 LLM 循环 |
|------|-----------|-----------------|----------------|
| **claude-code** | 扩展层：外部工具通过 MCP 接入，`shouldDefer=true`。核心工具 Bash/Read/Write/Edit 是原生 TS 代码 | 直接函数调用，零序列化开销 | `query.ts` async generator（自己控制） |
| **claw-code** | 扩展层：`mcp_tool_bridge.rs` 桥接外部 MCP server 工具。40个核心工具是 Rust 原生 `match` dispatch | 直接函数调用 | `conversation.rs` ConversationRuntime（自己控制）|
| **openclaw** | 双重角色：(1) MCP Client 连接外部工具 server (2) MCP Server 对外暴露会话监控。但 53个 bundled skills 是原生加载 | 内建 Pi runtime 直接调用 | Embedded Pi runtime 或 ACP 外部 runtime |
| **hermes-agent** | 可选扩展：MCP Client 在后台事件循环中运行，工具前缀 `mcp_{server}_`。47个核心工具是 Python `registry.register()` 自注册 | 直接函数调用（`registry.dispatch()`）| `run_agent.py` while 循环（自己控制）|
| **autoresearch** | 不用 MCP | 直接 shell 执行 | agent 控制 git commit/reset 循环 |

**5/5 项目的核心工具都是原生调用，MCP 只用于外部扩展。**

### 为什么 MCP 不能做核心

MCP 是一个工具**调用协议**（JSON-RPC over stdio），它有三个根本限制：

1. **无法控制 LLM 循环**：MCP 是 request-response 模式。它不能做上下文压缩、工具分区执行、迭代预算管理。这些都需要在 LLM 循环内部实现。

2. **性能开销**：每次工具调用要经过 JSON 序列化 → stdio 传输 → JSON 反序列化。对于高频工具（市场数据每秒多次查询），这比直接函数调用慢 10-100x。

3. **无法实现 harness 级功能**：
   - 5层上下文压缩 → 需要拦截每次 LLM 调用前的消息
   - 工具并发/串行分区 → 需要读取 `is_concurrency_safe` 属性
   - Background Review → 需要 fork agent 共享 memory store
   - IterationBudget refund → 需要在工具执行后修改预算
   - 以上全部无法通过 MCP 协议表达

### 问题：RRCLAW 的实现是否过时？

**现有 `hermes-openclaw-bridge` 实现的问题**：

```
当前架构（bridge 是翻译层）：
Gateway Pi runtime ──MCP──→ bridge ──Redis──→ PyAgent
                     ↑                         ↑
              Pi 控制循环              bridge 只做消息转发
              bridge 无法做上下文工程
              bridge 无法做自学习
              bridge 无法做容错

设计的 7层容错、5层压缩、4层自学习 → 全部无法实现
因为 bridge 不控制 LLM 循环
```

**这不是 MCP 的错，而是架构定位的错。** Bridge 应该是 harness（控制者），不是 translator（翻译者）。

### 问题：是否需要用 claw-code 架构重构？

**答案：用 claw-code 的架构模式，但不 fork claw-code 代码。**

**为什么用模式不用代码**：

| 维度 | 直接 fork claw-code | Python 重实现模式 |
|------|---------------------|-------------------|
| 语言 | Rust（需要跨语言调 Python agents + Hermes） | Python（与现有系统同语言） |
| 工具 | 40个通用工具（Bash/File/Web） | 71 PyAgent + 47 Hermes + 内建（更丰富） |
| 自学习 | 无（claw-code 没有 Background Review / Evolution） | 有（从 Hermes 移植） |
| PTC | 无 | 有（从 Hermes 移植）|
| A股业务 | 无 | 核心功能 |
| 代码量 | 136K 行 Rust（大部分是 parity 测试和 CLI） | 预计 5-8K 行 Python（精简核心） |
| 维护 | 需要跟踪 upstream 更新 | 独立演进 |

**从 claw-code 取什么**：
- `ConversationRuntime<C, T>` 泛型架构 → Python Protocol 实现
- Worker Boot 状态机 → 多智能体协调
- Recovery Recipes → 结构化故障恢复
- Policy Engine → 策略评估
- Session JSONL → 会话持久化

**从 claude-code 取什么**：
- `query.ts` async generator 循环 → Python async generator
- 5层上下文压缩管线 → 完整移植
- ToolSearch 惰性加载 → 完整移植
- Tool 并发/串行分区 → `is_concurrency_safe` 属性
- Circuit breaker → 通用断路器
- Model fallback chain → Provider 降级

**从 Hermes 取什么（claw-code 没有的）**：
- Background Review daemon thread → 会话内自学习
- PTC (Programmatic Tool Calling) → 多步骤折叠
- IterationBudget with refund → 迭代预算管理
- Credential Pool 4策略 → 凭证轮转
- Error Classification → 结构化错误分类
- Context Compressor → 保护头尾的压缩

**从 autoresearch 取什么**：
- keep/discard 实验循环 → 策略优化 + prompt 优化
- Git 即实验追踪 → 每个成功改进是一个 commit
- program.md 模式 → 用自然语言编排研究方向

### 最终架构决策

```
┌─ OpenClaw Gateway ──────────────────────────────┐
│  角色: 纯通道层 (Channel Only)                    │
│  - Telegram / WebChat / Feishu / API              │
│  - 不控制 LLM 循环                                │
│  - 通过 ACP 将消息转发给 RRCLAW                   │
│  - Canvas/A2UI 渲染（接收 RRCLAW 的 HTML）        │
└─────────────┬───────────────────────────────────┘
              │ ACP (WebSocket) 或 WS Channel Protocol
┌─────────────▼───────────────────────────────────┐
│  RRCLAW Harness (Python)                         │
│  角色: 大脑 — 控制所有决策                         │
│                                                   │
│  ConversationRuntime (from claw-code pattern)    │
│  ├── async generator LLM 循环 (from claude-code) │
│  ├── 5层上下文压缩 (from claude-code)             │
│  ├── 7层容错 (from claude-code + claw-code)       │
│  ├── ToolSearch 惰性加载 (from claude-code)       │
│  ├── Worker Boot + TaskPacket (from claw-code)   │
│  ├── Background Review (from hermes-agent)       │
│  ├── PTC sandbox (from hermes-agent)             │
│  ├── IterationBudget (from hermes-agent)         │
│  ├── Autoresearch 实验循环 (from autoresearch)    │
│  └── Provider Router + Credential Pool           │
│       (from claw-code + hermes-agent)            │
│                                                   │
│  工具调用方式（三层）：                              │
│  ├── NATIVE（核心，零开销）：                       │
│  │   ├── PyAgent 71 cmd → Redis Pub/Sub 直连      │
│  │   ├── Hermes 47 tools → ThreadPool 直连        │
│  │   ├── Built-in: bash, file ops, grep, glob    │
│  │   └── Canvas → Gateway WS 直连                 │
│  ├── SKILL（按需加载）：                            │
│  │   ├── Bundled skills (market, backtest, etc.)  │
│  │   └── Evolution 自动生成的 skills               │
│  └── MCP（外部扩展，仅此用途）：                     │
│      ├── ClawHub 社区工具                          │
│      ├── 第三方 MCP servers                        │
│      └── RRCLAW 对外暴露自身为 MCP server          │
│          （让 Claude Desktop / Cursor 调用）       │
└──────────────────────────────────────────────────┘
```

**MCP 在 RRCLAW 中的精确角色**：
- ❌ 不是核心工具调用方式（PyAgent/Hermes 走原生调用）
- ❌ 不是 LLM 循环的控制协议
- ✅ 是消费外部工具的标准客户端（ClawHub, 第三方）
- ✅ 是对外暴露能力的标准服务端（让 Claude Desktop / Cursor 调用 RRCLAW）
- ✅ 是 ReachRich 数据服务的标准化封装层（见下方分析）
- ✅ 与 claude-code / claw-code / hermes-agent / openclaw 中 MCP 的定位完全一致

### ReachRich 数据源：MCP 还是直连？

**当前状态**：`BridgeClient`（HTTP + HMAC）直连 `192.168.1.139:8001/api/bridge`，提供：
- 行情数据：涨停板、连板、板块、热门股、大盘总结、K线、技术指标
- 量化分析：回测、因子挖掘（含 PBO 交叉验证）、Alpha 信号、选股器
- 盘中扫描：DolphinDB 实时数据（3-6秒更新）
- 策略管理：策略保存、决策台账

**关键：ReachRich 是远程 HTTP 服务，不是本地函数。** 直连 HTTP vs MCP stdio 的性能差异可以忽略（网络延迟远大于序列化开销）。

**项目自身架构文档已建议**：`openclaw-v1.2-advanced-design.md` 第52行：
> "不要在 Agent 内部写死抓取逻辑，而是将 AKShare、飞书接口包装成标准的 MCP Server"

**分三类处理**：

| 操作类型 | 特征 | 推荐方式 | 理由 |
|---------|------|---------|------|
| **行情查询** (kline, snapshot, limitup, concepts) | 快速、请求-响应、<10s | **MCP Server** ✅ | 标准化，任何 MCP 客户端可用；性能无差 |
| **长时运算** (backtest, factor_mining, alpha) | 慢、最长620s、需轮询 | **Native + 异步轮询** | MCP 不适合长时操作；保留直接 HTTP |
| **实时流式** (intraday DolphinDB scan) | 持续更新、3-6秒 | **Native + Redis Stream** | MCP 是请求-响应，不支持流式推送 |

**ReachRich MCP Server 设计**：

```python
# tools/mcp/reachrich_server.py
# 仅包装适合 MCP 的快速查询操作

@mcp.tool()
async def market_limitup(page_size: int = 20) -> dict:
    """获取今日涨停板"""
    return await bridge_client.get_limitup(page_size=page_size)

@mcp.tool()
async def market_concepts(name: str = "") -> dict:
    """获取板块行情"""
    return await bridge_client.get_concepts(name=name)

@mcp.tool()
async def market_kline(code: str, period: str = "daily", count: int = 60) -> dict:
    """获取K线数据"""
    return await bridge_client.get_kline(code=code, period=period, count=count)

@mcp.tool()
async def market_indicators(code: str, indicators: list[str] = ["MA", "RSI"]) -> dict:
    """获取技术指标"""
    return await bridge_client.get_indicators(code=code, indicators=indicators)

@mcp.tool()
async def market_sentiment() -> dict:
    """获取市场情绪"""
    return await bridge_client.get_sentiment()

# 长时操作 NOT 暴露为 MCP，保留在 RRCLAW harness 内部原生调用
```

**好处**：
1. Claude Desktop / Cursor / 其他 agent 可以直接查询 A 股行情
2. 标准化 JSON Schema 输入输出
3. 复用现有 `BridgeClient`（HMAC 认证、重试、数据校验全保留）
4. 与 `data_sources/source_router.py` 的断路器兼容

### 项目命名

**当前命名**：
- `rrclaw` = CLI 管理脚本（2900行 shell），管理 12 agent + 2 channel + 2 service
- `hermes-openclaw-bridge` = GitHub repo（当前是消息翻译层）
- `ReachRich Claw` = 系统整体名称

**建议**：

| 组件 | 当前名 | 建议 | 理由 |
|------|--------|------|------|
| **统一 harness 包** | 无（bridge/) | `rrclaw` | 沿用已建立的品牌，但内涵从"CLI工具"升级为"统一智能体harness" |
| **GitHub repo** | hermes-openclaw-bridge | `rrclaw` 或 `rrclaw-harness` | 反映从 bridge 到 harness 的升级 |
| **CLI 管理工具** | rrclaw (shell) | 保留 `rrclaw` CLI | 运维管理功能保留 |
| **Python 包名** | bridge | `rrclaw` | `import rrclaw.runtime`, `python -m rrclaw` |
| **ReachRich MCP** | 无 | `rrclaw-market` | MCP server 暴露行情数据 |

**推荐保留 `rrclaw` 作为项目名**，理由：
1. 已在 CLI、文档、内存记录中广泛使用
2. "RR" = ReachRich（数据源），"Claw" = OpenClaw（平台）— 名称完整描述了系统
3. 从 CLI 工具升级为完整 harness 是自然演进，无需改名
4. GitHub repo 改名为 `pagliazi/rrclaw`（简洁有力）

---

## 一、架构稳定性设计（7层容错体系）

参考 claude-code 的 12 种稳定性模式 + claw-code 的 Recovery Recipes，构建 7 层容错。

### Layer 1: API 级容错 — 重试 + 退避 + 降级

参考 `claude-code/src/services/api/withRetry.ts`：

```python
# runtime/resilience/api_retry.py

class ApiRetryPolicy:
    """
    参考 claude-code withRetry：
    - 指数退避: 500ms base, 2x growth, 32s cap, 25% jitter
    - 最多 10 次重试
    - 429/529: 尊重 retry-after header
    - 529 连续 3 次: 触发 model fallback
    - 401/403: 自动刷新凭证
    - 无人值守模式: 永久重试，5分钟上限退避，30秒心跳
    """

    MAX_RETRIES = 10
    BASE_DELAY_MS = 500
    MAX_BACKOFF_MS = 32_000
    MAX_529_BEFORE_FALLBACK = 3

    async def call_with_retry(self, fn, *, model, fallback_model=None):
        consecutive_529 = 0
        for attempt in range(self.MAX_RETRIES):
            try:
                return await fn(model=model)
            except RateLimitError as e:
                if e.status == 529:
                    consecutive_529 += 1
                    if consecutive_529 >= self.MAX_529_BEFORE_FALLBACK and fallback_model:
                        model = fallback_model  # 降级到备用模型
                        consecutive_529 = 0
                delay = self._backoff(attempt, e.retry_after)
                await asyncio.sleep(delay)
            except AuthError:
                await self._refresh_credentials()  # 凭证轮转
            except ConnectionError:
                await self._reconnect()  # 重建连接池
```

**Provider Fallback Chain**（参考 claw-code 多 Provider）：
```yaml
# config: provider fallback
providers:
  primary: anthropic/claude-sonnet-4-6
  fallback_chain:
    - dashscope/qwen3.5-plus      # 阿里通义（低成本）
    - ollama/qwen2.5-coder:14b    # 本地（零成本但慢）
  fallback_trigger: 3_consecutive_529
```

### Layer 2: 工具执行容错 — 错误即结果 + 自修正

参考 `claude-code/src/services/tools/toolExecution.ts`：

```python
# tools/executor.py

class ToolExecutor:
    """
    工具执行失败不终止对话，错误作为 tool_result 返回给 LLM。
    LLM 看到错误后自行修正（最多 3 次）。

    参考 claude-code:
    - Unknown tool → error result + 可用工具列表
    - Schema not loaded → 提示先调用 ToolSearch
    - Execution error → error result + 错误详情
    - Timeout → error result + 建议减小请求范围
    """

    MAX_SELF_CORRECT_ATTEMPTS = 3

    async def execute(self, tool_use: ToolUse) -> ToolResult:
        try:
            # 1. 验证输入
            validation = tool.validate_input(tool_use.input)
            if not validation.valid:
                return ToolResult(is_error=True,
                    content=f"Invalid input: {validation.error}\nSchema: {tool.input_schema}")

            # 2. 权限检查
            perm = self.permission_engine.check(tool_use)
            if perm.denied:
                return ToolResult(is_error=True, content=f"Permission denied: {perm.reason}")

            # 3. 执行（带超时）
            result = await asyncio.wait_for(
                tool.call(tool_use.input),
                timeout=tool.spec.timeout
            )

            # 4. 结果预算（大结果持久化到磁盘）
            if len(result.content) > tool.spec.max_result_size:
                path = self._persist_to_disk(result)
                result = ToolResult(
                    content=f"Result too large ({len(result.content)} chars). "
                            f"Saved to {path}. Preview:\n{result.content[:500]}")

            return result

        except asyncio.TimeoutError:
            return ToolResult(is_error=True,
                content=f"Tool {tool_use.name} timed out after {tool.spec.timeout}s. "
                        f"Try with smaller input or use a simpler approach.")
        except Exception as e:
            return ToolResult(is_error=True, content=f"Error: {e}")
```

### Layer 3: 上下文溢出恢复 — 多阶段级联

参考 `claude-code/src/query.ts` 的 4 阶段恢复：

```python
# context/overflow_recovery.py

class OverflowRecoveryPipeline:
    """
    当 prompt 超出上下文窗口时的恢复级联：

    Stage 1: Context Collapse Drain
      - 将暂存的 collapse 操作全部执行（最便宜）
      - 如果仍然溢出 → Stage 2

    Stage 2: Reactive Compact
      - 完整对话压缩（fork agent 做总结）
      - 一次性尝试，防止无限循环
      - has_attempted_reactive_compact 守卫

    Stage 3: Media Strip
      - 移除大型图片/PDF 内容
      - 替换为文本描述

    Stage 4: Surface Error
      - 所有恢复失败 → 告知用户，建议新会话
    """

    async def recover(self, session, error) -> RecoveryResult:
        # Stage 1
        if self.context_collapse:
            drained = await self.context_collapse.drain_all()
            if drained:
                return RecoveryResult(recovered=True, stage=1)

        # Stage 2 (one-shot guard)
        if not self._has_attempted_reactive_compact:
            self._has_attempted_reactive_compact = True
            compacted = await self.compact_engine.reactive_compact(session)
            if compacted:
                return RecoveryResult(recovered=True, stage=2)

        # Stage 3
        stripped = self._strip_media(session)
        if stripped:
            return RecoveryResult(recovered=True, stage=3)

        # Stage 4
        return RecoveryResult(recovered=False, stage=4,
            message="上下文窗口已满，建议使用 /compact 或开始新会话")
```

### Layer 4: Circuit Breaker — 防止失败风暴

参考 `claude-code autoCompact.ts` 的 circuit breaker：

```python
# runtime/resilience/circuit_breaker.py

class CircuitBreaker:
    """
    参考 claude-code: 3次连续失败后停止尝试，防止资源浪费。
    BQ数据：修复前每天浪费 250K API 调用。

    应用于：
    - autocompact (3次)
    - tool execution per-tool (5次同一工具连续失败 → 标记degraded)
    - evolution engine skill creation (3次失败 → 暂停1小时)
    - Redis connection (5次 → 降级到本地模式)
    """

    def __init__(self, name: str, max_failures: int = 3, cooldown: float = 0):
        self.name = name
        self.max_failures = max_failures
        self.cooldown = cooldown  # 0 = 永久跳过（本session）
        self.consecutive_failures = 0
        self.tripped_at: float | None = None

    def is_open(self) -> bool:
        if self.consecutive_failures < self.max_failures:
            return False
        if self.cooldown > 0 and self.tripped_at:
            return time.time() - self.tripped_at < self.cooldown
        return True  # 永久跳过

    def record_success(self):
        self.consecutive_failures = 0
        self.tripped_at = None

    def record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_failures:
            self.tripped_at = time.time()
            logger.warning(f"Circuit breaker [{self.name}] tripped after "
                          f"{self.consecutive_failures} consecutive failures")
```

### Layer 5: Death Spiral Prevention

参考 `claude-code query.ts` — API 错误后跳过 stop hooks：

```python
# runtime/conversation.py 中的关键守卫

async def _tool_loop(self):
    while ...:
        try:
            response = await self.api_client.stream(context)
        except ApiError as e:
            # CRITICAL: API 错误后不运行 hooks，防止
            # error → hook blocking → retry → error 循环
            self._skip_hooks_for_api_error = True
            yield ErrorEvent(e)
            break

        # ... tool execution ...

        # Stop hooks（但 API 错误后跳过）
        if not self._skip_hooks_for_api_error:
            hook_result = await self.hooks.run_stop_hooks(response)
            if hook_result.errors:
                # Hook 错误也被容纳，不中断对话
                yield WarningEvent(hook_result.errors)
```

### Layer 6: Recovery Recipes — 结构化故障恢复

参考 `claw-code/rust/crates/runtime/src/recovery_recipes.rs`：

```python
# runtime/resilience/recovery_recipes.py

class FailureScenario(Enum):
    """7种故障场景，各有恢复方案"""
    REDIS_CONNECTION_LOST = "redis_lost"
    GATEWAY_DISCONNECTED = "gateway_dc"
    PYAGENT_TIMEOUT = "pyagent_timeout"
    HERMES_CRASH = "hermes_crash"
    MODEL_OVERLOADED = "model_overloaded"
    TOOL_DEGRADED = "tool_degraded"
    MEMORY_CORRUPTION = "memory_corrupt"

RECOVERY_RECIPES = {
    FailureScenario.REDIS_CONNECTION_LOST: RecoveryRecipe(
        steps=[
            RecoveryStep.RECONNECT_REDIS,
            RecoveryStep.VERIFY_AGENTS_ALIVE,  # heartbeat check
        ],
        max_attempts=1,
        escalation=EscalationPolicy.DEGRADE_TO_LOCAL,
        # 降级: 直接调用本地工具，跳过 Redis 路由
    ),

    FailureScenario.GATEWAY_DISCONNECTED: RecoveryRecipe(
        steps=[
            RecoveryStep.RECONNECT_GATEWAY,
            RecoveryStep.RE_REGISTER_MCP,
        ],
        max_attempts=1,
        escalation=EscalationPolicy.QUEUE_AND_RETRY,
        # 降级: 消息队列缓冲，Gateway 恢复后重发
    ),

    FailureScenario.PYAGENT_TIMEOUT: RecoveryRecipe(
        steps=[
            RecoveryStep.CHECK_AGENT_HEALTH,
            RecoveryStep.RESTART_AGENT,
        ],
        max_attempts=1,
        escalation=EscalationPolicy.FALLBACK_TO_HERMES,
        # 降级: Python agent 不可用时，Hermes 替代执行
    ),

    FailureScenario.HERMES_CRASH: RecoveryRecipe(
        steps=[
            RecoveryStep.RESTART_HERMES_RUNTIME,
        ],
        max_attempts=1,
        escalation=EscalationPolicy.LOG_AND_CONTINUE,
        # Hermes 不在热路径，崩溃不影响核心功能
    ),

    FailureScenario.MODEL_OVERLOADED: RecoveryRecipe(
        steps=[
            RecoveryStep.SWITCH_TO_FALLBACK_MODEL,
        ],
        max_attempts=1,
        escalation=EscalationPolicy.ALERT_USER,
    ),

    FailureScenario.TOOL_DEGRADED: RecoveryRecipe(
        steps=[
            RecoveryStep.MARK_TOOL_DEGRADED,
            RecoveryStep.NOTIFY_EVOLUTION_ENGINE,
        ],
        max_attempts=1,
        escalation=EscalationPolicy.DISABLE_TOOL,
    ),

    FailureScenario.MEMORY_CORRUPTION: RecoveryRecipe(
        steps=[
            RecoveryStep.RESTORE_FROM_CHECKPOINT,
            RecoveryStep.REBUILD_INDEX,
        ],
        max_attempts=1,
        escalation=EscalationPolicy.FRESH_SESSION,
    ),
}
```

### Layer 7: 健康监控 + 降级路由

```python
# runtime/resilience/health_monitor.py

class ComponentHealth:
    """每个组件的健康状态"""
    name: str
    status: Literal["healthy", "degraded", "down"]
    last_heartbeat: float
    consecutive_failures: int
    latency_p99_ms: float

class HealthMonitor:
    """
    每10秒检查一次组件健康：
    - Redis: PING
    - Python Agents: heartbeat channel
    - Hermes: thread pool alive check
    - Gateway: WebSocket ping/pong
    - LLM Provider: 最近API调用延迟

    健康状态影响路由决策：
    - healthy → 正常路由
    - degraded → 标记工具为 degraded，通知 LLM
    - down → 触发 Recovery Recipe + 降级路由
    """

    async def get_routing_decision(self, tool_name: str) -> RoutingDecision:
        backend = self.tool_to_backend[tool_name]
        health = self.health_states[backend]

        if health.status == "healthy":
            return RoutingDecision.NORMAL

        if health.status == "degraded":
            return RoutingDecision.WITH_WARNING
            # LLM 会看到: "⚠️ market agent 响应缓慢 (P99: 5.2s)，建议减少数据请求量"

        # down → 查找替代路径
        fallback = self.fallback_routes.get(backend)
        if fallback and self.health_states[fallback].status != "down":
            return RoutingDecision.FALLBACK(fallback)

        return RoutingDecision.UNAVAILABLE
```

---

## 二、自学习纠错系统（4层闭环）

这是相比现有架构最大的提升。参考 Hermes 的 Background Review Loop + claw-code Recovery Recipes + DSPy/GEPA 进化管线。

### 闭环架构

```
┌────────────────────────────────────────────────────────────────┐
│                    Self-Learning Closed Loop                    │
│                                                                │
│  Loop 1: 即时自修正 (秒级)                                     │
│  ┌──────────────────────────────────────────────────────┐      │
│  │ Tool Error → error as tool_result → LLM sees error  │      │
│  │ → LLM adjusts approach → retry (max 3)              │      │
│  │ 参考: claude-code toolExecution.ts                   │      │
│  └──────────────────────────────────────────────────────┘      │
│                                                                │
│  Loop 2: 会话内反思 (分钟级)                                    │
│  ┌──────────────────────────────────────────────────────┐      │
│  │ Background Review Agent Fork                         │      │
│  │ 触发: 每 10 轮对话 OR 每 10 次工具迭代               │      │
│  │ 动作: 分析对话 → 创建/更新 Skill → 更新 Memory      │      │
│  │ 参考: Hermes run_agent.py _spawn_background_review() │      │
│  └──────────────────────────────────────────────────────┘      │
│                                                                │
│  Loop 3: 跨会话学习 (小时级)                                    │
│  ┌──────────────────────────────────────────────────────┐      │
│  │ Evolution Engine (后台 asyncio 任务)                  │      │
│  │ 消费: Redis Stream harness:executions                │      │
│  │ 检测: 重复模式 / 重复失败 / 性能退化                  │      │
│  │ 动作: 创建 Skill / Recovery Recipe / 工具健康更新     │      │
│  │ 参考: claw-code recovery_recipes.rs                  │      │
│  └──────────────────────────────────────────────────────┘      │
│                                                                │
│  Loop 4: 系统进化 (天级)                                       │
│  ┌──────────────────────────────────────────────────────┐      │
│  │ GEPA 进化管线 (外部定时任务)                          │      │
│  │ 读取: 完整执行追踪 (错误日志/性能数据/推理日志)       │      │
│  │ 优化: 系统 prompt / Skill 描述 / 工具参数            │      │
│  │ 方法: 遗传-帕累托进化搜索 (DSPy)                     │      │
│  │ 参考: hermes-agent-self-evolution / GEPA             │      │
│  └──────────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────┘
```

### Loop 1: 即时自修正

已在 Layer 2 (工具执行容错) 中覆盖。关键补充：

```python
# tools/executor.py — 增强版自修正

class SelfCorrectionTracker:
    """跟踪工具自修正历史，供 Loop 2/3 分析"""

    def record_correction(self, tool_name, original_error, correction_action, success):
        """
        记录: 什么工具 → 什么错误 → 怎么修正 → 是否成功
        这些记录被 Loop 2 (Background Review) 消费
        """
        self.corrections.append({
            "tool": tool_name,
            "error": str(original_error),
            "correction": correction_action,
            "success": success,
            "timestamp": time.time(),
        })

    def get_correction_patterns(self) -> list[CorrectionPattern]:
        """
        提取纠错模式：
        - 哪些错误反复出现？
        - 哪些修正策略有效？
        - 哪些工具最容易出错？
        """
```

### Loop 2: 会话内反思 — Background Review Agent

**完全参考 Hermes `_spawn_background_review()` 模式**，这是最关键的自学习机制：

```python
# evolution/background_review.py

class BackgroundReviewSystem:
    """
    参考 Hermes run_agent.py 第1765行 _spawn_background_review()

    计数器驱动:
    - _turns_since_memory: 每次用户对话+1，使用 memory 工具后重置
    - _iters_since_skill: 每次工具迭代+1，使用 skill_manage 后重置

    触发条件:
    - turns_since_memory >= 10 AND memory 工具可用
    - iters_since_skill >= 10 AND skill_manage 工具可用

    执行方式:
    - 创建完整 AIAgent fork (后台守护线程)
    - 相同模型、工具、上下文
    - max_iterations=8, quiet_mode=True
    - 共享 memory_store (写入立即持久化)
    """

    MEMORY_NUDGE_INTERVAL = 10   # 每10轮用户对话触发记忆回顾
    SKILL_NUDGE_INTERVAL = 10    # 每10次工具迭代触发技能回顾

    # 三种回顾 prompt（参考 Hermes 原始实现）
    MEMORY_REVIEW_PROMPT = """
    回顾刚才的对话。用户有没有透露关于自己的信息？
    - 交易偏好、关注的板块、风险承受能力
    - 工作流程习惯、常用命令
    - 如果发现有价值的信息，使用 memory 工具保存到 USER.md
    """

    SKILL_REVIEW_PROMPT = """
    回顾刚才的执行过程。有没有发现值得保存的模式？
    - 是否用了 5+ 次工具调用完成一个任务？
    - 是否经过试错才找到正确方法？
    - 是否有重复出现的工具调用链？
    如果值得保存，使用 skill_manage 创建新的 SKILL.md。
    包含：具体步骤、参数、常见陷阱、验证方法。
    """

    CORRECTION_REVIEW_PROMPT = """
    回顾刚才的纠错过程。分析：
    - 什么操作导致了错误？
    - 错误的根因是什么？
    - 成功的修正策略是什么？
    - 如何预防同类错误？
    如果发现可复用的纠错模式，使用 skill_manage 创建纠错技能。
    """

    async def check_and_spawn(self, session, turn_result):
        should_review_memory = (
            self._turns_since_memory >= self.MEMORY_NUDGE_INTERVAL
            and self.memory_available
        )
        should_review_skill = (
            self._iters_since_skill >= self.SKILL_NUDGE_INTERVAL
            and self.skill_manage_available
        )
        # 新增: 如果本轮有纠错，也触发回顾
        had_corrections = len(self.correction_tracker.corrections) > 0

        if not (should_review_memory or should_review_skill or had_corrections):
            return

        # 选择 prompt
        if had_corrections:
            prompt = self.CORRECTION_REVIEW_PROMPT
        elif should_review_memory and should_review_skill:
            prompt = self.MEMORY_REVIEW_PROMPT + "\n\n" + self.SKILL_REVIEW_PROMPT
        elif should_review_memory:
            prompt = self.MEMORY_REVIEW_PROMPT
        else:
            prompt = self.SKILL_REVIEW_PROMPT

        # Fork agent in background thread (参考 Hermes 模式)
        review_agent = self._fork_agent(
            session=session,
            extra_user_message=prompt,
            max_iterations=8,
            quiet_mode=True,
        )

        thread = threading.Thread(
            target=self._run_review,
            args=(review_agent,),
            daemon=True,
        )
        thread.start()

        # 重置计数器
        if should_review_memory:
            self._turns_since_memory = 0
        if should_review_skill:
            self._iters_since_skill = 0
```

### Loop 3: 跨会话学习 — Evolution Engine

```python
# evolution/engine.py

class EvolutionEngine:
    """
    后台 asyncio 任务，消费执行流水线，发现可学习的模式。

    数据源: Redis Stream "harness:executions"
    每条记录: {tool, action, params, result_summary, success, latency_ms, corrections[], timestamp}
    """

    CHECK_INTERVAL = 300  # 5分钟检查一次

    async def run_forever(self):
        while True:
            await asyncio.sleep(self.CHECK_INTERVAL)
            if self.circuit_breaker.is_open():
                continue

            try:
                events = await self.redis.xread_executions(since=self.last_check)
                self.last_check = time.time()

                # 1. 模式检测
                patterns = self.pattern_detector.detect(events)
                for pattern in patterns:
                    if pattern.occurrence_count >= 3:
                        await self._create_skill_from_pattern(pattern)

                # 2. 失败模式检测
                failure_patterns = self.failure_detector.detect(events)
                for fp in failure_patterns:
                    if fp.occurrence_count >= 3:
                        await self._create_recovery_recipe(fp)

                # 3. 性能退化检测
                degradations = self.perf_detector.detect(events)
                for d in degradations:
                    self.health_monitor.mark_degraded(d.tool, d.reason)

                self.circuit_breaker.record_success()

            except Exception as e:
                self.circuit_breaker.record_failure()
                logger.error(f"Evolution engine error: {e}")

    async def _create_skill_from_pattern(self, pattern: ToolChainPattern):
        """
        检测到重复工具链 → 调用 Hermes 创建 Skill

        例: 用户反复执行 "zt → 筛选半导体 → backtest"
        → 创建 "semiconductor_limitup_backtest" skill
        """
        prompt = f"""
        检测到重复执行模式（{pattern.occurrence_count}次）：
        {pattern.describe()}

        请创建一个可复用的 Skill，包含：
        1. 完整执行步骤
        2. 参数化（哪些参数应由用户指定）
        3. 常见陷阱和错误处理
        4. 验证步骤
        """
        await self.hermes_runtime.run_task(
            prompt=prompt,
            toolsets=["core"],  # 只给 skill_manage 工具
            max_iterations=10,
        )
        # Skill 自动同步到 OpenClaw 和 PyAgent
        await self.skill_sync.sync_all()

    async def _create_recovery_recipe(self, fp: FailurePattern):
        """
        检测到重复失败 → 分析根因 → 生成 Recovery Recipe

        例: pyagent_market_zt 在早盘9:25-9:30之间总是超时
        → 生成 recipe: 该时段自动增加超时到 60s
        """
        prompt = f"""
        检测到重复失败模式（{fp.occurrence_count}次）：
        工具: {fp.tool}
        错误: {fp.common_error}
        上下文: {fp.context_summary}

        分析根因并建议修复方案：
        1. 是参数问题还是系统问题？
        2. 是否有时间规律？
        3. 建议的自动修复策略是什么？
        """
        analysis = await self.hermes_runtime.run_task(
            prompt=prompt, toolsets=["core"], max_iterations=5)

        # 将分析结果转化为 Recovery Recipe
        recipe = self._parse_recipe_from_analysis(analysis)
        self.recovery_registry.register(fp.scenario, recipe)
```

### Loop 4: 系统进化 — GEPA Pipeline

```python
# evolution/gepa_pipeline.py (定时任务，非实时)

class GEPAPipeline:
    """
    参考 hermes-agent-self-evolution + GEPA (ICLR 2026 Oral)

    每日凌晨运行：
    1. 收集过去24小时的完整执行追踪
    2. 提取失败案例和低效路径
    3. 用 DSPy 优化：
       - 系统 prompt（SOUL.md 中的行为指导）
       - Skill 描述（工具选择准确率）
       - 工具参数默认值（减少错误）
    4. A/B 验证：在历史案例上对比优化前后效果
    5. 自动部署通过验证的优化
    """

    async def daily_evolution(self):
        # 1. 收集执行追踪
        traces = await self.collect_traces(hours=24)

        # 2. 提取优化目标
        failures = [t for t in traces if not t.success]
        slow_paths = [t for t in traces if t.latency_ms > t.expected_latency * 2]

        # 3. DSPy 优化
        optimized_prompts = await self.dspy_optimize(
            current_soul=self.load_soul_md(),
            failure_examples=failures[:10],
            metric=self.success_rate_metric,
        )

        # 4. A/B 验证
        improvement = await self.ab_test(
            original=self.current_config,
            candidate=optimized_prompts,
            test_cases=traces[:50],
        )

        # 5. 部署（仅当改进 > 5%）
        if improvement.success_rate_delta > 0.05:
            await self.deploy_optimization(optimized_prompts)
            logger.info(f"GEPA evolution deployed: "
                       f"+{improvement.success_rate_delta:.1%} success rate")
```

### Skill 安全守卫

参考 Hermes `skills_guard.py`：

```python
# evolution/skill_guard.py

class SkillGuard:
    """
    所有自动生成的 Skill 必须通过安全扫描。
    参考 Hermes tools/skills_guard.py。

    扫描类别:
    - exfiltration: curl/wget + secrets, 外发敏感数据
    - injection: prompt injection patterns
    - destructive: rm -rf, DROP TABLE, git push --force
    - persistence: crontab, systemd, launchd
    - obfuscation: base64 encode of commands, eval()

    信任级别:
    - bundled: safe=allow, caution=allow, dangerous=allow
    - agent-created: safe=allow, caution=allow, dangerous=ASK_USER
    - hub-installed: safe=allow, caution=ASK_USER, dangerous=DENY
    """
```

---

## 三、5 层上下文压缩（完整设计）

参考 claude-code 的 5 层系统，适配 RRCLAW：

```python
# context/engine.py

class ContextEngine:
    """
    每次 LLM 调用前，按顺序执行 5 层压缩。
    参考 claude-code query.ts 第 400-500 行。
    """

    async def prepare(self, session: Session) -> Context:
        messages = session.messages.copy()

        # Layer 1: Tool Result Budget
        # 大结果 → 持久化到磁盘 + 预览摘要
        messages = self.tool_result_budget.apply(messages)

        # Layer 2: History Snip
        # 旧对话段 → 截断标记 [--- 已省略 N 轮对话 ---]
        messages = self.history_snip.apply(messages)

        # Layer 3: Microcompact
        # 旧工具结果内联折叠（不需要 LLM，基于规则）
        messages = self.microcompact.apply(messages)

        # Layer 4: Context Collapse
        # 投影式归档：旧消息 → 摘要块
        messages = await self.context_collapse.apply(messages)

        # Layer 5: Autocompact (带 circuit breaker)
        # token 超阈值 → fork agent 做完整总结
        if self._should_autocompact(messages):
            if not self.autocompact_breaker.is_open():
                try:
                    messages = await self.autocompact.apply(messages)
                    self.autocompact_breaker.record_success()
                except Exception:
                    self.autocompact_breaker.record_failure()

        return self._build_context(messages)
```

---

## 四、代码结构（更新）

```
rrclaw/
├── runtime/
│   ├── conversation.py          # ConversationRuntime (核心循环)
│   ├── session.py               # JSONL 持久化 + 轮转 + compaction
│   ├── config.py                # 三级配置合并
│   ├── prompt.py                # 系统 prompt 构建 + Skill 索引注入
│   ├── hooks.py                 # PreToolUse / PostToolUse
│   ├── resilience/
│   │   ├── api_retry.py         # 指数退避 + 模型降级
│   │   ├── circuit_breaker.py   # 通用 circuit breaker
│   │   ├── recovery_recipes.py  # 7种故障场景恢复
│   │   ├── health_monitor.py    # 组件健康 + 降级路由
│   │   └── overflow_recovery.py # 上下文溢出4阶段恢复
│   └── providers/
│       ├── base.py, anthropic.py, openai_compat.py, dashscope.py
│       └── router.py            # 前缀路由 + fallback chain
│
├── tools/
│   ├── base.py                  # Tool 基类 (参考 claude-code Tool.ts)
│   ├── registry.py              # GlobalToolRegistry
│   ├── executor.py              # 并发/串行分区 + 错误容纳 + 自修正跟踪
│   ├── search.py                # ToolSearch 惰性加载
│   ├── builtin/                 # ~10 内置工具
│   ├── pyagent/                 # 71 命令 (should_defer=True)
│   ├── hermes/                  # 47 工具 + PTC (should_defer=True)
│   └── mcp/                     # MCP 客户端/服务端
│
├── context/
│   ├── engine.py                # 5层压缩总调度
│   ├── tool_result_budget.py    # L1
│   ├── history_snip.py          # L2
│   ├── microcompact.py          # L3
│   ├── context_collapse.py      # L4
│   ├── autocompact.py           # L5 (with circuit breaker)
│   └── memory/                  # 3层记忆
│
├── permissions/
│   ├── policy.py                # safe/aware/consent/critical
│   ├── enforcer.py              # 工作空间边界 + bash 只读检测
│   └── trust.py                 # TrustResolver
│
├── workers/
│   ├── boot.py                  # Worker Boot 状态机
│   ├── coordinator.py           # Coordinator Mode
│   └── task_packet.py           # TaskPacket + 验收测试
│
├── evolution/                   # ⭐ 自学习核心
│   ├── background_review.py     # Loop 2: 会话内反思 (Hermes 模式)
│   ├── engine.py                # Loop 3: 跨会话 Evolution Engine
│   ├── gepa_pipeline.py         # Loop 4: GEPA 日级进化
│   ├── pattern_detector.py      # 工具链模式检测
│   ├── failure_detector.py      # 失败模式检测
│   ├── perf_detector.py         # 性能退化检测
│   ├── skill_creator.py         # 自动 Skill 生成
│   ├── skill_guard.py           # Skill 安全扫描
│   ├── correction_tracker.py    # 纠错记录 + 模式提取
│   └── recovery.py              # 自动 Recovery Recipe 生成
│
├── channels/                    # Gateway 集成
├── skills/                      # Skill 系统
├── commands/                    # Slash 命令
└── deploy/                      # 部署配置
```

---

## 五、相比现有架构的核心提升

| 维度 | 现有架构 | RRCLAW 重构 |
|------|---------|------------|
| **容错** | 无。任何组件崩溃直接不可用 | 7层容错：重试→断路器→恢复方案→降级路由→死循环防护 |
| **自学习** | 无。每次从零开始 | 4层闭环：即时自修正→会话内反思→跨会话进化→GEPA优化 |
| **上下文管理** | 无。长对话直接 OOM | 5层压缩 + 3层记忆 + 严格 token 预算 |
| **工具加载** | 全量注入 prompt（浪费 90% token） | ToolSearch 惰性加载（按需发现） |
| **模型降级** | 固定单模型 | Provider fallback chain (Anthropic→DashScope→Ollama) |
| **错误处理** | 工具错误 = 任务失败 | 错误即结果 + LLM 自修正（3次） + 纠错模式提取 |
| **Skill 积累** | 无 | 自动创建 Skill + 安全扫描 + 双向同步 |
| **健康监控** | heartbeat only | 实时健康 + P99延迟 + 降级路由 + 告警 |
| **会话持续** | 无 | JSONL 持久化 + Ralph Loop 跨窗口恢复 |
| **权限模型** | 无 | 4层 (safe/aware/consent/critical) + hook override |

---

## 六、OpenClaw 原生集成（基于 openclaw/openclaw 真实架构）

OpenClaw 真实架构要点（从源码确认）：
- Gateway 是 WS+HTTP server，协议 v3（req/res/event 帧）
- MCP 配置在 `openclaw.json` 的 `mcp.servers` 下（非 `mcpServers`）
- Agent Bindings 路由消息到特定 agent（按 channel/peer/guild 匹配）
- Workspace 文件：SOUL.md, TOOLS.md, AGENTS.md, USER.md, HEARTBEAT.md, BOOTSTRAP.md, MEMORY.md, BOOT.md
- Embedded Pi runtime 是核心 agent loop（非外部调用）
- 支持 ACP (Agent Communication Protocol) 外部 runtime
- Skill 格式：YAML frontmatter + Markdown body
- Hook 系统：HTTP webhook + 内部生命周期 hook
- Canvas/A2UI 用于可视化输出

### 6.1 RRCLAW 作为 OpenClaw MCP Server

```json5
// ~/.openclaw/openclaw.json
{
  "mcp": {
    "servers": {
      "rrclaw-pyagent": {
        "command": "/opt/rrclaw/.venv/bin/python",
        "args": ["-m", "rrclaw.tools.mcp.server", "--backend", "pyagent"],
        "env": { "REDIS_URL": "redis://127.0.0.1:6379/0" }
      },
      "rrclaw-hermes": {
        "command": "/opt/rrclaw/.venv/bin/python",
        "args": ["-m", "rrclaw.tools.mcp.server", "--backend", "hermes"],
        "env": { "HERMES_AGENT_PATH": "/opt/hermes-agent" }
      }
    }
  }
}
```

Gateway 启动时 spawn 这两个子进程为 stdio MCP server。Pi runtime 在每次 agent run 时将 MCP 工具 materialize 到 tool catalog 中。

### 6.2 RRCLAW 作为 OpenClaw ACP Runtime

更深层集成：RRCLAW 注册为 ACP 外部 runtime，完全接管 agent loop：

```json5
// ~/.openclaw/openclaw.json
{
  "agents": {
    "list": [{
      "id": "rrclaw",
      "runtime": { "type": "acp", "url": "ws://127.0.0.1:7790" },
      "workspace": "~/.openclaw/workspace",
      "skills": ["market-analysis", "strategy-backtest"]
    }]
  },
  "bindings": [{
    "agentId": "rrclaw",
    "match": { "channel": "telegram" }
  }, {
    "agentId": "rrclaw",
    "match": { "channel": "webchat" }
  }]
}
```

这样 RRCLAW 的 ConversationRuntime 完全接管 LLM 循环，OpenClaw Gateway 只负责通道收发。

### 6.3 Workspace 文件适配

```
~/.openclaw/workspace/
├── SOUL.md          # Claw 🦞 A股量化助手身份（已有，保持）
├── TOOLS.md         # RRCLAW MCP server 配置（新增）
├── AGENTS.md        # 工作流约定 + RRCLAW 工具使用说明（更新）
├── USER.md          # 用户偏好（RRCLAW evolution 自动更新）
├── HEARTBEAT.md     # 定时任务：早盘总结、收盘分析（已有）
├── BOOTSTRAP.md     # 启动时加载的上下文文件列表
├── MEMORY.md        # 长期记忆（RRCLAW 三层记忆 Tier 1 索引）
└── BOOT.md          # Gateway 启动时执行：验证 RRCLAW 服务健康
```

### 6.4 OpenClaw Hook 集成

```json5
// ~/.openclaw/openclaw.json
{
  "hooks": {
    "mappings": [{
      "match": { "path": "rrclaw/evolution" },
      "action": "agent",
      "agentId": "rrclaw",
      "messageTemplate": "Evolution update: {{body.summary}}"
    }]
  }
}
```

Evolution Engine 通过 HTTP hook 通知 Gateway，将学习结果推送给用户。

### 6.5 Canvas/A2UI 用于市场数据可视化

```python
# tools/builtin/canvas.py

class CanvasTool(Tool):
    """
    通过 OpenClaw Canvas 渲染交互式图表。
    参考 openclaw/src/canvas-host/

    用例:
    - 涨停板热力图
    - 策略回测收益曲线
    - 板块资金流向 Sankey 图
    - 持仓风险仪表盘
    """
    async def call(self, input: dict) -> ToolResult:
        html = self.render_chart(input["chart_type"], input["data"])
        await self.gateway.canvas_present(html)
        return ToolResult(content="Chart rendered to Canvas")
```

---

## 七、Hermes Agent 原生集成（基于 NousResearch/hermes-agent 真实架构）

从源码确认的真实架构要点：
- `run_agent.py` 9600行，完全同步循环（非 async）
- PTC 通过 `execute_code` 工具，两种传输：UDS（本地）、文件 RPC（远程）
- Background Review 是 daemon 线程，`max_iterations=8`，共享 memory/skill store
- IterationBudget：parent=90, subagent=50，PTC 调用可 refund
- Credential Pool：4策略（fill_first, round_robin, random, least_used）+ 1小时冷却
- Error Classification：`FailoverReason` enum + `ClassifiedError` with recovery hints
- MCP Client：后台事件循环，自动重连，动态工具发现
- Context Compressor：剪裁旧工具结果 → 总结中间轮次（保护头尾）
- Prompt Caching：Claude 模型自动注入 cache_control 断点

### 7.1 完全复用 Hermes 原生能力

```python
# tools/hermes/runtime.py

class HermesNativeRuntime:
    """
    直接使用 Hermes AIAgent 类，不做二次封装。

    关键: 复用 Hermes 的原生能力而非重新实现:
    - IterationBudget (含 refund)
    - Credential Pool (4策略)
    - Error Classification + Failover
    - Context Compressor
    - Background Review (daemon thread)
    - PTC (execute_code via UDS)
    - Session Persistence (SQLite FTS5)
    """

    def __init__(self, hermes_path: str):
        sys.path.insert(0, hermes_path)
        from run_agent import AIAgent
        from agent.credential_pool import CredentialPool
        from agent.error_classifier import classify_error

        self.AIAgent = AIAgent
        self.credential_pool = CredentialPool.from_config()

    async def run_task(self, prompt, *, toolsets=None, max_iterations=30) -> HermesResult:
        """在线程池中运行完整 Hermes agent loop"""
        agent = self.AIAgent(
            model=self.model,
            enabled_toolsets=toolsets or ["core", "web", "terminal"],
            max_iterations=max_iterations,
            # 关键: 启用 background review
            background_review=True,
            memory_nudge_interval=10,
            skill_nudge_interval=10,
        )
        # Hermes 是同步的，包在 executor 中
        result = await asyncio.get_event_loop().run_in_executor(
            self.executor,
            agent.run_conversation,
            prompt,
        )
        return self._parse_result(result)
```

### 7.2 Hermes MCP Client 连接 RRCLAW

Hermes 作为 MCP **客户端**连接到 RRCLAW 暴露的 MCP server，获取 PyAgent 工具：

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  rrclaw:
    command: "/opt/rrclaw/.venv/bin/python"
    args: ["-m", "rrclaw.tools.mcp.server", "--backend", "pyagent"]
    env:
      REDIS_URL: "redis://127.0.0.1:6379/0"
```

这样 Hermes 在 PTC 脚本中可以直接调用 PyAgent 工具：
```python
# PTC 脚本示例（Hermes agent 生成的 Python）
from hermes_tools import mcp_rrclaw_pyagent_market_zt
limitup = mcp_rrclaw_pyagent_market_zt(page_size=50)
```

### 7.3 Hermes 的 Credential Pool 复用

```python
# runtime/providers/credential_pool.py

class CredentialPool:
    """
    完全参考 Hermes agent/credential_pool.py

    策略:
    - fill_first: 用尽一个再换下一个（最大化单 key 缓存）
    - round_robin: 轮流使用（均衡负载）
    - random: 随机选择（防止 thundering herd）
    - least_used: 选最少使用的

    + 1小时冷却：429/402 后该凭证冷却1小时
    + OAuth 自动刷新
    """
```

### 7.4 Hermes 的 Error Classification 复用

```python
# runtime/resilience/error_classifier.py

class FailoverReason(Enum):
    """参考 Hermes agent/error_classifier.py"""
    AUTH = "auth"
    BILLING = "billing"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_NOT_FOUND = "model_not_found"
    FORMAT_ERROR = "format_error"

@dataclass
class ClassifiedError:
    reason: FailoverReason
    retryable: bool
    should_compress: bool       # context_overflow → 触发压缩
    should_rotate_credential: bool  # rate_limit → 换 key
    should_fallback: bool       # 连续失败 → 换 provider
    cooldown_seconds: int       # 该凭证的冷却时间
```

---

## 八、Autoresearch 自主实验模式（基于 pagliazi/autoresearch）

Karpathy 的 autoresearch 模式可以直接应用于**策略优化**和**系统自进化**。

### 8.1 策略优化实验循环

```python
# evolution/autoresearch_loop.py

class StrategyResearchLoop:
    """
    参考 autoresearch (Karpathy):
    人类写 program.md → agent 写 Python → 执行 → 评估 → 保留/丢弃

    应用到 A 股策略优化:
    人类写策略方向 → agent 修改策略代码 → 回测 → 评估收益/风险 → 保留/丢弃

    无限循环，直到人工中断。
    """

    RESEARCH_PROGRAM = """
    ## Strategy Research Program (program.md pattern)

    目标: 优化 {strategy_name} 的夏普比率

    规则:
    1. 每次实验修改 strategy.py 的一个方面
    2. 运行回测: rrclaw backtest --strategy strategy.py --period 2024-01-01:2025-12-31
    3. 评估指标: sharpe_ratio, max_drawdown, annual_return
    4. 如果 sharpe_ratio 提升 > 0.05: 保留 (git commit)
    5. 如果 sharpe_ratio 下降: 丢弃 (git reset)
    6. 记录到 results.tsv
    7. 继续下一个实验（永不停止）

    实验方向:
    - 参数调优 (窗口期, 阈值, 止损线)
    - 因子组合 (动量+价值, 技术+基本面)
    - 入场/出场条件
    - 仓位管理
    """

    async def run_experiment_loop(self, strategy_path: str, max_experiments: int = 100):
        # 1. 建立基线
        baseline = await self.run_backtest(strategy_path)
        best_sharpe = baseline.sharpe_ratio
        results = [baseline]

        for i in range(max_experiments):
            # 2. Agent 提出并实施修改
            modification = await self.hermes_runtime.run_task(
                prompt=f"基于当前策略（夏普比 {best_sharpe:.2f}），"
                       f"提出一个改进方案并修改 {strategy_path}",
                toolsets=["core", "file"],
                max_iterations=10,
            )

            # 3. 回测评估
            result = await self.run_backtest(strategy_path)

            # 4. 保留/丢弃（参考 autoresearch 的 git commit/reset 模式）
            if result.sharpe_ratio > best_sharpe + 0.05:
                await self.git_commit(f"Experiment #{i}: sharpe {result.sharpe_ratio:.2f}")
                best_sharpe = result.sharpe_ratio
                logger.info(f"✓ Experiment #{i} KEPT: sharpe {result.sharpe_ratio:.2f}")
            else:
                await self.git_reset()
                logger.info(f"✗ Experiment #{i} DISCARDED: sharpe {result.sharpe_ratio:.2f}")

            results.append(result)
            self._update_results_tsv(results)

        return results
```

### 8.2 系统 Prompt 自优化实验循环

```python
# evolution/prompt_research_loop.py

class PromptResearchLoop:
    """
    autoresearch 模式应用于系统 prompt 优化:
    - 修改 SOUL.md → 在历史案例上评估 → 保留/丢弃

    评估指标: 工具选择准确率 + 任务完成率
    """

    async def run_prompt_experiments(self, test_cases: list[TestCase]):
        baseline_score = await self.evaluate(self.current_soul, test_cases)

        for i in range(50):
            # Agent 修改 SOUL.md
            candidate_soul = await self.hermes_runtime.run_task(
                prompt=f"当前系统 prompt 在以下案例中表现不佳:\n"
                       f"{self._format_failures(test_cases)}\n"
                       f"请优化 SOUL.md 以改善这些案例的表现",
                toolsets=["core", "file"],
            )

            # 评估
            score = await self.evaluate(candidate_soul, test_cases)

            if score > baseline_score * 1.05:  # 5% improvement
                self._deploy_soul(candidate_soul)
                baseline_score = score
            else:
                self._rollback_soul()
```

### 8.3 Git 即实验追踪

参考 autoresearch 的 `results.tsv` + git commit 模式：

```
~/.rrclaw/experiments/
├── strategies/
│   ├── .git/                    # 每个成功实验 = 一个 commit
│   ├── strategy.py              # 当前最优策略
│   └── results.tsv              # experiment_id  sharpe  drawdown  return  status  description
├── prompts/
│   ├── .git/
│   ├── SOUL.md                  # 当前最优 prompt
│   └── results.tsv              # experiment_id  accuracy  completion_rate  status  description
└── skills/
    ├── .git/
    └── results.tsv              # skill_name  usage_count  success_rate  avg_latency
```

---

## 九、完整数据流（端到端）

```
用户 (Telegram) "今天涨停板有哪些半导体？"
    │
    ▼
OpenClaw Gateway (:18789)
    │ Bindings: channel=telegram → agentId=rrclaw
    │
    ▼ (二选一)
Option A: MCP 模式
    │ Gateway Pi runtime → 调用 rrclaw-pyagent MCP server
    │ MCP tools/list → 返回 Tier 0 工具列表
    │ LLM 决定调用 tool_search("涨停 半导体")
    │ → 返回 pyagent_market_zt + pyagent_market_bk 的完整 Schema
    │ LLM 调用 pyagent_market_zt(page_size=50)
    │ → MCP server → Redis Pub/Sub → Python market agent → 返回数据
    │ LLM 筛选半导体 → 格式化回复
    │
Option B: ACP 模式 (更深集成)
    │ Gateway WS → RRCLAW ConversationRuntime (接管 agent loop)
    │ → 5层上下文压缩
    │ → ToolSearch 发现 pyagent_market_zt
    │ → ToolExecutor 并发执行（market_zt is_concurrency_safe=True）
    │ → Redis Pub/Sub → Python market agent → 返回数据
    │ → 权限检查 (market data = SAFE, 自动通过)
    │ → Evolution Stream 记录执行
    │ → LLM 筛选半导体 → 格式化回复
    │
    ▼
OpenClaw Gateway → Telegram API → 用户看到回复

    同时（后台，非阻塞）:
    │
    ▼
Background Review (if turns >= 10)
    │ Fork agent 分析对话
    │ → 发现用户经常查询半导体板块
    │ → memory("add", "用户关注半导体板块") → USER.md
    │
Evolution Engine (if pattern count >= 3)
    │ 检测到 "zt → 筛选板块 → 格式化" 重复3次
    │ → Hermes 创建 Skill "semiconductor_limitup_report"
    │ → SkillBridge 同步到 OpenClaw + Hermes
    │ → 下次用户问同样问题，直接触发 Skill
```

---

## 十、验证方案

### 功能验证
1. **MCP 端到端**: Telegram "今天涨停板" → Gateway → MCP → Redis → market agent → 返回
2. **ACP 端到端**: WebChat 发消息 → Gateway → RRCLAW ConversationRuntime → 完整 agent loop
3. **ToolSearch**: 验证 `tool_search("回测")` 返回 pyagent_backtest 相关工具
4. **PTC**: Hermes PTC 脚本调用 RRCLAW MCP 工具成功
5. **Canvas**: 涨停板数据 → Canvas 热力图渲染

### 稳定性验证
6. **Redis 故障**: kill Redis → 验证降级到本地 → Redis 恢复后重连
7. **Provider Fallback**: 模拟 Anthropic 529 → 验证切换到 DashScope
8. **Circuit Breaker**: autocompact 连续3次失败 → 验证停止尝试
9. **Overflow Recovery**: 注入大量对话 → 验证4阶段恢复
10. **Death Spiral**: API 错误后 → 验证跳过 hooks

### 自学习验证
11. **Background Review**: 连续10轮对话 → 验证 daemon thread 创建 Skill/Memory
12. **Evolution Engine**: 重复3次工具链 → 验证模式检测 → Skill 生成
13. **Correction Pattern**: 工具失败后自修正 → 验证纠错模式被提取
14. **Strategy Research**: autoresearch loop → 验证策略夏普比提升
15. **Prompt Research**: SOUL.md 优化实验 → 验证工具选择准确率提升

### 安全验证
16. **Skill Guard**: agent 创建含 `rm -rf` 的 Skill → 验证被拦截
17. **Permission Tiers**: shell 命令 → 验证需要 CONSENT 确认
18. **Trust Resolver**: Worker 启动 → 验证信任网关流程

---

## 十一、ConversationRuntime 完整设计（核心循环）

这是整个 harness 的心脏。参考 `claude-code/src/query.ts` async generator + `claw-code/conversation.rs` ConversationRuntime + `hermes-agent/run_agent.py` while 循环。

### 11.1 生命周期状态机

```
┌─────────┐    init()     ┌──────────┐   stream()   ┌───────────┐
│  IDLE   │──────────────→│ PREPARED │─────────────→│ STREAMING │
└─────────┘               └──────────┘              └─────┬─────┘
                                                          │
                          ┌───────────────────────────────┘
                          │ response contains tool_use?
                          ▼
                    ┌─────────────┐  execute tools   ┌──────────────┐
                    │ TOOL_DISPATCH│─────────────────→│ TOOL_RUNNING │
                    └─────────────┘                  └──────┬───────┘
                          ▲                                 │
                          │     tool_results ready          │
                          └────────────────────────────────┘
                          │
                          │ no more tool_use (end_turn / stop)
                          ▼
                    ┌───────────┐   background review   ┌──────────┐
                    │ POST_TURN │──────────────────────→│ REVIEW   │
                    └─────┬─────┘                       └──────────┘
                          │                              (daemon, async)
                          ▼
                    ┌───────────┐
                    │ YIELD_RESP│ → 返回给用户
                    └───────────┘
```

### 11.2 核心 async generator 循环

```python
# runtime/conversation.py

from typing import AsyncGenerator, Protocol
from dataclasses import dataclass, field

class ContextProvider(Protocol):
    """参考 claw-code ConversationRuntime<C, T> 的 C 泛型"""
    async def prepare(self, session: "Session") -> "Context": ...

class ToolProvider(Protocol):
    """参考 claw-code ConversationRuntime<C, T> 的 T 泛型"""
    async def execute(self, tool_use: "ToolUse") -> "ToolResult": ...
    def get_tier0_tools(self) -> list["ToolSpec"]: ...

@dataclass
class TurnConfig:
    max_tool_rounds: int = 30          # 单轮最大工具调用次数
    iteration_budget: int = 90          # 参考 Hermes parent=90
    budget_refund_on_ptc: int = 5       # PTC 调用 refund
    streaming: bool = True              # SSE token-by-token
    skip_hooks_on_api_error: bool = True # 死循环防护

@dataclass
class ConversationRuntime:
    """
    RRCLAW 的核心 LLM 循环。

    设计来源:
    - claude-code query.ts: async generator yield 模式
    - claw-code conversation.rs: ConversationRuntime<C, T> 泛型
    - hermes run_agent.py: iteration budget + background review

    关键设计决策:
    1. async generator（不是 callback）— 调用方用 async for 消费
    2. Context 和 Tool 通过 Protocol 注入（不是硬编码）
    3. 每次 LLM 调用前执行 5 层上下文压缩
    4. 工具错误不中断循环（错误即结果）
    5. API 错误后跳过 hooks（死循环防护）
    6. iteration budget 每次工具调用扣 1，PTC 可 refund
    """

    context_provider: ContextProvider
    tool_provider: ToolProvider
    api_client: "ApiClient"
    session: "Session"
    config: TurnConfig = field(default_factory=TurnConfig)

    # 内部状态
    _iteration_budget: int = 0
    _skip_hooks: bool = False
    _background_review: "BackgroundReviewSystem | None" = None

    async def run_turn(self, user_message: str) -> AsyncGenerator["TurnEvent", None]:
        """
        处理一轮用户消息，yield 所有事件。

        参考 claude-code query.ts 的 async generator 模式：
        调用方:
            async for event in runtime.run_turn("今天涨停板"):
                if event.type == "text":
                    send_to_user(event.text)
                elif event.type == "tool_use":
                    show_tool_call(event)
        """
        # 0. 追加用户消息到会话
        self.session.append_user(user_message)
        self._iteration_budget = self.config.iteration_budget
        self._skip_hooks = False
        tool_round = 0

        while tool_round < self.config.max_tool_rounds:
            # ── 1. 上下文压缩（每次 LLM 调用前） ──
            try:
                context = await self.context_provider.prepare(self.session)
            except ContextOverflowError as e:
                recovery = await self._overflow_recovery(e)
                if recovery.recovered:
                    context = await self.context_provider.prepare(self.session)
                else:
                    yield TurnEvent.error(recovery.message)
                    return

            # ── 2. LLM 调用（streaming） ──
            try:
                response = self.api_client.stream(
                    messages=context.messages,
                    system=context.system_prompt,
                    tools=context.tool_schemas,
                    model=context.model,
                )

                # 2a. Streaming yield — 逐 token 返回文本
                assistant_message = AssistantMessage()
                async for chunk in response:
                    if chunk.type == "text_delta":
                        assistant_message.append_text(chunk.text)
                        yield TurnEvent.text_delta(chunk.text)
                    elif chunk.type == "tool_use":
                        assistant_message.append_tool_use(chunk.tool_use)
                    elif chunk.type == "usage":
                        self.session.record_usage(chunk.usage)

            except ApiError as e:
                # ── 死循环防护: API 错误后跳过 hooks ──
                self._skip_hooks = True
                classified = classify_error(e)

                if classified.should_compress:
                    # context_overflow → 触发压缩后重试
                    await self.context_provider.force_compact(self.session)
                    continue  # 重新进入循环

                if classified.should_rotate_credential:
                    self.api_client.rotate_credential()
                    continue

                if classified.should_fallback:
                    self.api_client.switch_to_fallback()
                    continue

                # 不可恢复 → 通知用户
                yield TurnEvent.error(f"API Error: {classified.reason}")
                return

            # ── 3. 保存 assistant 消息到会话 ──
            self.session.append_assistant(assistant_message)

            # ── 4. 检查是否有工具调用 ──
            tool_uses = assistant_message.tool_uses
            if not tool_uses:
                break  # 无工具调用 = LLM 完成思考，退出循环

            # ── 5. Iteration Budget 检查 ──
            self._iteration_budget -= len(tool_uses)
            if self._iteration_budget <= 0:
                yield TurnEvent.warning(
                    f"迭代预算耗尽 ({self.config.iteration_budget} 次)，"
                    f"停止工具调用。"
                )
                break

            # ── 6. 工具执行（并发/串行分区） ──
            #   参考 claude-code: isConcurrencySafe → 并发
            #                     否则 → 串行
            concurrent = [tu for tu in tool_uses
                          if self.tool_provider.is_concurrent_safe(tu.name)]
            sequential = [tu for tu in tool_uses
                          if not self.tool_provider.is_concurrent_safe(tu.name)]

            results: list[ToolResult] = []

            # 6a. 并发工具同时执行
            if concurrent:
                yield TurnEvent.tool_batch_start(concurrent)
                concurrent_results = await asyncio.gather(
                    *[self.tool_provider.execute(tu) for tu in concurrent],
                    return_exceptions=True,
                )
                for tu, r in zip(concurrent, concurrent_results):
                    if isinstance(r, Exception):
                        r = ToolResult(is_error=True, content=str(r))
                    results.append(r)
                    yield TurnEvent.tool_result(tu, r)

            # 6b. 串行工具逐个执行
            for tu in sequential:
                yield TurnEvent.tool_start(tu)
                r = await self.tool_provider.execute(tu)
                results.append(r)
                yield TurnEvent.tool_result(tu, r)

                # PTC refund: execute_code 成功 → 返还预算
                if tu.name == "execute_code" and not r.is_error:
                    self._iteration_budget += self.config.budget_refund_on_ptc

            # ── 7. 追加 tool_results 到会话 ──
            self.session.append_tool_results(tool_uses, results)

            # ── 8. Pre-tool hooks（但 API 错误后跳过） ──
            if not self._skip_hooks:
                hook_result = await self._run_post_tool_hooks(tool_uses, results)
                if hook_result and hook_result.inject_message:
                    self.session.append_system(hook_result.inject_message)

            # ── 9. 纠错跟踪 ──
            for tu, r in zip(tool_uses, results):
                if r.is_error:
                    self._correction_tracker.record_error(tu, r)
                else:
                    self._correction_tracker.record_success(tu)

            tool_round += 1

        # ── 10. Post-turn: Background Review ──
        if self._background_review:
            self._background_review.increment_turn()
            self._background_review.increment_iterations(tool_round)
            await self._background_review.check_and_spawn(self.session, None)

        # ── 11. 会话持久化 ──
        await self.session.persist()

        yield TurnEvent.turn_complete()
```

### 11.3 Session JSONL 持久化

```python
# runtime/session.py

class Session:
    """
    参考 claw-code session JSONL (256KB 轮转)。

    每条消息一行 JSON，支持:
    - 追加写（append-only，崩溃安全）
    - 256KB 自动轮转（旧文件 gzip 归档）
    - 从 JSONL 恢复（断电重启后恢复到最后状态）
    - compaction（压缩后重写，减少磁盘占用）
    """

    ROTATION_SIZE = 256 * 1024  # 256KB
    SESSION_DIR = "~/.rrclaw/sessions/"

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.messages: list[Message] = []
        self._file = open(self._path(), "a")
        self._usage = UsageTracker()

    def append_user(self, text: str):
        msg = Message(role="user", content=text)
        self.messages.append(msg)
        self._write_jsonl(msg)

    def append_assistant(self, msg: AssistantMessage):
        self.messages.append(msg)
        self._write_jsonl(msg)

    def append_tool_results(self, tool_uses, results):
        for tu, r in zip(tool_uses, results):
            msg = Message(role="tool", tool_use_id=tu.id, content=r.content)
            self.messages.append(msg)
            self._write_jsonl(msg)

    async def persist(self):
        """flush + 检查轮转"""
        self._file.flush()
        if os.path.getsize(self._path()) > self.ROTATION_SIZE:
            await self._rotate()

    def _write_jsonl(self, msg):
        self._file.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

    async def _rotate(self):
        """关闭当前文件 → gzip → 新建空文件"""
        self._file.close()
        await asyncio.to_thread(self._gzip_current)
        self._file = open(self._path(), "a")

    @classmethod
    def restore(cls, session_id: str) -> "Session":
        """从 JSONL 恢复完整会话（崩溃恢复）"""
        session = cls(session_id)
        for line in open(session._path()):
            msg = Message.from_dict(json.loads(line))
            session.messages.append(msg)
        return session
```

### 11.4 Streaming 与 非 Streaming 模式

```python
# runtime/conversation.py — streaming 适配

class ApiClient:
    """
    两种调用模式:

    Streaming (默认，用于交互式对话):
      - SSE token-by-token，用户立即看到文字
      - yield TextDelta 事件
      - 参考 claude-code query.ts 的 streaming 处理

    Non-streaming (用于 Background Review / Evolution):
      - 一次性返回完整响应
      - 减少连接开销
      - Background Review 不需要实时展示
    """

    async def stream(self, *, messages, system, tools, model) -> AsyncGenerator:
        """Streaming 模式: yield chunks"""
        async with self._provider.stream_create(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
        ) as stream:
            async for event in stream:
                yield event

    async def complete(self, *, messages, system, tools, model) -> "Response":
        """Non-streaming 模式: 返回完整响应"""
        return await self._provider.create(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
        )
```

---

## 十二、ToolSearch 惰性加载完整设计

参考 `claude-code/src/tools/ToolSearch.ts`：只有 Tier 0 工具的 schema 注入 LLM prompt，其余通过 `tool_search` 按需发现。

### 12.1 工具分层

```
Tier 0 — 始终加载（schema 注入每次 LLM 调用）
  约 8 个工具，占 ~2K tokens
  LLM 必须知道这些工具才能完成基本操作

Tier 1 — 惰性加载（仅名称+描述注入，schema 按需）
  约 100+ 个工具，名称+描述索引占 ~3K tokens
  LLM 通过 tool_search 发现后获得完整 schema

Tier 2 — 按需发现（连索引都不注入）
  MCP 外部工具、社区工具
  仅在 tool_search 关键词匹配时出现
```

### 12.2 Tier 0 工具清单

```python
# tools/search.py — Tier 0 清单

TIER_0_TOOLS = [
    # 基础交互（每次对话必需）
    "tool_search",       # 发现其他工具的入口（自引用）
    "bash",              # Shell 执行
    "read_file",         # 读文件
    "write_file",        # 写文件
    "edit_file",         # 编辑文件

    # A股核心（最高频业务工具）
    "market_query",      # 统一市场查询入口（涵盖 zt/lb/bk/hot/summary）
    "canvas",            # 可视化输出到 OpenClaw Canvas

    # 记忆与技能（自学习入口）
    "memory",            # 读写长期记忆
]
```

**为什么 `market_query` 而不是 5 个分开的工具？**

现有 PyAgent 有 5 个市场命令（zt/lb/bk/hot/summary），但作为 Tier 0 放 5 个太多。合并为一个 `market_query(type="limitup|连板|concepts|hot|summary", ...)` 入口，LLM 通过 type 参数选择。具体实现仍然路由到 5 个 PyAgent 命令。

### 12.3 Tier 1 索引格式

```python
# tools/search.py — Tier 1 索引

@dataclass
class ToolIndex:
    """每个惰性工具的索引条目"""
    name: str                    # 唯一标识
    description: str             # 1-2 句中文描述（用于 LLM 匹配）
    keywords: list[str]          # 搜索关键词（中英文混合）
    agent: str                   # 所属 agent（market/dev/backtest/...）
    category: str                # 分类标签
    timeout: int                 # 超时秒数
    is_concurrent_safe: bool     # 是否可并发
    should_defer: bool = True    # Tier 1 默认 defer

# 索引示例（从 command_registry 107 个命令自动生成）
TIER_1_INDEX = [
    ToolIndex(
        name="pyagent_backtest",
        description="运行量化策略回测，支持自定义时间范围和策略参数",
        keywords=["回测", "backtest", "策略", "夏普", "收益", "drawdown"],
        agent="backtest", category="quant", timeout=300,
        is_concurrent_safe=False,  # 长时运算，不并发
    ),
    ToolIndex(
        name="pyagent_factor_mining",
        description="因子挖掘与PBO交叉验证，从历史数据中发现有效因子",
        keywords=["因子", "factor", "挖掘", "mining", "PBO", "alpha"],
        agent="backtest", category="quant", timeout=620,
        is_concurrent_safe=False,
    ),
    ToolIndex(
        name="pyagent_screener",
        description="多条件选股器，根据技术指标和基本面筛选股票",
        keywords=["选股", "screener", "筛选", "条件", "过滤"],
        agent="backtest", category="quant", timeout=60,
        is_concurrent_safe=True,
    ),
    ToolIndex(
        name="pyagent_dev_claude",
        description="调用 Claude Code 执行开发任务（代码生成、重构、测试）",
        keywords=["开发", "代码", "claude", "编程", "重构", "fix"],
        agent="dev", category="development", timeout=360,
        is_concurrent_safe=False,
    ),
    ToolIndex(
        name="pyagent_deploy",
        description="部署前端应用到生产环境",
        keywords=["部署", "deploy", "发布", "上线"],
        agent="dev", category="development", timeout=300,
        is_concurrent_safe=False,
    ),
    ToolIndex(
        name="pyagent_news",
        description="获取财经新闻和市场资讯",
        keywords=["新闻", "news", "资讯", "财经", "消息"],
        agent="news", category="information", timeout=120,
        is_concurrent_safe=True,
    ),
    ToolIndex(
        name="pyagent_research",
        description="深度研究：多源搜索+分析+总结",
        keywords=["研究", "research", "深度", "调研", "分析"],
        agent="news", category="information", timeout=300,
        is_concurrent_safe=False,
    ),
    ToolIndex(
        name="pyagent_monitor_alerts",
        description="查看监控告警状态，包含 Grafana 和自定义告警",
        keywords=["告警", "alert", "监控", "monitor", "巡检"],
        agent="monitor", category="ops", timeout=120,
        is_concurrent_safe=True,
    ),
    ToolIndex(
        name="hermes_execute_code",
        description="PTC: 在沙箱中执行 Python 代码，支持多步骤自动化",
        keywords=["执行", "代码", "python", "脚本", "PTC", "编程"],
        agent="hermes", category="execution", timeout=120,
        is_concurrent_safe=False,
    ),
    ToolIndex(
        name="hermes_web_search",
        description="Hermes 网页搜索，支持多引擎（Google/Bing/DuckDuckGo）",
        keywords=["搜索", "search", "网页", "google", "bing"],
        agent="hermes", category="information", timeout=60,
        is_concurrent_safe=True,
    ),
    ToolIndex(
        name="hermes_skill_manage",
        description="创建/编辑/删除可复用技能（SKILL.md 格式）",
        keywords=["技能", "skill", "创建", "管理", "模板"],
        agent="hermes", category="evolution", timeout=30,
        is_concurrent_safe=False,
    ),
    # ... 完整列表从 command_registry (107) + hermes tools (47) 自动生成
    # 总计约 120+ 条，每条约 25 tokens → 索引总量 ~3K tokens
]
```

### 12.4 tool_search 工具实现

```python
# tools/search.py

class ToolSearchTool(Tool):
    """
    参考 claude-code ToolSearch.ts:
    - LLM 调用 tool_search(query="回测 策略") → 返回匹配工具的完整 schema
    - 匹配算法: 关键词交集 + 描述子串 + 模糊拼音匹配
    - 返回 top-5 匹配，包含完整 JSON Schema（LLM 可直接调用）
    - 一次 search → schema 被缓存到本次会话 → 后续调用无需再 search
    """

    name = "tool_search"
    description = "搜索可用工具。输入关键词，返回匹配工具的完整参数说明。"
    is_tier0 = True
    is_concurrent_safe = True

    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（中文或英文），如 '回测'、'backtest'、'涨停板'"
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "description": "最大返回数量"
            }
        },
        "required": ["query"]
    }

    async def call(self, input: dict) -> ToolResult:
        query = input["query"]
        max_results = input.get("max_results", 5)

        # 三层匹配
        scored: list[tuple[float, ToolIndex]] = []
        for tool_idx in self.registry.tier1_index:
            score = self._match_score(query, tool_idx)
            if score > 0:
                scored.append((score, tool_idx))

        # 排序 + 取 top-N
        scored.sort(key=lambda x: -x[0])
        matches = [idx for _, idx in scored[:max_results]]

        if not matches:
            return ToolResult(
                content=f"没有找到与 '{query}' 匹配的工具。\n"
                        f"可用分类: quant, market, development, information, ops, execution"
            )

        # 加载匹配工具的完整 schema 并缓存到会话
        results = []
        for idx in matches:
            full_schema = self.registry.load_full_schema(idx.name)
            self._session_cache[idx.name] = full_schema  # 缓存，后续可直接调用
            results.append(self._format_tool_info(idx, full_schema))

        return ToolResult(content="\n\n---\n\n".join(results))

    def _match_score(self, query: str, idx: ToolIndex) -> float:
        """
        三层匹配算法:
        1. 精确关键词匹配 (权重 3.0)
        2. 描述子串匹配 (权重 1.5)
        3. 分类匹配 (权重 1.0)
        """
        score = 0.0
        query_terms = query.lower().split()

        for term in query_terms:
            # Layer 1: 关键词精确匹配
            if term in [k.lower() for k in idx.keywords]:
                score += 3.0

            # Layer 2: 描述子串
            if term in idx.description.lower():
                score += 1.5

            # Layer 3: 分类匹配
            if term in idx.category.lower() or term in idx.agent.lower():
                score += 1.0

        return score

    def _format_tool_info(self, idx: ToolIndex, schema: dict) -> str:
        """格式化为 LLM 可读的工具说明"""
        return (
            f"**{idx.name}**\n"
            f"  描述: {idx.description}\n"
            f"  分类: {idx.category} | Agent: {idx.agent}\n"
            f"  超时: {idx.timeout}s | 并发安全: {idx.is_concurrent_safe}\n"
            f"  参数:\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```"
        )
```

### 12.5 索引自动生成

```python
# tools/index_builder.py

class ToolIndexBuilder:
    """
    启动时从三个来源自动生成 Tier 1 索引:

    1. PyAgent command_registry (107 命令)
       → 通过 Redis 查询 agent:commands 获取最新命令列表
       → 自动生成 ToolIndex (command→name, description, aliases→keywords)

    2. Hermes tools (47 工具)
       → 从 Hermes registry.list_tools() 获取
       → 自动生成 ToolIndex

    3. MCP 外部工具 (动态)
       → 连接 MCP server → tools/list → 生成 ToolIndex
       → 这些进入 Tier 2（不注入索引，仅 tool_search 可发现）

    索引缓存到 ~/.rrclaw/tool_index.json，每次启动重建。
    """

    async def build(self) -> list[ToolIndex]:
        indices = []

        # 1. PyAgent 命令
        pyagent_cmds = await self.redis.hgetall("agent:commands")
        for cmd_name, cmd_json in pyagent_cmds.items():
            cmd = json.loads(cmd_json)
            indices.append(ToolIndex(
                name=f"pyagent_{cmd_name}",
                description=cmd["description"],
                keywords=self._extract_keywords(cmd),
                agent=cmd["agent"],
                category=self._infer_category(cmd["agent"]),
                timeout=cmd.get("timeout", 30),
                is_concurrent_safe=cmd.get("timeout", 30) < 60,
            ))

        # 2. Hermes 工具
        hermes_tools = self.hermes_runtime.list_tools()
        for tool in hermes_tools:
            indices.append(ToolIndex(
                name=f"hermes_{tool.name}",
                description=tool.description,
                keywords=self._extract_keywords_from_description(tool.description),
                agent="hermes",
                category=tool.category or "general",
                timeout=tool.timeout or 60,
                is_concurrent_safe=tool.is_read_only,
            ))

        # 缓存
        self._save_cache(indices)
        return indices
```

### 12.6 System Prompt 中的工具注入

```python
# runtime/prompt.py — 工具注入策略

class PromptBuilder:
    """
    参考 claude-code query.ts 构建 system prompt 的方式。

    System prompt 结构:
    ┌─────────────────────────────────────┐
    │ SOUL.md (身份 + 行为准则)            │  ~1K tokens
    ├─────────────────────────────────────┤
    │ Tier 0 工具 schema (8个)            │  ~2K tokens
    ├─────────────────────────────────────┤
    │ Tier 1 索引 (名称+描述, ~120个)      │  ~3K tokens
    ├─────────────────────────────────────┤
    │ Session 上下文 (memory, user prefs)  │  ~1K tokens
    ├─────────────────────────────────────┤
    │ Active Skills (当前激活的技能)        │  ~0.5K tokens
    └─────────────────────────────────────┘
    总计: ~7.5K tokens（远低于当前全量注入的 ~50K tokens）

    当 LLM 调用 tool_search 后，被发现工具的 schema
    追加到该会话的 tools 参数中（不重复注入 system prompt）。
    """

    def build_system_prompt(self, session: Session) -> str:
        parts = []

        # 身份
        parts.append(self._load_soul())

        # Tier 1 索引（仅名称+描述，不含 schema）
        parts.append("## 可用工具索引\n")
        parts.append("使用 `tool_search` 搜索以下工具获取完整参数说明:\n")
        for idx in self.tool_index:
            parts.append(f"- **{idx.name}**: {idx.description}")

        # Session 上下文
        if session.user_preferences:
            parts.append(f"\n## 用户偏好\n{session.user_preferences}")

        # 激活的 Skills
        active_skills = self.skill_manager.get_active(session)
        if active_skills:
            parts.append("\n## 可用技能\n")
            for skill in active_skills:
                parts.append(f"- /{skill.name}: {skill.description}")

        return "\n\n".join(parts)

    def get_tool_schemas(self, session: Session) -> list[dict]:
        """
        返回当前可调用的工具 schema:
        - Tier 0: 始终包含
        - Session 缓存: tool_search 发现过的工具
        """
        schemas = [t.schema for t in self.tier0_tools]

        # 追加本会话通过 tool_search 发现的工具
        for name, schema in session.discovered_tool_schemas.items():
            schemas.append(schema)

        return schemas
```

---

## 十三、渐进式实施路径（P0 → P5）

从现有 6 文件 bridge 到完整 30+ 模块 harness 的分阶段实施。每个 Phase 独立交付，完成后系统即可运行。

### Phase 概览

```
P0 ─── 核心循环 ──→ P1 ─── 上下文工程 ──→ P2 ─── 容错体系 ──→
  (替代 bridge)      (解决 token 浪费)    (生产稳定)

P3 ─── 自学习 ──→ P4 ─── 系统进化 ──→ P5 ─── 深度集成
  (差异化能力)      (自主优化)          (完整体验)
```

### P0: 核心循环（最小可运行 harness）

**目标**: 替代现有 bridge，RRCLAW 控制 LLM 循环。

**交付模块**:
```
rrclaw/
├── __init__.py
├── __main__.py                  # python -m rrclaw
├── runtime/
│   ├── conversation.py          # ConversationRuntime (async generator)
│   ├── session.py               # JSONL 持久化
│   └── config.py                # YAML 配置加载
├── tools/
│   ├── base.py                  # Tool 基类 + ToolResult
│   ├── registry.py              # GlobalToolRegistry
│   ├── executor.py              # 工具执行（错误即结果）
│   └── pyagent/
│       └── bridge.py            # 通过 Redis 调用 PyAgent 命令
├── channels/
│   └── gateway.py               # OpenClaw Gateway WS 连接
└── deploy/
    └── (保留现有 Dockerfile/systemd/launchd)
```

**关键验证**:
- Telegram "今天涨停板" → RRCLAW ConversationRuntime → Redis → PyAgent → 返回
- LLM 循环在 RRCLAW 中运行（不是 Gateway Pi runtime）
- 工具错误不崩溃（返回给 LLM 自修正）
- JSONL 会话持久化 + 崩溃恢复

**复用现有代码**:
- `bridge/redis_broker.py` → `tools/pyagent/bridge.py`（重构）
- `bridge/gateway_client.py` → `channels/gateway.py`（重构）
- `bridge/protocol.py` → 保留消息格式

**不做**:
- 不做上下文压缩（先用简单截断）
- 不做 ToolSearch（先全量注入 Tier 0 + 少量常用工具）
- 不做容错（先用基础 try/except）
- 不做自学习

**Provider**: 直接用 Anthropic SDK（单 provider）

---

### P1: 上下文工程（解决 token 浪费 + 长对话）

**目标**: 从 ~50K token 全量注入降到 ~7.5K，支持长对话不 OOM。

**依赖**: P0 完成

**新增模块**:
```
rrclaw/
├── tools/
│   ├── search.py                # ToolSearch 惰性加载
│   └── index_builder.py         # 从 PyAgent+Hermes 自动生成索引
├── context/
│   ├── engine.py                # 5层压缩总调度
│   ├── tool_result_budget.py    # L1: 大结果持久化
│   ├── history_snip.py          # L2: 旧对话截断
│   ├── microcompact.py          # L3: 规则折叠
│   ├── context_collapse.py      # L4: 摘要归档
│   └── autocompact.py           # L5: LLM 总结
└── runtime/
    └── prompt.py                # System prompt 构建（Tier 0 + 索引注入）
```

**关键验证**:
- `tool_search("回测")` → 返回 pyagent_backtest 完整 schema → LLM 可调用
- 50 轮对话后不 OOM（5 层压缩生效）
- System prompt token 从 ~50K 降到 ~7.5K

---

### P2: 容错体系（生产稳定性）

**目标**: 7 层容错全部就位，系统可在组件故障时降级运行。

**依赖**: P1 完成

**新增模块**:
```
rrclaw/
├── runtime/
│   ├── resilience/
│   │   ├── api_retry.py         # L1: 指数退避 + 模型降级
│   │   ├── circuit_breaker.py   # L4: 通用断路器
│   │   ├── recovery_recipes.py  # L6: 7 种故障恢复
│   │   ├── health_monitor.py    # L7: 组件健康 + 降级路由
│   │   ├── overflow_recovery.py # L3: 上下文溢出恢复
│   │   └── error_classifier.py  # Hermes 错误分类
│   └── providers/
│       ├── base.py              # Provider 基类
│       ├── anthropic.py         # Anthropic provider
│       ├── dashscope.py         # 阿里通义 provider
│       ├── openai_compat.py     # OpenAI 兼容 (Ollama)
│       ├── router.py            # 前缀路由 + fallback chain
│       └── credential_pool.py   # 凭证轮转 (Hermes 4策略)
└── permissions/
    ├── policy.py                # safe/aware/consent/critical
    └── enforcer.py              # 工作空间边界
```

**关键验证**:
- Kill Redis → 降级到本地 → Redis 恢复后重连
- Anthropic 529 × 3 → 自动切换 DashScope
- autocompact 失败 3 次 → 断路器跳闸
- 权限: shell 命令需 CONSENT 确认

---

### P3: 自学习（差异化核心能力）

**目标**: Loop 1-3 闭环运行，系统能从错误中学习。

**依赖**: P2 完成

**新增模块**:
```
rrclaw/
├── tools/
│   └── hermes/
│       └── runtime.py           # HermesNativeRuntime（线程池运行）
├── evolution/
│   ├── background_review.py     # Loop 2: 会话内反思
│   ├── engine.py                # Loop 3: 跨会话 Evolution Engine
│   ├── pattern_detector.py      # 工具链模式检测
│   ├── failure_detector.py      # 失败模式检测
│   ├── correction_tracker.py    # 纠错记录 + 模式提取
│   ├── skill_creator.py         # 自动 Skill 生成
│   └── skill_guard.py           # Skill 安全扫描
├── skills/
│   ├── loader.py                # YAML+Markdown Skill 加载
│   ├── executor.py              # Skill 执行引擎
│   └── sync.py                  # SkillBridge 双向同步
└── context/
    └── memory/
        ├── tier1_session.py     # 会话内记忆
        ├── tier2_user.py        # 用户级记忆 (USER.md)
        └── tier3_system.py      # 系统级记忆 (MEMORY.md)
```

**关键验证**:
- 10 轮对话后 → Background Review daemon 创建 Memory/Skill
- 同一工具链重复 3 次 → Evolution Engine 检测 → 自动创建 Skill
- 工具失败 → 纠错模式被提取 → 下次预防

---

### P4: 系统进化（自主优化）

**目标**: GEPA 管线 + Autoresearch 循环，系统能自我优化。

**依赖**: P3 完成

**新增模块**:
```
rrclaw/
├── evolution/
│   ├── gepa_pipeline.py         # Loop 4: GEPA 日级进化
│   ├── perf_detector.py         # 性能退化检测
│   ├── recovery.py              # 自动 Recovery Recipe 生成
│   ├── autoresearch_loop.py     # 策略优化实验循环
│   └── prompt_research_loop.py  # Prompt 优化实验循环
├── workers/
│   ├── boot.py                  # Worker Boot 状态机
│   ├── coordinator.py           # Coordinator Mode
│   └── task_packet.py           # TaskPacket + 验收测试
└── commands/
    ├── research.py              # /research 命令（启动实验循环）
    └── evolve.py                # /evolve 命令（手动触发进化）
```

**关键验证**:
- 策略 autoresearch 循环 → 夏普比逐步提升
- GEPA 日级进化 → 工具选择准确率提升 > 5%
- Worker Boot → 多 agent 协调运行

---

### P5: 深度集成（完整体验）

**目标**: OpenClaw ACP 接管、Canvas 可视化、MCP Server 对外暴露。

**依赖**: P4 完成

**新增模块**:
```
rrclaw/
├── channels/
│   ├── acp_runtime.py           # ACP 外部 runtime（接管 LLM 循环）
│   └── webhook.py               # Hook 集成
├── tools/
│   ├── builtin/
│   │   └── canvas.py            # Canvas 可视化
│   └── mcp/
│       ├── server.py            # RRCLAW 作为 MCP Server
│       ├── client.py            # 连接外部 MCP Server
│       └── reachrich_server.py  # ReachRich 行情 MCP Server
└── runtime/
    └── hooks.py                 # PreToolUse / PostToolUse hooks
```

**关键验证**:
- OpenClaw ACP 模式: Gateway → RRCLAW ConversationRuntime（完全接管）
- Canvas 渲染: 涨停板热力图在 WebChat 中显示
- MCP Server: Claude Desktop 可调用 RRCLAW 工具
- ReachRich MCP: 快速行情查询通过标准 MCP 协议

---

### 实施时间线与依赖图

```
            P0 (核心循环)
                │
                ▼
            P1 (上下文工程)
                │
                ▼
            P2 (容错体系)
               ╱ ╲
              ▼   ▼
    P3 (自学习)   P5 (深度集成)  ← P3 和 P5 可并行
              ╲
               ▼
            P4 (系统进化)
```

**P3 和 P5 可以并行**：P3 专注自学习（Hermes 集成、Evolution Engine），P5 专注外部集成（ACP、Canvas、MCP Server），两者无代码依赖。

### 每个 Phase 的退出标准

| Phase | 退出标准 | 回滚方案 |
|-------|---------|---------|
| P0 | Telegram 端到端成功，JSONL 持久化通过 | 切回现有 bridge |
| P1 | token 使用降 > 80%，50 轮不 OOM | 关闭 ToolSearch，回到全量注入 |
| P2 | Redis/Provider 故障降级测试通过 | 关闭 resilience，回到基础 try/except |
| P3 | Background Review 生成有效 Skill | 关闭 evolution，手动管理 Skill |
| P4 | GEPA A/B 测试有正向改进 | 关闭自动进化，保留手动触发 |
| P5 | ACP + Canvas + MCP 端到端通过 | 保持 MCP 模式，ACP 降级回 WS |
