这个方案一旦落地，你将彻底告别“两头跑”的割裂感，真正进入“上帝视角”的 Vibe Coding 状态。

为了确保在整合过程中 **138（前端）与 139（后端）的连接绝对不中断、不异常**，我们需要极其小心地处理**环境配置屏蔽**和**热更新机制**。

以下是手把手、零风险的完整实施路径：

### 第一阶段：安全构建 Monorepo（单体仓库）

我们要把 139 的代码“克隆”一份到 Mac 本地，但绝不能破坏 139 上现有的运行环境。

**1. 在 Mac 上创建宇宙级目录**
打开终端，执行以下命令构建骨架：

```bash
mkdir OpenClaw-Universe
cd OpenClaw-Universe
mkdir openclaw-brain      # 放 Mac 原本的控制面代码
mkdir openclaw-sandbox    # 放 139 的后端执行面代码

```

**2. 物理迁移代码**

* **Mac 端代码**：把你 Mac 上原有的 OpenClaw 代码全部移入 `openclaw-brain`。
* **139 端代码**：在 Mac 终端用 `scp` 命令（或任意 SFTP 客户端）把 139 上的代码拉到本地：
```bash
# 假设 139 上的代码在 /opt/backend/openclaw 目录下
scp -r 你的用户名@192.168.1.139:/opt/backend/openclaw/* ./openclaw-sandbox/

```


*(注：此时你本地拥有了 139 的完整代码副本，Cursor 可以全局阅读它们了)*

---

### 第二阶段：配置“不伤筋骨”的精准 SFTP 自动同步

这是确保 138 和 139 连接正常的**最核心步骤**。我们绝不能让 Mac 上的环境配置（比如局域网 IP、密码）覆盖掉 139 上的生产级配置。

**1. 安装插件**
在 Mac 的 Cursor 中，搜索并安装扩展：**`SFTP`** (作者通常是 Natizyskate 或 Liximomo 的衍生版)。

**2. 配置精准映射与忽略名单 (极其重要)**
在 `OpenClaw-Universe` 根目录下，新建 `.vscode/sftp.json`，严格按照以下配置填写：

```json
{
    "name": "139-Backend-Sync",
    "host": "192.168.1.139",
    "protocol": "sftp",
    "port": 22,
    "username": "你的139用户名",
    "password": "你的密码", 
    "remotePath": "/opt/backend/openclaw", 
    "uploadOnSave": true,
    "context": "openclaw-sandbox",
    "ignore": [
        "**/.vscode/**",
        "**/.git/**",
        "**/.DS_Store",
        "**/venv/**",
        "**/__pycache__/**",
        "**/*.pyc",
        "**/.env",           
        "**/config.yaml",    
        "**/*.sqlite3",      
        "**/*.log"
    ]
}

```

**🛡️ 保障 138 连通性的硬核防线：**

* `"context": "openclaw-sandbox"`：这告诉 SFTP 插件，**只同步这个文件夹里的东西**。Mac 大脑 (`openclaw-brain`) 的代码绝对不会被乱传到 139 上。
* `"**/.env"` 和 `"**/config.yaml"`：**这是防具里的防弹插板。** 139 服务器上的数据库密码、供 138 调用的跨域 CORS 设置、IP 绑定，都在这俩文件里。设置 ignore 后，你在 Mac 上怎么折腾测试，都不会覆盖 139 的底层网络配置，138 访问 139 的连接就稳如泰山。

---

### 第三阶段：激活 139 的“热重载” (Hot Reload)

代码传过去后，如果 139 的后端（FastAPI）不重启，新代码是不会生效的。如果每次都要手动去 139 敲 `systemctl restart`，Vibe 就全毁了。

**你需要确保 139 的 FastAPI 是以 `--reload` 模式运行的。**

SSH 登录到 139 服务器，查看你的后端启动命令。
如果是直接用 `uvicorn` 跑的，请加上 `--reload`：

```bash
# 139 上的启动命令示例
cd /opt/backend/openclaw
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001 --reload

```

**原理**：加上 `--reload` 后，139 上的 FastAPI 会像监控探头一样盯着文件夹。你在 Mac 的 Cursor 里按下 `Cmd+S`，SFTP 花 0.1 秒传到 139，`uvicorn` 瞬间检测到文件变动，花 0.5 秒优雅重启工作进程。**138 前端的请求在这个极短的间隙依然会被接收排队，用户完全无感！**

---

### 第四阶段：给 Cursor 注入“架构师灵魂” (.cursorrules)

既然代码全在一个窗口里了，你必须告诉 Cursor 你的系统宏观架构，防止它在写代码时把前端和后端的逻辑搞混。

在 `OpenClaw-Universe` 根目录下创建一个 `.cursorrules` 文件：

```markdown
# OpenClaw 宇宙架构指南 (给 Cursor AI 的全局上下文)

你现在接管的是一个宏大的量化交易系统。本项目包含两个物理隔离但逻辑联动的微服务：

## 1. openclaw-brain (运行在 Mac，系统的大脑)
- 职责：意图识别、Agent 调度、LLM Prompt 管理 (Researcher, Coder, PM)、发送 Bridge API 请求。
- 禁忌：绝对不能包含直接访问 ClickHouse 或 DolphinDB 的代码。

## 2. openclaw-sandbox (运行在 139 服务器，系统的躯干)
- 职责：FastAPI (Bridge API) 宿主、直连 ClickHouse 和 DolphinDB、提供安全的 Python 沙箱 (vectorbt)。
- 运行机制：该目录下的代码通过 SFTP 实时同步到 139 服务器。

## 协作规范 (Vibe Coding 指南)
1. 当我要求新增一个量化因子时，你必须同时做两步：
   - 步骤A：修改 `openclaw-sandbox` 中沙箱或 FastAPI 的底层数据获取逻辑。
   - 步骤B：修改 `openclaw-brain/prompts/` 里的 Schema 契约，让 LLM 知道这个新因子的存在。
2. 脑和躯干的唯一通信方式是 HTTP API 请求，确保接口字段一致。

```

### 🎉 验收你的终极开发流

1. 用 Cursor 打开 `OpenClaw-Universe` 文件夹。
2. 随便在 `openclaw-sandbox/xxx.py` 里加一行注释 `# Test Sync`，按 `Cmd+S`。
3. 注意看 Cursor 底部的状态栏，应该会闪过 `SFTP: Uploading... Done`。
4. 去 138 的前端点一下你的页面，一切正常加载，数据瞬间返回。

至此，你完成了一次华丽的工程架构升维。从此，你只需要在 Mac 前敲击键盘说出你的“意图”，代码的编写、双端的对齐、到 139 服务器的部署更新，全部在按下保存键的那一秒内由机器自动完成。