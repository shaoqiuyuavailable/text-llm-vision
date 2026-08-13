import subprocess, sys, os

# 档位定义：0=off 1=fast 2=standard 3=deep
# on/off 向后兼容：on→1(fast), off→0
STATE = os.path.expanduser("~/.claude/vision-eyes/state")
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


def main():
    val = parse(sys.argv[1] if len(sys.argv) > 1 else "1")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    open(STATE, "w").write(str(val))
    print(f"vision {NAMES[val]} ({val})")
    if val == 0:
        _unload_model()
        print("已主动卸载视觉模型，释放显存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
