import subprocess, sys, os, json

# 档位定义：0=off 1=fast 2=standard 3=deep
# on/off 向后兼容：on→1(fast), off→0
STATE = os.path.expanduser("~/.claude/vision-eyes/state")
CONFIG = os.path.expanduser("~/.claude/vision-eyes/config.json")
NAMES = {0: "OFF", 1: "fast", 2: "standard", 3: "deep"}
# off 时主动卸载的模型（从 config 读或默认 qwen2.5vl）
VISION_MODEL = "qwen2.5vl"


def set_port(port: int) -> int:
    """切换代理监听端口：写 config.json 的 port 字段。
    提示：端口变化需同步 CC Switch 里 DeepSeek 的 Base URL 到 http://localhost:<port>。"""
    port = int(port)
    if not (1 <= port <= 65535):
        print(f"invalid port: {port}")
        return 1
    cfg = {}
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        except (ValueError, OSError):
            cfg = {}
    cfg["port"] = port
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"port set to {port} (config.json)")
    print(f"REMEMBER: update CC Switch base URL -> http://localhost:{port}")
    print(f"          then restart session (SessionStart restarts proxy on new port)")
    return 0


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


def main():
    args = sys.argv[1:]
    # 子命令：port <N> 切换端口（写 config.json）
    if args and args[0].lower() in ("port", "-p"):
        if len(args) < 2:
            print("usage: vision port <1-65535>")
            return 1
        return set_port(args[1])
    val = parse(args[0] if args else "1")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    open(STATE, "w").write(str(val))
    print(f"vision {NAMES[val]} ({val})")
    if val == 0:
        _unload_model()
        print("已主动卸载视觉模型，释放显存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
