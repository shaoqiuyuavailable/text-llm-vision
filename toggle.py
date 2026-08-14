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


MODEL_LOG = os.path.expanduser("~/.claude/vision-eyes/vision-model.log")


def _model_log(action: str, ok: bool, detail: str = ""):
    """模型操作日志（v1.5 兜底可诊断）：时间戳/动作/成败/详情，写 vision-model.log。"""
    try:
        import time
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(MODEL_LOG, "a", encoding="utf-8") as f:
            f.write(f"{ts} [{'OK' if ok else 'FAIL'}] {action} {detail}\n")
    except OSError:
        pass


def _do_download(name: str, mtype: str, provider: str = ""):
    """按模型类型执行下载。返回 (code, msg)。"""
    if mtype == "ollama":
        return _cmd(["ollama", "pull", name], timeout=600)
    if mtype == "pip":
        return _cmd(["pip", "install", name], timeout=300)
    if mtype == "cloud":
        return 0, "cloud 模型无需下载（key 走环境变量/配置）"
    return 1, f"unknown type {mtype}"


def model_list() -> int:
    """列出注册表模型 + 状态（ollama 已拉取?）+ 被 router 引用的场景。"""
    cfg = _read_cfg()
    # 生效注册表 = 基线（prompts.py MODELS）+ config.json 覆盖
    models = {**config_loader.get().get("models", {}), **(cfg.get("models", {}) or {})}
    router = cfg.get("router", {}) or {}
    refs = {}
    for scene, v in router.items():
        if ":" in str(v):
            m = str(v).split(":", 1)[1]
            refs.setdefault(m, []).append(scene)
    pulled = set()
    code, out = _cmd(["ollama", "list"])
    if code == 0:
        pulled = {l.split()[0] for l in out.splitlines()[1:] if l.split()}
    if not models:
        print("模型注册表为空（config.models），用 model add 添加")
    print(f"{'模型':<22}{'type':<8}{'purpose':<10}{'状态':<10}被引用场景")
    for name, m in models.items():
        st = "✓已拉取" if name in pulled else ("云端" if m.get("type") == "cloud" else "未拉取")
        ref = ",".join(refs.get(name, [])) or "-"
        print(f"{name:<22}{str(m.get('type','')):<8}{str(m.get('purpose','')):<10}{st:<10}{ref}")
    return 0


def model_add(argv) -> int:
    """model add <name> --type ollama|cloud|pip [--provider x] [--purpose y] [--download]"""
    if not argv:
        print("usage: model add <name> --type ollama|cloud|pip [--provider x] [--purpose y] [--download]")
        return 1
    name = argv[0]
    mtype, provider, purpose, download = "ollama", "", "", False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--type" and i + 1 < len(argv): mtype, i = argv[i + 1], i + 2; continue
        if a == "--provider" and i + 1 < len(argv): provider, i = argv[i + 1], i + 2; continue
        if a == "--purpose" and i + 1 < len(argv): purpose, i = argv[i + 1], i + 2; continue
        if a == "--download": download, i = True, i + 1; continue
        i += 1
    if mtype not in ("ollama", "cloud", "pip"):
        print(f"invalid --type: {mtype}（ollama|cloud|pip）")
        return 1
    if mtype == "cloud" and not provider:
        print("cloud 模型需 --provider <厂商>（config.cloud.clouds 里的名字）")
        return 1
    cfg = _read_cfg()
    models = cfg.setdefault("models", {})
    entry = {"type": mtype}
    if provider:
        entry["provider"] = provider
    if purpose:
        entry["purpose"] = purpose
    models[name] = entry
    _write_cfg(cfg)
    print(f"✓ 已注册模型 {name}（{mtype}" + (f", provider={provider}" if provider else "") + "）")
    _model_log(f"add {name}", True, f"type={mtype}" + (f" provider={provider}" if provider else ""))
    if download:
        code, msg = _do_download(name, mtype, provider)
        ok = code == 0
        _model_log(f"download {name}", ok, msg.strip()[:200])
        print("✓ 下载完成" if ok else f"✗ 下载失败（{msg.strip()[:200]}）")
        return 0 if ok else 1
    return 0


