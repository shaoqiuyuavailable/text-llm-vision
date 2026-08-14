#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""子进程辅助：install/toggle/mcp_hosts 共用（stdlib-only，Windows 平台坑单点维护）。

Windows 上 npm 安装的 claude 是 .cmd 批处理，list 形式直接执行报 FileNotFoundError
（WinError 2），回退经 `cmd /c` 解析。Windows 加 CREATE_NO_WINDOW：父进程无控制台
时子进程不弹 cmd 黑窗（根治「每 5 秒弹黑窗」）。
"""
import os
import subprocess


def run_cmd(cmd, timeout=30):
    """执行命令返回 (code, out)。失败不抛。"""
    kwargs = dict(capture_output=True, text=True,
                  timeout=timeout, encoding="utf-8", errors="replace")
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        p = subprocess.run(cmd, **kwargs)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        if os.name == "nt":
            try:
                p = subprocess.run(["cmd", "/c", *cmd], **kwargs)
                return p.returncode, (p.stdout or "") + (p.stderr or "")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
