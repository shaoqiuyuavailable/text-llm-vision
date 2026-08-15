#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-vision Python CLI.

统一入口：dsh 插件通过子进程调用本文件，所有图片理解复用 visual-ds 的
vision_client（Scan→Zoom→Guess + OCR + grounding）。

用法:
  python vision_cli.py describe <图片路径> [--precision fast|standard|deep] [--prompt 文本] [--mode 模式]
  python vision_cli.py extract <图片路径>
  python vision_cli.py locate <图片路径> <查询>
  python vision_cli.py compare <图A> <图B> [--precision fast|standard|deep]
"""
import os
import sys

# 识别库与插件同目录（迁移自 visual-ds 基线的自包含副本）：
# vision_client.py / config_loader.py / prompts.py 已随插件部署，不再依赖
# 外部 visual-ds 目录或 ~/.claude/vision-eyes 部署。
HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE_ROOTS = [
    HERE,
    os.path.expanduser("~/.dsh/vision"),
]
for root in CANDIDATE_ROOTS:
    if root and os.path.isdir(root) and root not in sys.path:
        sys.path.insert(0, root)

import config_loader  # noqa: E402
import vision_client  # noqa: E402

# 配置：优先 dsh 专属（GUI 设置写入处），其次插件自带配置。
DSH_CONFIG = os.path.expanduser("~/.dsh/vision/config.json")
LOCAL_CONFIG = os.path.join(HERE, "config.json")
if os.path.exists(DSH_CONFIG):
    config_loader.CONFIG_PATH = DSH_CONFIG
elif os.path.exists(LOCAL_CONFIG):
    config_loader.CONFIG_PATH = LOCAL_CONFIG

# 视觉档位 state：dsh 专属（GUI 档位写入处）。
DSH_STATE = os.path.expanduser("~/.dsh/vision/state")
vision_client.STATE_FILE = DSH_STATE


def _parse_known(argv, flags):
    """极简参数解析：返回 (positional, options)。options 是 dict。"""
    positional = []
    options = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in flags and i + 1 < len(argv):
            options[arg[2:].replace("-", "_")] = argv[i + 1]
            i += 2
            continue
        positional.append(arg)
        i += 1
    return positional, options


def cmd_describe(argv):
    positional, opts = _parse_known(argv, {"--precision", "--prompt", "--mode"})
    if not positional:
        print("describe: missing image path", file=sys.stderr)
        return 1
    path = positional[0]
    precision = opts.get("precision", "standard")
    prompt = opts.get("prompt", "")
    mode = opts.get("mode", "")
    if prompt:
        print(vision_client.describe(path, prompt=prompt))
    else:
        print(vision_client.analyze(path, precision, mode=mode))
    return 0


def cmd_extract(argv):
    if not argv:
        print("extract: missing image path", file=sys.stderr)
        return 1
    path = argv[0]
    text = vision_client.ocr(path)
    if not text:
        text = vision_client.describe(path, prompt="只提取图片中的全部文字，原文照抄，不要解释、不要评论。")
    text = text.strip()
    if not text:
        text = "未提取到文字（OCR 与视觉模型均无结果）"
    print(text)
    return 0


def cmd_locate(argv):
    if len(argv) < 2:
        print("locate: missing image path or query", file=sys.stderr)
        return 1
    print(vision_client.locate(argv[0], argv[1]))
    return 0


def cmd_compare(argv):
    positional, opts = _parse_known(argv, {"--precision"})
    if len(positional) < 2:
        print("compare: missing image_a or image_b", file=sys.stderr)
        return 1
    precision = opts.get("precision", "standard")
    print(vision_client.compare(positional[0], positional[1], precision))
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    command = sys.argv[1]
    argv = sys.argv[2:]
    if command == "describe":
        return cmd_describe(argv)
    if command == "extract":
        return cmd_extract(argv)
    if command == "locate":
        return cmd_locate(argv)
    if command == "compare":
        return cmd_compare(argv)
    print(f"unknown command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    sys.exit(main())
