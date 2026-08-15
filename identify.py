#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""识别单张图片，三次判定：scan(描述+大类+小类) → zoom(按大类提取事实) → guess(大胆推测)。

用法：
    python identify.py <图片路径>                    # 完整识别（analyze deep，含空间结构）
    python identify.py <图片路径> --precision fast   # 只快速描述（1次调用）
    python identify.py <图片路径> --precision standard # 描述+场景+细节
    python identify.py <图片路径> --type person.anime  # 手动指定 大类.小类
    python identify.py <图片路径> --scan             # 只第1次：描述+场景判断
    python identify.py <图片路径> --zoom             # 只第2次：按场景提取事实
    python identify.py <图片路径> --guess            # 只第3次：大胆推测
    python identify.py <图片路径> --ask "自定义问题"
    python identify.py <图片路径> --mode rigorous    # 动态温度：rigorous/identity/military/anime/open（覆盖 guess 温度）
提示词与采样参数从 config.json 读取（缺失时回退 prompts.py 默认）。
"""
import sys

import config_loader
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
    mode_name = ""  # --mode 动态温度（v2）：rigorous/identity/military/anime/open
    forced_scene, forced_sub = None, None
    precision = ""  # 空=用 config 默认；CLI 默认 deep（深挖工具）
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--type":
            if i + 1 < len(args):
                forced_scene, forced_sub = _parse_forced(args[i + 1])
                i += 2
                continue
        elif a == "--mode":
            if i + 1 < len(args):
                mode_name = args[i + 1].lower()
                if mode_name not in config_loader.get().get("modes", {}):
                    print(f"[warn] 未知 --mode '{mode_name}'，已回退提示词默认温度（可用: "
                          f"{', '.join(config_loader.get().get('modes', {}))}）", file=sys.stderr)
                i += 2
                continue
        elif a == "--precision":
            if i + 1 < len(args):
                precision = args[i + 1].lower()
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

    # --scan/--zoom/--guess 需要先 scan 判断场景；full 模式不走这里（直接 analyze）
    if mode != "full":
        if forced_scene is not None:
            scene, sub = forced_scene, forced_sub
            desc = f"(手动指定: {scene}" + (f".{sub}" if sub else "") + ")"
        else:
            parsed = vision_client.scan(path)
            desc, scene, sub = parsed[0], parsed[1], parsed[2]
            extra = parsed[3] if len(parsed) > 3 else []

    if mode == "--scan":
        print("[scan]")
        print(desc)
        print(f"\n大类: {scene}")
        print(f"小类: {sub or '无'}")
        if extra:
            print(f"内容: {', '.join(extra)}")
        return 0

    if mode == "--zoom":
        print(f"[zoom_{scene}" + (f".{sub}" if sub else "") + "]")
        print(vision_client.zoom(path, scene, sub=sub, scan_desc=desc, extra=extra))
        return 0

    if mode == "--guess":
        facts = vision_client.zoom(path, scene, sub=sub, scan_desc=desc, extra=extra)
        print(f"[zoom_{scene}" + (f".{sub}" if sub else "") + " 事实]")
        print(facts)
        print("\n[guess 推测]")
        print(vision_client.guess(path, context=facts, scene=scene, sub=sub, scan_desc=desc, mode=mode_name))
        return 0

    # 默认 full：走 analyze 统一入口（含 spatial），precision 默认 deep
    if not precision:
        precision = "deep"  # CLI 是深挖工具，默认完整三次+空间结构
    result = vision_client.analyze(path, precision, mode=mode_name)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
