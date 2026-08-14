#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""text-llm-vision 一键部署：把 README 的 9 步压缩成 1 步。

用法（幂等，重复运行安全，已配置项跳过）：
  python install.py                 检测 + 自动配置安全项；不能自动的（下载/改 BASE_URL）给出命令
  python install.py --auto          额外自动安装 pip 依赖 + ollama pull 模型（需下载，可能慢）
  python install.py --check         只体检不执行（输出 ✓/✗ 清单，等价诊断）
  python install.py --point-proxy   设置 ANTHROPIC_BASE_URL 指向代理（自动备份，改前备份到 state/）
  python install.py --rollback      恢复 ANTHROPIC_BASE_URL 到备份值

步骤映射到 README「环境与部署」：
  1  Ollama + 模型     5  指向代理(BASE_URL，默认只提示不动)
  2  Python 依赖       6  注册 MCP
  3  部署代码          7  CLAUDE.md 引导
  4  启动代理          8  视觉档位(commands/vision.md + permissions)
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request

import config_loader
import _proc

TARGET = os.path.expanduser("~/.claude/vision-eyes")   # 部署目标目录
SETTINGS = os.path.expanduser("~/.claude/settings.json")
CLAUDE_MD = os.path.expanduser("~/.claude/CLAUDE.md")
COMMANDS_DIR = os.path.expanduser("~/.claude/commands")
VISION_MD = os.path.join(COMMANDS_DIR, "vision.md")
STATE_DIR = os.path.expanduser("~/.claude/vision-eyes/state")
BAK = os.path.join(STATE_DIR, "settings.json.bak.vision")  # BASE_URL 改动前备份
SRC = os.path.dirname(os.path.abspath(__file__))           # 本文件所在目录（项目源）
PORT = config_loader.get_port()
PIP_DEPS = ["fastapi", "uvicorn", "httpx"]

# 部署需要的文件清单（拷贝时排除 __pycache__/.git/日志/state/用户 config）
NEEDED_FILES = [
    "proxy.py", "config_loader.py", "control_api.py", "prompts.py", "vision_client.py",
    "mcp-vision.js", "mcp_server.py", "mcp_hosts.py",
    "identify.py", "batch_identify.py", "collect_images.py",
    "scan_one.py", "read_port.py", "toggle.py", "start_proxy.py",
    "start-proxy.bat", "status.bat", "requirements.txt", "install.py", "_proc.py", "README.md",
]

CLAUDE_MD_GUIDE = """\n# 视觉能力使用规范\n\n看图时必须走本地视觉工具，**不能直接用 Read 读图片**（会返回 `[Unsupported Image]`）。\n\n## 看图规范（强制）\n\n1. **需要查看图片内容时，调用 MCP 工具 `describe_image`**（传图片绝对路径），它会用本地视觉模型识别后返回文字描述。\n2. **绝对不要用 Read 工具读图片文件**——Read 读图片只会得到 `[Unsupported Image]`，是**已知的失败路径**。如果尝试 Read 图片后拿到 `[Unsupported Image]`，立刻改用 `describe_image`。\n3. **判断图片路径的标准**：文件扩展名是 `.jpg/.jpeg/.png/.webp/.gif/.bmp` 的就是图片，必须走 `describe_image`；只有非图片文本文件才用 Read。\n4. 如果用户粘贴了图片，代理已自动把图片转成文字描述进入上下文，无需额外处理。\n"""

VISION_MD_CONTENT = """---
description: 视觉控制：档位 0-3 + 后端 local[端口]/cloud[厂商]
argument-hint: 0-3|local|cloud|help
---
请执行视觉控制命令，参数为 $ARGUMENTS。子命令：
- `0|1|2|3|on|off`：档位（识别精度，与后端无关）
- `local [端口]`：切本地 Ollama 后端（可选指定端口，无参数保持当前端口）
- `cloud [厂商]`：切云端后端（可选指定厂商，无参数用当前/第一个）
- `list`：查看当前档位/后端/端口/厂商
- `help`：显示完整帮助

运行：`python "{target}/toggle.py" $ARGUMENTS`
"""

# ---------- 工具 ----------

def run(cmd, timeout=30):
    """执行命令，返回 (returncode, stdout)。失败不抛。委托 _proc.run_cmd。"""
    return _proc.run_cmd(cmd, timeout)


