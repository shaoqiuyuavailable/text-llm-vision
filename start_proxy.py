#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""启动/验活视觉代理（start-proxy.bat 的薄壳背后的实际逻辑，全部放 Python）。

职责：
1. 读端口（config.json 的 port，默认 8787）
2. 若已在运行（/health 返回 ok）→ 提示并退出
3. 否则预检依赖 → 启动 uvicorn（脱离父进程）→ 等待并验活
4. 失败给出明确提示

bat 只做 `python start_proxy.py`，避免 cmd 的引号/errorlevel 坑。
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

import config_loader

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = config_loader.get_port()
STATE = os.path.expanduser("~/.claude/vision-eyes/state")


def port_open(host, port, timeout=1.0) -> bool:
    """TCP 端口是否在监听（可靠探测，不依赖 curl/errorlevel）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def health_ok(port, timeout=2.0) -> bool:
    """/health 是否返回 ok（用 urllib，避开 bat 的 curl 坑）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200 and b'"ok"' in r.read()
    except Exception:
        return False


def main() -> int:
    # 1. 已在运行 → 提示退出
    if port_open("127.0.0.1", PORT):
        if health_ok(PORT):
            print(f"[vision] proxy already running on :{PORT} (healthy)")
        else:
            print(f"[vision] WARN: port :{PORT} in use but /health not responding - check proxy")
        return 0

    # 2. 依赖预检
    try:
        import uvicorn, fastapi, httpx  # noqa: F401
    except ImportError:
        print("[vision] FAIL: python deps (uvicorn/fastapi/httpx) not installed in this python.")
        print(f"         used python: {sys.executable}")
        print("         proxy NOT started. Install deps then re-open Claude Code.")
        return 1

    # 3. 启动 uvicorn（脱离父进程，避免随 SessionStart 退出）
    try:
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "proxy:app", "--port", str(PORT)],
            cwd=DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) or 0x00000008,
        )
    except Exception as e:
        print(f"[vision] FAIL: could not launch uvicorn: {e}")
        return 1

    # 4. 等待并验活（最多 8 秒）
    for _ in range(8):
        time.sleep(1)
        if health_ok(PORT):
            print(f"[vision] proxy started OK on :{PORT}")
            return 0
    print(f"[vision] WARN: proxy launched on :{PORT} but /health not responding - see vision-proxy.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
