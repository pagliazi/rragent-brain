#!/bin/bash
# OpenClaw 启动脚本
# 使用方式: cd ~/openclaw && bash start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

# 激活 venv
source "${VENV_DIR}/bin/activate"

# 代理配置
export http_proxy="http://127.0.0.1:6152"
export https_proxy="http://127.0.0.1:6152"
export all_proxy="socks5://127.0.0.1:6153"
export no_proxy="localhost,127.0.0.1,::1,*.local"

echo "Python:    $(python --version)"
echo "Venv:      ${VENV_DIR}"
echo "Proxy:     ${http_proxy}"
echo "Ollama:    http://127.0.0.1:11434"
echo ""

# 根据项目类型启动
if [ -f "main.py" ]; then
    python main.py
elif [ -f "package.json" ]; then
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    npm start
else
    echo "可用启动方式:"
    echo "  python -m browser_use"
    echo "  npm start"
fi