def read_settings():
    """读 settings.json，缺失返回 {}；损坏时备份原文件后返回 {}。"""
    if not os.path.exists(SETTINGS):
        return {}
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        shutil.copy2(SETTINGS, SETTINGS + ".corrupt.vision")
        return {}


def write_settings(s):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def health_ok(port, timeout=2.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200 and b'"ok"' in r.read()
    except Exception:
        return False


def mark(ok, text):
    print(f"{'✓' if ok else '✗'}  {text}")
    return ok


# ---------- 检测类 ----------

def check_python(auto=False) -> bool:
    ok = sys.version_info >= (3, 10)
    mark(ok, f"Python ≥3.10 ({sys.version.split()[0]})")
    if not ok:
        print("   → 安装 Python 3.10+，重新运行 install.py")
        return False
    missing = [m for m in PIP_DEPS if not _importable(m)]
    if not missing:
        mark(True, "Python 依赖 fastapi/uvicorn/httpx")
        return True
    mark(False, f"Python 依赖缺失: {', '.join(missing)}")
    if auto:
        print("   → 自动安装中…")
        code, out = run([sys.executable, "-m", "pip", "install", *missing], timeout=180)
        mark(code == 0, "pip install " + " ".join(missing))
        if code == 0:
            return True
        print("   → 安装失败，手动执行: pip install " + " ".join(missing))
        return False
    print("   → 修复: python -m pip install " + " ".join(missing) + "（或 install.py --auto）")
    return False


def _importable(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def check_node() -> bool:
    code, out = run(["node", "--version"])
    ok = code == 0
    mark(ok, f"Node.js ≥18 ({out.strip() if ok else '未安装'})")
    if not ok:
        print("   → 安装 Node.js ≥18（MCP server 运行环境）")
    return ok


def check_ollama(auto=False) -> bool:
    code, out = run(["ollama", "--version"])
    ok = code == 0
    mark(ok, f"Ollama ({out.strip() if ok else '未安装'})")
    if not ok:
        print("   → 安装: winget install Ollama.Ollama，然后重开终端")
        return False
    model = config_loader.get().get("ollama", {}).get("model") or "qwen2.5vl"
    # 运行中？ollama list 能连上服务才算
    code, out = run(["ollama", "list"])
    ok_run = code == 0
    mark(ok_run, "Ollama 服务运行中")
    if not ok_run:
        print("   → 启动 Ollama（桌面应用或 `ollama serve`），再重跑")
        return False
    # 模型
    has_model = model in out
    mark(has_model, f"视觉模型 {model}（ollama list）")
    if not has_model:
        if auto:
            print(f"   → 拉取中（约 6GB，耗时看网速）…")
            code, o = run(["ollama", "pull", model], timeout=600)
            mark(code == 0, f"ollama pull {model}")
            return code == 0
        print(f"   → 修复: ollama pull {model}（或 install.py --auto）")
        return False
    return True


# ---------- 配置类 ----------

def deploy_code(check_only=False) -> bool:
    """源目录 → 部署目录拷贝（config.json 已有则保留用户配置）。

    check_only=True 时只报告缺失文件，不执行拷贝（体检语义）。"""
    if os.path.normpath(SRC) == os.path.normpath(TARGET):
        mark(True, f"代码已在部署目录 {TARGET}")
        return True
    if check_only:
        missing = [f for f in NEEDED_FILES if not os.path.exists(os.path.join(TARGET, f))]
        if missing:
            mark(False, f"部署目录缺文件: {', '.join(missing[:5])}…（运行 install.py 补齐）")
            return False
        mark(True, f"部署目录代码齐全（{len(NEEDED_FILES)} 个文件）")
        return True
    os.makedirs(TARGET, exist_ok=True)
    copied = 0
    for f in NEEDED_FILES:
        src = os.path.join(SRC, f)
        if not os.path.exists(src):
            continue
        dst = os.path.join(TARGET, f)
        # config.json 只在目标缺失时复制（不覆盖用户配置）
        if f == "config.json" and os.path.exists(dst):
            continue
        shutil.copy2(src, dst)
        copied += 1
    mark(True, f"部署代码 → {TARGET}（{copied} 个文件）")
    return True


def ensure_config() -> bool:
    dst = os.path.join(TARGET, "config.json")
    if os.path.exists(dst):
        mark(True, f"config.json 存在（保留现有配置）")
        return True
    src = os.path.join(SRC, "config.json")
    if os.path.exists(src):
        shutil.copy2(src, dst)
        mark(True, "config.json 已复制（默认配置）")
        return True
    mark(False, "config.json 缺失且源目录也没有 — 从 README 手动创建")
    return False


def mcp_registered() -> bool:
    code, out = run(["claude", "mcp", "list"], timeout=20)
    return code == 0 and "vision" in out


def ensure_mcp() -> bool:
    if mcp_registered():
        mark(True, "MCP server `vision` 已注册")
        return True
    server = os.path.join(TARGET, "mcp_server.py")
    cmd = ["claude", "mcp", "add", "--scope", "user", "vision", "--", sys.executable, server]
    code, out = run(cmd, timeout=30)
    ok = code == 0 and mcp_registered()
    mark(ok, "注册 MCP server `vision`（mcp_server.py）")
    if not ok:
        print(f"   → 修复: claude mcp add --scope user vision -- {sys.executable} \"{server}\"")
    return ok


def register_mcp(host_arg: str) -> int:
    """--mcp 入口：注册到指定宿主或 all。"""
    import mcp_hosts
    mcp_hosts.SERVER_PATH = os.path.join(TARGET, "mcp_server.py")
    if host_arg == "all":
        rows = mcp_hosts.register_all()
    else:
        rows = mcp_hosts.register_host(host_arg)
    for r in rows:
        print(r)
    return 0


def ensure_hook() -> bool:
    s = read_settings()
    hooks = s.get("hooks", {})
    start = hooks.get("SessionStart") or []
    has = any(
        (h.get("hooks") or [{}])[0].get("type") == "command" and
        "start-proxy.bat" in (h.get("hooks") or [{}])[0].get("command", "")
        for h in start if isinstance(h, dict)
    )
    if has:
        mark(True, "SessionStart hook 已配置（自动拉起代理）")
        return True
    bat = os.path.join(TARGET, "start-proxy.bat")
    s.setdefault("hooks", {}).setdefault("SessionStart", []).append(
        {"hooks": [{"type": "command", "command": f'cmd /c "{bat}"'}]})
    write_settings(s)
    mark(True, "SessionStart hook 已写入（Claude Code 启动时自动拉起代理）")
    return True


def ensure_claude_md() -> bool:
    if os.path.exists(CLAUDE_MD):
        try:
            with open(CLAUDE_MD, encoding="utf-8") as f:
                if "describe_image" in f.read():
                    mark(True, "CLAUDE.md 已含 describe_image 看图规范")
                    return True
        except OSError:
            pass
    with open(CLAUDE_MD, "a", encoding="utf-8") as f:
        f.write(CLAUDE_MD_GUIDE)
    mark(True, "CLAUDE.md 看图规范已追加")
    return True


def ensure_command() -> bool:
    if os.path.exists(VISION_MD):
        mark(True, "commands/vision.md 已存在（/vision 命令）")
        return True
    os.makedirs(COMMANDS_DIR, exist_ok=True)
    with open(VISION_MD, "w", encoding="utf-8") as f:
        f.write(VISION_MD_CONTENT.format(target=TARGET))
    mark(True, "commands/vision.md 已创建（/vision 命令）")
    return True


def ensure_permission() -> bool:
    s = read_settings()
    allow = s.setdefault("permissions", {}).setdefault("allow", [])
    rule = "Bash(python *vision-eyes*toggle.py *)"
    if rule in allow:
        mark(True, "permissions.allow 已含 toggle.py 调用权限")
        return True
    allow.append(rule)
    write_settings(s)
    mark(True, "permissions.allow 已加 toggle.py 调用权限")
    return True


def start_proxy() -> bool:
    if health_ok(PORT):
        mark(True, f"代理已在运行 :{PORT}（健康）")
        return True
    code, out = run([sys.executable, os.path.join(TARGET, "start_proxy.py")], timeout=30)
    ok = health_ok(PORT)
    mark(ok, f"启动代理 :{PORT}")
    if not ok:
        print(f"   → 手动启动: cd {TARGET} && python -m uvicorn proxy:app --port {PORT}")
    return ok


# ---------- BASE_URL（断连风险，默认只提示不动） ----------

def check_base_url() -> bool:
    s = read_settings()
    cur = (s.get("env", {}) or {}).get("ANTHROPIC_BASE_URL", "")
    want = f"http://localhost:{PORT}"
    ok = cur == want
    mark(ok, f"ANTHROPIC_BASE_URL 指向代理（当前: {cur or '未设置'}）")
    if not ok:
        print(f"   → 这是最后一步（有断连风险），请确认前面步骤全 ✓ 后再改")
        print(f"   → 方式: install.py --point-proxy（自动备份+改）或手动改 settings.json 的 env")
    return ok


def point_proxy() -> int:
    """设置 BASE_URL 指向代理。改前文件级备份到 state/。"""
    if health_ok(PORT):
        print(f"警告: 代理 :{PORT} 未在运行，指向后请求会断连。先启动代理再执行。")
    os.makedirs(os.path.dirname(BAK), exist_ok=True)
    if os.path.exists(SETTINGS) and not os.path.exists(BAK):
        shutil.copy2(SETTINGS, BAK)
        print(f"备份 settings.json → {BAK}")
    s = read_settings()
    old = (s.get("env", {}) or {}).get("ANTHROPIC_BASE_URL", "(无)")
    s.setdefault("env", {})["ANTHROPIC_BASE_URL"] = f"http://localhost:{PORT}"
    write_settings(s)
    print(f"✓ ANTHROPIC_BASE_URL: {old} → http://localhost:{PORT}")
    print("  回退: python install.py --rollback")
    print("  生效: 重启 Claude Code（会话启动时加载 env）")
    return 0


def rollback() -> int:
    if not os.path.exists(BAK):
        print("✗ 无备份文件，无法回退（从未执行过 --point-proxy）")
        return 1
    shutil.copy2(BAK, SETTINGS)
    print(f"✓ 已从 {BAK} 恢复 settings.json")
    print("  重启 Claude Code 生效")
    return 0


# ---------- 主流程 ----------

def main() -> int:
    # Windows GBK 终端无法输出 ✓/✗（U+2713/2717），强制 UTF-8 输出（不崩；老 cmd 会乱码但不影响功能）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description="text-llm-vision 一键部署")
    ap.add_argument("--check", action="store_true", help="只体检不执行")
    ap.add_argument("--auto", action="store_true", help="自动装 pip 依赖 + ollama pull 模型")
    ap.add_argument("--point-proxy", action="store_true", help="设置 ANTHROPIC_BASE_URL 指向代理（备份）")
    ap.add_argument("--rollback", action="store_true", help="恢复 ANTHROPIC_BASE_URL")
    ap.add_argument("--mcp", metavar="HOST", default="",
                    help="注册 MCP 到宿主: claude|codex|opencode|cline|continue|copilot|cursor|all")
    args = ap.parse_args()

    print(f"== text-llm-vision 部署器（端口 :{PORT}）==")
    if args.mcp:
        return register_mcp(args.mcp)
    if args.point_proxy:
        return point_proxy()
    if args.rollback:
        return rollback()

    # 检测类（必跑）
    check_python(args.auto)
    check_node()
    check_ollama(args.auto)

    # 配置类（--check 只检测不执行）
    if args.check:
        deploy_code(check_only=True)
        ensure_config()
        ensure_mcp()
        ensure_hook()
        ensure_claude_md()
        ensure_command()
        ensure_permission()
        start_proxy()
        check_base_url()
        print("\n== 体检完成 ==")
        print("有 ✗ 项的按提示修复后重跑；全部 ✓ 后执行 install.py --point-proxy 走最后一步。")
        return 0

    deploy_code()
    ensure_config()
    ensure_mcp()
    ensure_hook()
    ensure_claude_md()
    ensure_command()
    ensure_permission()
    start_proxy()

    # 最后一步：默认只提示
    check_base_url()
    print("\n== 部署完成 ==")
    print("最后一步（未自动执行）：设置 ANTHROPIC_BASE_URL 指向代理")
    print("  执行 install.py --point-proxy，或手动改 settings.json 的 env。")
    print("  改后重启 Claude Code。验证：粘贴图片应自动转文字。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
