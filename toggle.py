import sys, os

STATE = os.path.expanduser("~/.claude/vision-eyes/state")
val = sys.argv[1] if len(sys.argv) > 1 else "on"
val = "on" if val in ("on", "1", "true", "开") else "off"
os.makedirs(os.path.dirname(STATE), exist_ok=True)
open(STATE, "w").write(val)
print(f"vision {'ON' if val == 'on' else 'OFF'}")
