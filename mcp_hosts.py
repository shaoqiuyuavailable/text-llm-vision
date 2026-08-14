#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多宿主 MCP 注册 + 触发规则（install.py --mcp 与 toggle.py doctor 共用）。

设计要点：
- 零第三方依赖（TOML 手写追加；JSON 用 json 库 merge，不覆盖宿主既有配置）。
- 幂等：已注册/已含规则时不再重复写。
- 触发规则文本与 mcp_server.py 的 RULES_TEXT 同源（视觉能力规范母版）。
"""
import json
import os
import _proc
import sys

SERVER_PATH = os.path.join(os.path.expanduser("~/.claude/vision-eyes"), "mcp_server.py")
HOSTS = ["claude", "codex", "opencode", "cline", "continue", "copilot", "cursor"]

RULES_TEXT = """# 视觉能力（text-llm-vision）
你的模型没有视觉能力。出现以下情况必须调用相应工具：
- 用户引用本地图片路径 / 粘贴截图 / 你看到 [Unsupported Image] → describe_image(图片路径)
- 终端红字、报错栈、文档扫描 → extract_text(图片路径)
- 图中某元素在哪里 → locate_object(图片路径, 元素名)
- 前后两张图对比 → compare_images(图A路径, 图B路径)
"""


def _python_cmd() -> str:
    return sys.executable


def _server_args() -> list:
    return [SERVER_PATH]


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _cmd(args, timeout=20):
    return _proc.run_cmd(args, timeout)


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, (dict, list)) else {}
    except (ValueError, OSError):
        return {}


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _entry_stdio() -> dict:
    return {"command": _python_cmd(), "args": _server_args()}


def _append_if_missing(path, text, marker) -> bool:
    """文件不存在或未含 marker 时追加 text；返回是否真的写入。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if marker in f.read():
                    return False
        except OSError:
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + text if os.path.exists(path) and text.startswith("#") else text)
    return True


# ---- 路径发现 ----

def _home() -> str:
    return os.path.expanduser("~")


def codex_path() -> str:
    return os.path.join(_home(), ".codex", "config.toml")


def opencode_path() -> str:
    return os.path.join(_home(), ".config", "opencode", "opencode.json")


def cline_paths() -> list:
    ap = os.environ.get("APPDATA", "")
    vs_code = (os.path.join(ap, "Code", "User", "globalStorage",
                            "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json")
               if ap else "")
    cli = os.path.join(_home(), ".cline", "data", "settings", "cline_mcp_settings.json")
    return [p for p in (vs_code, cli) if p]


def continue_path() -> str:
    return os.path.join(_home(), ".continue", "config.json")


def copilot_path() -> str:
    return os.path.join(os.getcwd(), ".vscode", "mcp.json")


def cursor_path() -> str:
    return os.path.join(_home(), ".cursor", "mcp.json")


# ---- 写 MCP 配置（各宿主格式差异见 spec §7） ----

