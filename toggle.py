import subprocess, sys, os, json
import _proc
import config_loader

# 档位定义：0=off 1=fast 2=standard 3=deep
# on/off 向后兼容：on→1(fast), off→0
STATE = os.path.expanduser("~/.claude/vision-eyes/state")
CONFIG = os.path.expanduser("~/.claude/vision-eyes/config.json")
NAMES = {0: "OFF", 1: "fast", 2: "standard", 3: "deep"}


def parse(arg: str) -> int:
    a = (arg or "1").strip().lower()
    if a in ("off", "0", "关闭"):
        return 0
    if a in ("on", "1", "fast", "快"):
        return 1
    if a in ("2", "standard", "标准"):
        return 2
    if a in ("3", "deep", "深度"):
        return 3
    return 1  # 非法输入回退 fast


def _unload_model():
    """off 时主动卸载视觉模型，立即释放显存（不等 keep_alive 超时）。"""
    try:
        model = config_loader.get().get("ollama", {}).get("model") or "qwen2.5vl"
        subprocess.run(["ollama", "stop", model], timeout=30, capture_output=True)
    except Exception:
        pass


def _read_cfg() -> dict:
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return {}


def _write_cfg(cfg: dict):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _cloud_names(cfg: dict) -> list:
    return [c.get("name", "") for c in cfg.get("cloud", {}).get("clouds", []) if c.get("name")]


def _cloud_info(cfg: dict, name: str) -> dict:
    for c in cfg.get("cloud", {}).get("clouds", []):
        if c.get("name") == name:
            return c
    return {}


def _has_key(cfg: dict, name: str) -> bool:
    """该厂商是否有 key：环境变量 <NAME>_API_KEY 或 config api_key。"""
    if os.environ.get(f"{name.upper()}_API_KEY"):
        return True
    return bool(_cloud_info(cfg, name).get("api_key"))


def _set_active(cfg: dict, name: str):
    cfg.setdefault("cloud", {})["active"] = name
    _write_cfg(cfg)


def _cmd(args, timeout=20):
    """执行命令返回 (code, out)；Windows .cmd 回退 + CREATE_NO_WINDOW（见 _proc.run_cmd）。"""
    return _proc.run_cmd(args, timeout)


def mark(ok, text):
    print(f"{'✓' if ok else '✗'}  {text}")
    return ok