def model_download(argv) -> int:
    """model download <name>：拉取已注册模型（ollama pull / pip install / 云端=标记）。"""
    if not argv:
        print("usage: model download <name>")
        return 1
    name = argv[0]
    m = _read_cfg().get("models", {}).get(name)
    if not m:
        print(f"模型 {name} 未注册（先 model add）")
        return 1
    code, msg = _do_download(name, m.get("type"), m.get("provider", ""))
    ok = code == 0
    _model_log(f"download {name}", ok, msg.strip()[:200])
    print("✓ 下载完成" if ok else f"✗ 下载失败（{msg.strip()[:200]}）")
    return 0 if ok else 1


def model_rm(argv) -> int:
    """model rm <name> [--physical] [--yes]：逻辑删除（仅 config）或物理删除（config + ollama rm）。"""
    if not argv:
        print("usage: model rm <name> [--physical] [--yes]")
        return 1
    name = argv[0]
    physical = "--physical" in argv
    cfg = _read_cfg()
    models = cfg.get("models", {}) or {}
    if name not in models:
        print(f"模型 {name} 不在注册表（models）")
        return 1
    router = cfg.get("router", {}) or {}
    refs = [scene for scene, v in router.items()
            if ":" in str(v) and str(v).split(":", 1)[1] == name]
    if refs:
        print(f"⚠ 模型 {name} 正被场景引用：{', '.join(refs)}；删除后这些场景路由回退全局模型")
    if physical:
        if "--yes" not in argv:
            print(f"物理删除 {name} 将执行 ollama rm（不可逆，释放磁盘）。确认请加 --yes")
            return 1
        code, msg = _cmd(["ollama", "rm", name], timeout=60)
        ok = code == 0
        _model_log(f"rm --physical {name}", ok, msg.strip()[:200])
        print("✓ 已物理删除本地模型" if ok else f"✗ ollama rm 失败（{msg.strip()[:100]}）")
    del models[name]
    _write_cfg(cfg)
    _model_log(f"rm {name}", True, "physical" if physical else "logical")
    print(f"✓ 已从 config 移除模型 {name}" + ("（逻辑删除，可重新 add）" if not physical else ""))
    return 0


def model_replace(argv) -> int:
    """model replace <旧> <新>：改 router 里所有引用旧模型的场景 → 新模型。"""
    if len(argv) < 2:
        print("usage: model replace <旧模型> <新模型>")
        return 1
    old, new = argv[0], argv[1]
    cfg = _read_cfg()
    router = cfg.get("router", {}) or {}
    changed = []
    for scene, v in router.items():
        if ":" in str(v):
            eng, m = str(v).split(":", 1)
            if m == old:
                router[scene] = f"{eng}:{new}"
                changed.append(scene)
    if not changed:
        print(f"没有场景引用 {old}（router 里无 {old} 引用）")
        return 0
    _write_cfg(cfg)
    _model_log(f"replace {old}->{new}", True, "场景: " + ",".join(changed))
    print(f"✓ 已替换 {old} → {new}（场景：{', '.join(changed)}）")
    if new not in (cfg.get("models", {}) or {}):
        print(f"  ⚠ {new} 未在 models 注册表，建议 model add {new} 后下载")
    return 0


def model_cmd(argv) -> int:
    """model 子命令分发：list/add/download/rm/replace。"""
    if not argv:
        return model_list()
    cmd = argv[0].lower()
    if cmd == "list":
        return model_list()
    if cmd == "add":
        return model_add(argv[1:])
    if cmd == "download":
        return model_download(argv[1:])
    if cmd == "rm":
        return model_rm(argv[1:])
    if cmd == "replace":
        return model_replace(argv[1:])
    print(f"unknown model subcommand: {cmd}")
    print("usage: model list|add <name>|download <name>|rm <name> [--physical] [--yes]|replace <旧> <新>")
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
    if first == "model":
        return model_cmd(args[1:])
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