def write_codex(path: str) -> bool:
    """~/.codex/config.toml 追加 [mcp_servers.vision]（TOML 手写）。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if "[mcp_servers.vision]" in f.read():
                    return False
        except OSError:
            pass
    block = (f'\n[mcp_servers.vision]\ncommand = "{_python_cmd()}"\n'
             f'args = ["{_norm(_server_args()[0])}"]\n')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    return True


def write_opencode(path: str):
    """opencode.json 用 `mcp` 键 + 数组 command + `environment`（非 mcpServers 格式）。"""
    data = _load_json(path)
    data.setdefault("mcp", {})["vision"] = {
        "type": "local",
        "command": [_python_cmd(), _server_args()[0]],
        "enabled": True,
    }
    _save_json(path, data)


def write_cline(path: str):
    data = _load_json(path)
    data.setdefault("mcpServers", {})["vision"] = {
        **_entry_stdio(), "disabled": False, "autoApprove": [],
    }
    _save_json(path, data)


def write_continue(path: str):
    data = _load_json(path)
    servers = data.setdefault("mcpServers", [])
    if isinstance(servers, dict):  # 兼容对象形态 → 转数组
        servers = data["mcpServers"] = []
    if not any(isinstance(s, dict) and s.get("name") == "vision" for s in servers):
        servers.append({"name": "vision", **_entry_stdio()})
    _save_json(path, data)


def write_copilot(path: str):
    """.vscode/mcp.json（VS Code Copilot 用 `servers` 键 + type:stdio）。"""
    data = _load_json(path)
    data.setdefault("servers", {})["vision"] = {"type": "stdio", **_entry_stdio()}
    _save_json(path, data)


def write_cursor(path: str):
    """.cursor/mcp.json（与 Claude Code 同构：mcpServers + command/args）。"""
    data = _load_json(path)
    data.setdefault("mcpServers", {})["vision"] = _entry_stdio()
    _save_json(path, data)


# ---- 触发规则 ----

def write_agents_md(path: str) -> bool:
    return _append_if_missing(path, RULES_TEXT, "describe_image")


def write_clinerules(path: str) -> bool:
    return _append_if_missing(path, RULES_TEXT, "describe_image")


def write_copilot_rules(path: str) -> bool:
    return _append_if_missing(path, RULES_TEXT, "describe_image")


def write_continue_rules(path: str):
    data = _load_json(path)
    rules = data.setdefault("rules", [])
    if not any("describe_image" in r for r in rules):
        rules.append(RULES_TEXT)
    _save_json(path, data)


# ---- 注册入口 ----

def register_host(host: str) -> list:
    """注册一个宿主：写 MCP 配置 + 写触发规则。返回描述行列表。"""
    msgs = []
    if host == "claude":
        code, _ = _cmd(["claude", "mcp", "add", "--scope", "user", "vision",
                        "--", _python_cmd(), _server_args()[0]])
        msgs.append(f"claude: {'✓' if code == 0 else '✗'} claude mcp add")
        _append_if_missing(os.path.join(_home(), "CLAUDE.md"), RULES_TEXT, "describe_image")
    elif host == "codex":
        msgs.append(f"codex: {'✓' if write_codex(codex_path()) else '已注册'} config.toml")
        msgs.append(f"codex: {'✓' if write_agents_md(os.path.join(os.getcwd(), 'AGENTS.md')) else '已含规则'} AGENTS.md")
    elif host == "opencode":
        write_opencode(opencode_path())
        msgs.append("opencode: ✓ opencode.json")
        msgs.append(f"opencode: {'✓' if write_agents_md(os.path.join(os.getcwd(), 'AGENTS.md')) else '已含规则'} AGENTS.md")
    elif host == "cline":
        wrote = False
        for p in cline_paths():
            write_cline(p)
            wrote = True
        msgs.append(f"cline: {'✓' if wrote else '✗ 未找到配置路径'} cline_mcp_settings.json")
        msgs.append(f"cline: {'✓' if write_clinerules(os.path.join(os.getcwd(), '.clinerules')) else '已含规则'} .clinerules")
    elif host == "continue":
        write_continue(continue_path())
        write_continue_rules(continue_path())
        msgs.append("continue: ✓ config.json")
    elif host == "copilot":
        write_copilot(copilot_path())
        msgs.append("copilot: ✓ .vscode/mcp.json")
        msgs.append(f"copilot: {'✓' if write_copilot_rules(os.path.join(os.getcwd(), '.github', 'copilot-instructions.md')) else '已含规则'} copilot-instructions.md")
    elif host == "cursor":
        write_cursor(cursor_path())
        msgs.append("cursor: ✓ .cursor/mcp.json")
        msgs.append(f"cursor: {'✓' if write_agents_md(os.path.join(os.getcwd(), 'AGENTS.md')) else '已含规则'} AGENTS.md")
    else:
        msgs.append(f"未知宿主: {host}（可用: {', '.join(HOSTS + ['all'])})")
    return msgs


def register_all() -> list:
    msgs = []
    for h in HOSTS:
        msgs.extend(register_host(h))
    return msgs


# ---- 只读状态（doctor 用） ----

def file_has_vision(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return "mcp_server.py" in f.read()
    except OSError:
        return False


def host_status() -> list:
    """[(宿主, 是否就绪, 详情)]，只读检查。"""
    rows = []
    code, out = _cmd(["claude", "mcp", "list"])
    rows.append(("claude", code == 0 and "vision" in out, "claude mcp list"))
    rows.append(("codex", file_has_vision(codex_path()), codex_path()))
    rows.append(("opencode", file_has_vision(opencode_path()), opencode_path()))
    cline_rows = [p for p in cline_paths() if file_has_vision(p)]
    rows.append(("cline", bool(cline_rows), "; ".join(cline_paths()) or "(未找到路径)"))
    rows.append(("continue", file_has_vision(continue_path()), continue_path()))
    rows.append(("copilot", file_has_vision(copilot_path()), copilot_path()))
    rows.append(("cursor", file_has_vision(cursor_path()), cursor_path()))
    return rows