def doctor() -> int:
    """逐项诊断四处配置（只读不写），输出 ✓/✗ 清单 + 每项修复命令。"""
    import urllib.request
    port = _read_cfg().get("port", 8787)
    home = os.path.expanduser("~/.claude")
    print(f"== text-llm-vision 体检（端口 :{port}）==")

    # 1. Ollama 服务 + 模型
    code, out = _cmd(["ollama", "list"])
    ok = code == 0
    print(f"{'✓' if ok else '✗'}  Ollama 服务" + ("" if not ok else f"（{out.strip().splitlines()[1].split()[0] if len(out.splitlines())>1 else '运行中'}）"))
    if not ok:
        print("   → 启动 Ollama（桌面应用或 `ollama serve`）后重试")
    model = config_loader.get().get("ollama", {}).get("model") or "qwen2.5vl"
    has_model = model in out
    print(f"{'✓' if has_model else '✗'}  视觉模型 {model}")
    if not has_model:
        print(f"   → ollama pull {model}")

    # 2. 代理健康
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    print(f"{'✓' if ok else '✗'}  代理 :{port} 健康")
    if not ok:
        print(f"   → 启动: python {os.path.join(home, 'vision-eyes', 'start_proxy.py')}")

    # 3. MCP 注册
    code, out = _cmd(["claude", "mcp", "list"])
    if code == 0 and "vision" in out and "mcp_server.py" in out:
        mark(True, "MCP server `vision`（mcp_server.py）")
    elif code == 0 and "vision" in out:
        mark(False, "MCP server `vision` 是旧 Node 形态（mcp-vision.js）")
        print("   → 迁移: claude mcp add --scope user vision -- python "
              f"\"{os.path.join(home, 'vision-eyes', 'mcp_server.py')}\"")
    else:
        mark(False, "MCP server `vision` 未注册")
        print("   → 修复: claude mcp add --scope user vision -- python "
              f"\"{os.path.join(home, 'vision-eyes', 'mcp_server.py')}\"")

    # 4. ANTHROPIC_BASE_URL
    cur = ""
    s = os.path.join(home, "settings.json")
    if os.path.exists(s):
        try:
            with open(s, encoding="utf-8") as f:
                cur = (json.load(f).get("env", {}) or {}).get("ANTHROPIC_BASE_URL", "")
        except (ValueError, OSError):
            pass
    ok = cur == f"http://localhost:{port}"
    print(f"{'✓' if ok else '✗'}  ANTHROPIC_BASE_URL 指向代理" + (f"（当前: {cur}）" if cur else "（未设置）"))
    if not ok:
        print("   → 最后一步，确认上面全 ✓ 后再改：")
        print("     python <部署目录>/install.py --point-proxy（自动备份）或手动改 settings.json env")

    # 5. CLAUDE.md 引导
    md = os.path.join(home, "CLAUDE.md")
    has = False
    if os.path.exists(md):
        try:
            with open(md, encoding="utf-8") as f:
                has = "describe_image" in f.read()
        except OSError:
            pass
    print(f"{'✓' if has else '✗'}  CLAUDE.md 含 describe_image 看图规范")
    if not has:
        print("   → 运行 install.py 自动追加，或手动加规范")

    # 6. /vision 命令（vision.md + permissions）
    vm = os.path.join(home, "commands", "vision.md")
    has_vm = os.path.exists(vm)
    perm_ok = False
    if os.path.exists(s):
        try:
            with open(s, encoding="utf-8") as f:
                allow = (json.load(f).get("permissions", {}) or {}).get("allow", [])
                perm_ok = any("vision-eyes*toggle.py" in str(a) for a in allow)
        except (ValueError, OSError):
            pass
    ok = has_vm and perm_ok
    print(f"{'✓' if ok else '✗'}  /vision 命令（vision.md{' ✓' if has_vm else ' ✗'} + permissions{' ✓' if perm_ok else ' ✗'}）")
    if not ok:
        print("   → 运行 install.py 自动补齐 commands/vision.md 与 permissions.allow")

    # 7. 多宿主 MCP 注册
    try:
        import mcp_hosts
        print("\n-- 多宿主 MCP 注册（install.py --mcp <host> 补全）--")
        for host, ok, detail in mcp_hosts.host_status():
            print(f"{'✓' if ok else '✗'}  {host}: {detail}")
    except ImportError:
        pass  # 旧部署无 mcp_hosts.py，忽略（doctor 其余项仍有效）

    print()
    print("全部 ✓ → 功能就绪；有 ✗ → 按提示修复后重跑 vision doctor")
    return 0


