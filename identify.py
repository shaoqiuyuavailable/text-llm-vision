#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""识别单张图片，三次判定：scan(描述+大类+小类) → zoom(按大类提取事实) → guess(大胆推测)。

用法：
    python identify.py <图片路径>                    # 三段式全流程（scan 自动判场景）
    python identify.py <图片路径> --type person.anime  # 手动指定 大类.小类
    python identify.py <图片路径> --type document      # 只指定大类（小类留空）
    python identify.py <图片路径> --scan             # 只第1次：描述+场景判断
    python identify.py <图片路径> --zoom             # 只第2次：按场景提取事实
    python identify.py <图片路径> --guess            # 只第3次：大胆推测
    python identify.py <图片路径> --ask "自定义问题"
提示词与采样参数从 config.json 读取（缺失时回退 prompts.py 默认）。
"""
import sys

import vision_client


def _parse_forced(arg: str):
    """解析 --type 值：'person' 或 'person.anime' → (scene, sub)。"""
    if "." in arg:
        scene, sub = arg.split(".", 1)
        return scene.lower(), sub.lower()
    return arg.lower(), ""


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]

    mode = "full"
    forced_scene, forced_sub = None, None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--type":
            if i + 1 < len(args):
                forced_scene, forced_sub = _parse_forced(args[i + 1])
                i += 2
                continue
        elif a in ("--scan", "--zoom", "--guess"):
            mode = a
        elif a == "--ask":
            mode = "--ask"
            q = " ".join(args[i + 1:])
            print(vision_client.describe(path, q))
            return 0
        i += 1

    # 需要场景：手动指定就用指定的，否则先 scan 判断
    if forced_scene is not None:
        scene, sub = forced_scene, forced_sub
        desc = f"(手动指定: {scene}" + (f".{sub}" if sub else "") + ")"
    else:
        desc, scene, sub = vision_client.scan(path)

    if mode == "--scan":
        print("[scan]")
        print(desc)
        print(f"\n大类: {scene}")
        print(f"小类: {sub or '无'}")
        return 0

    if mode == "--zoom":
        print(f"[zoom_{scene}" + (f".{sub}" if sub else "") + "]")
        print(vision_client.zoom(path, scene, sub=sub, scan_desc=desc))
        return 0

    if mode == "--guess":
        facts = vision_client.zoom(path, scene, sub=sub, scan_desc=desc)
        print(f"[zoom_{scene}" + (f".{sub}" if sub else "") + " 事实]")
        print(facts)
        print("\n[guess 推测]")
        print(vision_client.guess(path, context=facts, scene=scene, sub=sub, scan_desc=desc))
        return 0

    # 默认 full：三段式，上下文逐层传递
    print("=== 第1次 scan（描述 + 大类 + 小类）===")
    print(desc)
    print(f"\n大类: {scene}")
    print(f"小类: {sub or '无'}")

    print("\n=== 第2次 zoom（按大类提取事实，注入 scan 上下文）===")
    facts = vision_client.zoom(path, scene, sub=sub, scan_desc=desc)
    print(facts)

    print("\n=== 第3次 guess（大胆推测，注入 scan+zoom 上下文）===")
    print(vision_client.guess(path, context=facts, scene=scene, sub=sub, scan_desc=desc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
