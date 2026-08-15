#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-vision 全量测试入口：依次运行三个测试套件。

  python scripts/test_all.py          # 全部
  python scripts/test_all.py cli      # 只跑 CLI 分发
  python scripts/test_all.py client   # 只跑引擎逻辑
  python scripts/test_all.py config   # 只跑配置逻辑
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = {
    "cli": ["scripts", "test_cli.py"],
    "client": ["scripts", "test_vision_client.py"],
    "config": ["scripts", "test_config_loader.py"],
}


def main():
    import os
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    args = sys.argv[1:]
    if not args:
        targets = list(SUITES)
    else:
        targets = [a for a in args if a in SUITES]
        if not targets:
            print(f"未知套件: {args}；可选: {', '.join(SUITES)}")
            return 1
    total_fail = 0
    for name in targets:
        path = ROOT.joinpath(*SUITES[name])
        print(f"\n=== {name}: {path.relative_to(ROOT)} ===")
        r = subprocess.run([sys.executable, str(path)])
        if r.returncode != 0:
            total_fail += 1
    print("\n" + ("✅ 全部套件通过" if total_fail == 0 else f"❌ {total_fail} 个套件失败"))
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
