import sys, os

# 档位定义：0=off 1=fast 2=standard 3=deep
# on/off 向后兼容：on→1(fast), off→0
STATE = os.path.expanduser("~/.claude/vision-eyes/state")
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


def main():
    val = parse(sys.argv[1] if len(sys.argv) > 1 else "1")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    open(STATE, "w").write(str(val))
    print(f"vision {NAMES[val]} ({val})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
