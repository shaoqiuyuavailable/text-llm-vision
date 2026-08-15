#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-vision 全量测试入口：依次运行测试套件。

  python scripts/test_all.py          # 全部（Python 3 套件 + Node 冒烟）
  python scripts/test_all.py cli      # 只跑 CLI 分发
  python scripts/test_all.py client   # 只跑引擎逻辑
  python scripts/test_all.py config   # 只跑配置逻辑
  python scripts/test_all.py node     # 只跑 Node 冒烟（smoke-apply.mjs）
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = {
    "cli": ["scripts", "test_cli.py"],
    "client": ["scripts", "test_vision_client.py"],
    "config": ["scripts", "test_config_loader.py"],
}
NODE_SMOKE = ["scripts", "smoke-apply.mjs"]


def run_node_smoke() -> int:
    """Node 侧冒烟：在 dsh workspace 里加载插件并执行 apply，验证 5 工具注册。"""
    smoke = ROOT.joinpath(*NODE_SMOKE)
    print(f"\n=== node: {smoke.relative_to(ROOT)} ===")
    node = shutil.which("node")
    if node is None:
        print("⚠️  node 不在 PATH，跳过 Node 冒烟")
        return 0
    # smoke-apply.mjs 需要解析 @deepseek-ai/* 包：在 dsh 仓库（或插件目录上级含 node_modules 处）运行。
    # 插件目录上级是 dsh 仓库时可直接用；否则尝试 D:\deepseek-harness（常见源码布局）。
    candidates = [str(ROOT), r"D:\deepseek-harness"]
    for cwd in candidates:
        if (Path(cwd) / "node_modules").exists():
            r = subprocess.run([node, "--import", "tsx/esm", str(smoke)], cwd=cwd)
            return r.returncode
    print("⚠️  未找到含 node_modules 的工作目录，跳过 Node 冒烟")
    return 0


def main():
    import os
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    args = sys.argv[1:]
    if not args:
        targets = list(SUITES) + ["node"]
    else:
        targets = [a for a in args if a in SUITES or a == "node"]
        if not targets:
            print(f"未知套件: {args}；可选: {', '.join(SUITES)} node")
            return 1
    total_fail = 0
    for name in targets:
        if name == "node":
            r = run_node_smoke()
        else:
            path = ROOT.joinpath(*SUITES[name])
            print(f"\n=== {name}: {path.relative_to(ROOT)} ===")
            r = subprocess.run([sys.executable, str(path)])
        if r.returncode != 0:
            total_fail += 1
    print("\n" + ("✅ 全部套件通过" if total_fail == 0 else f"❌ {total_fail} 个套件失败"))
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