def set_backend(kind: str, arg: str = "") -> int:
    """切视觉后端（local/cloud 命令族，逻辑隔离）：
    - vision local        → active=""（本地），端口保持
    - vision local <port> → active="" + 指定端口（端口唯一入口，遗弃独立 port 子命令）
    - vision cloud        → active=当前或第一个厂商，不动端口
    - vision cloud <厂商>  → active=指定厂商
    - vision list         → 汇总状态
    隔离：local 不碰 cloud 厂商列表，cloud 不碰 port，档位不碰后端。"""
    cfg = _read_cfg()
    kind = (kind or "").strip().lower()
    names = _cloud_names(cfg)

    if kind == "local":
        _set_active(cfg, "")
        if arg:  # 可选端口参数（独立 port 子命令已并入此处）
            try:
                p = int(arg)
            except ValueError:
                print(f"invalid port: {arg}")
                return 1
            if not (1 <= p <= 65535):
                print(f"invalid port: {arg}")
                return 1
            cfg["port"] = p
            _write_cfg(cfg)
        port = cfg.get("port", 8787)
        print(f"vision backend: LOCAL (Ollama) :{port}")
        print("  本地识别，零费用、数据不出机器")
        return 0

    if kind == "cloud":
        if arg:  # 指定厂商
            if arg not in names:
                print(f"unknown provider: {arg}")
                print(f"  可用: {', '.join(names) or '(无)'}")
                return 1
            _set_active(cfg, arg)
            print(f"vision backend: CLOUD ({arg})")
            print(f"  已切换厂商 {arg}，设环境变量 {arg.upper()}_API_KEY 生效")
            return 0
        # 无参数：当前 active 或第一个厂商
        cur = cfg.get("cloud", {}).get("active", "")
        if cur and cur in names:
            print(f"vision backend: CLOUD ({cur})")
        elif names:
            _set_active(cfg, names[0])
            print(f"vision backend: CLOUD ({names[0]})")
        else:
            print("vision backend: CLOUD 但未配置任何云端平台")
            print("  编辑 config.json 的 cloud.clouds 添加厂商(base_url/model)，")
            print("  并设环境变量 <NAME>_API_KEY 或填 api_key")
            return 1
        print("  云端识别（质量更高），配了 key 的厂商：", ", ".join(names) or "(无)")
        return 0

    if kind == "list":
        st = open(STATE).read().strip() if os.path.exists(STATE) else "?"
        active = cfg.get("cloud", {}).get("active", "")
        port = cfg.get("port", 8787)
        backend = "LOCAL" if not active else f"CLOUD({active})"
        print(f"vision 档位: {st} | 后端: {backend} | 端口: {port}")
        for n in names:
            mark = "✓" if _has_key(cfg, n) else "✗"
            print(f"  {n}: key {mark} model={_cloud_info(cfg, n).get('model', '') or '(未配)'}")
        return 0

    if kind == "doctor":
        return doctor()

    if kind == "help":
        print("vision — 本地视觉控制")
        print()
        print("用法:")
        print("  vision 0|1|2|3|on|off      档位（识别精度，与后端无关）")
        print("      0=off 1=fast 2=standard 3=deep；on→1 off→0")
        print("  vision local [端口]        切本地 Ollama 后端；可选指定端口(默认保持)")
        print("  vision cloud [厂商]        切云端后端；可选指定厂商(默认当前/第一个)")
        print("  vision list                查看档位/后端/端口/各厂商 key")
        print("  vision doctor              体检四处配置（Ollama/代理/MCP/BASE_URL），逐项给修复命令")
        print("  vision help                显示本帮助")
        print()
        print("逻辑隔离：local 不碰云端厂商列表，cloud 不碰端口，档位不碰后端。")
        print("云端 key：环境变量 <厂商大写>_API_KEY 或 config.cloud.api_key。")
        return 0

    print(f"unknown subcommand: {kind}")
    print("usage: vision 0-3|local[端口]|cloud[厂商]|list|doctor|help")
    return 1


def main():
    # Windows GBK 终端无法输出 ✓/✗，强制 UTF-8（不崩；老 cmd 会乱码但不影响功能）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = sys.argv[1:]
    if not args:
        args = ["1"]  # 默认 fast（向后兼容）
    first = args[0].lower()
    if first in ("local", "云"):
        return set_backend("local", args[1] if len(args) > 1 else "")
    if first in ("cloud", "云端"):
        return set_backend("cloud", args[1] if len(args) > 1 else "")
    if first in ("list", "状态"):
        return set_backend("list")
    if first in ("doctor", "体检"):
        return set_backend("doctor")
    if first in ("help", "帮助", "-h", "--help"):
        return set_backend("help")
    # 档位（不碰后端）
    val = parse(args[0])
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    open(STATE, "w").write(str(val))
    print(f"vision {NAMES[val]} ({val})")
    if val == 0:
        _unload_model()
        print("已主动卸载视觉模型，释放显存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
