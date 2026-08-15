#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-vision 挂载验证脚本（静态检查，自包含版）。

不启动 dsh，只检查插件文件、识别库、配置是否存在，并给出挂载命令。
识别引擎随插件 python/ 自带，不再依赖外部 visual-ds 目录。

用法：
  python scripts/verify_mount.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_DIR = ROOT / "python"

ok = True


def check(cond: bool, name: str, hint: str = ""):
    global ok
    print(("✅" if cond else "❌") + f"  {name}" + (f"  ({hint})" if hint and not cond else ""))
    if not cond:
        ok = False


def main():
    print("== dsh-vision 挂载验证 ==")
    check((ROOT / "cordis.patch.yml").exists(), "cordis.patch.yml", "缺少插件补丁")
    check((ROOT / "cordis.yml").exists(), "cordis.yml（兼容别名）", "可忽略")
    check((ROOT / "src" / "index.ts").exists(), "src/index.ts", "缺少 dsh 插件源码")
    check((PY_DIR / "vision_cli.py").exists(), "python/vision_cli.py", "缺少 Python CLI")
    check((PY_DIR / "config.json").exists(), "python/config.json", "缺少视觉配置")

    # 自包含识别库：与插件同目录，不依赖外部 visual-ds / ~/.claude/vision-eyes
    for core in ("vision_client.py", "config_loader.py", "prompts.py"):
        check((PY_DIR / core).exists(), f"python/{core}", "识别库缺失（需从 visual-ds 基线迁移）")
    sys.path.insert(0, str(PY_DIR))
    try:
        import vision_client  # noqa: F401
        check(True, "vision_client 可导入（自包含）")
    except Exception as e:
        check(False, "vision_client 可导入（自包含）", str(e))

    config_candidates = [
        Path.home() / ".dsh" / "vision" / "config.json",
        PY_DIR / "config.json",
    ]
    cfg = next((p for p in config_candidates if p.exists()), None)
    check(cfg is not None, "配置文件存在", "GUI 设置会写入 ~/.dsh/vision/config.json")

    print()
    if ok:
        print("✅ 静态检查通过。挂载命令：")
        print('  pnpm dsh plugin --profile web add <插件目录>')
        print('  （或已挂载时直接重启 dsh）')
        print()
        print("GUI 配置：设置 → 插件 → dsh-vision 卡片")
        print("回归测试：python scripts/test_all.py")
    else:
        print("❌ 有未通过项，请按提示修复后重试。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
