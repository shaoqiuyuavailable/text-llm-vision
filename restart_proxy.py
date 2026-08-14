#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重启视觉代理：自杀旧进程 → start_proxy.py 重启 → 验证 /health → 确保 ANTHROPIC_BASE_URL。

用项目脚本机制（start_proxy.py 起 detached uvicorn），不手动拼 uvicorn。
最后一步改/确认 settings.json 的 ANTHROPIC_BASE_URL 指向本机代理（有变动才备份+改）。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import config_loader  # F4：端口单一事实来源（config.json port，默认 8787）

HOME = os.path.expanduser("~")
DIR = os.path.dirname(os.path.abspath(__file__))
PORT = config_loader.get_port()  # 与代理同源，改端口后 restart 仍找对 PID / 验活 / 写 BASE_URL
SETTINGS = os.path.join(HOME, ".claude", "settings.json")


def port_open(port, timeout=1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def health_ok(port, timeout=2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200 and b'"ok"' in r.read()
    except Exception:
        return False


def pid_on_port(port):
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, encoding="mbcs", errors="replace",
                             timeout=10).stdout
        for line in out.splitlines():
            m = re.search(r":%d\s+\S+\s+LISTENING\s+(\d+)" % port, line)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def ensure_base_url() -> None:
    """最后一步：确保 ANTHROPIC_BASE_URL 指向本机代理（有变动才备份+改）。"""
    s = {}
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                s = json.load(f)
        except (ValueError, OSError):
            s = {}
    cur = (s.get("env") or {}).get("ANTHROPIC_BASE_URL", "")
    want = f"http://localhost:{PORT}"
    if cur == want:
        print(f"[restart] ANTHROPIC_BASE_URL 已指向 {want}（无需改）")
        return
    bak = os.path.join(HOME, ".claude", "vision-eyes", "state", "settings.json.bak.restart")
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    if os.path.exists(SETTINGS) and not os.path.exists(bak):
        shutil.copy2(SETTINGS, bak)
    s.setdefault("env", {})["ANTHROPIC_BASE_URL"] = want
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    print(f"[restart] ANTHROPIC_BASE_URL: {cur or '(无)'} → {want}（备份 → {bak}）")


def main() -> int:
    pid = pid_on_port(PORT)
    if pid:
        print(f"[restart] 自杀旧代理 PID={pid} on :{PORT} …")
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, text=True,
                       encoding="mbcs", errors="replace")
        for _ in range(10):
            if not port_open(PORT):
                break
            time.sleep(0.5)
        print(f"[restart] 端口 :{PORT} 已释放" if not port_open(PORT) else "[restart] 警告: 端口仍占用")
    print("[restart] 启动新代理 …")
    r = subprocess.run([sys.executable, os.path.join(DIR, "start_proxy.py")],
                       capture_output=True, text=True, timeout=40)
    print((r.stdout or r.stderr).strip())
    ok = health_ok(PORT)
    print(f"[restart] /health: {'OK' if ok else 'FAIL'}")
    # 最后一步：改/确认 ANTHROPIC_BASE_URL
    ensure_base_url()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
