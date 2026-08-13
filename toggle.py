import subprocess, sys, os, json

# 档位定义：0=off 1=fast 2=standard 3=deep
# on/off 向后兼容：on→1(fast), off→0
STATE = os.path.expanduser("~/.claude/vision-eyes/state")
CONFIG = os.path.expanduser("~/.claude/vision-eyes/config.json")
NAMES = {0: "OFF", 1: "fast", 2: "standard", 3: "deep"}
# off 时主动卸载的模型（从 config 读或默认 qwen2.5vl）
VISION_MODEL = "qwen2.5vl"


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
    """off 时主动卸载视觉模型，立即释放显存（不等 keep_alive 超时）。
    模型未驻留时 ollama stop 无害；失败不阻塞（容错）。"""
    try:
        subprocess.run(["ollama", "stop", VISION_MODEL],
                       timeout=30, capture_output=True)
    except Exception:
        pass  # ollama 未装/未跑/已卸载 → 忽略


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

    print(f"unknown subcommand: {kind}")
    print("usage: vision 0|1|2|3|on|off | local [port] | cloud [厂商] | list")
    return 1


def main():
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
