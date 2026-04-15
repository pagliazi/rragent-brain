"""
WebUI 启动器 — 绕过 macOS 系统代理
Surge 通过 macOS System Preferences 设置系统级代理，
Python 的 urllib/httpx 通过 macOS API 读取到代理配置，
导致 Gradio 对 localhost 的健康检查被 Surge 拦截返回 503。
此脚本在 import 之前清除代理。
"""

import os
import urllib.request

os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("all_proxy", None)
os.environ.pop("ALL_PROXY", None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

urllib.request.getproxies = lambda: {}

import sys
sys.argv = ["webchat_app.py"]

exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webchat_app.py")).read())
