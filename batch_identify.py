#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量识别目录下所有图片，逐张用本地视觉模型生成一句话描述，结果写入 UTF-8 文件。

用法：python batch_identify.py <目录> [输出文件]
提示词与采样参数从 config.json 读取（缺失时回退 prompts.py 默认）。
"""
import base64, glob, os, sys, json, httpx

import config_loader
import vision_client


def identify(path: str) -> str:
    return vision_client.scan(path)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    d = sys.argv[1]
    outfile = sys.argv[2] if len(sys.argv) > 2 else "batch_result.txt"
    exts = ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.bmp")
    files = []
    for e in exts:
        files.extend(glob.glob(os.path.join(d, e)))
    files = sorted(set(files))
    results = []
    for i, fp in enumerate(files, 1):
        name = os.path.basename(fp)
        try:
            desc = identify(fp)
            print(f"[{i}/{len(files)}] {name}: {desc}", flush=True)
            results.append(f"{name}: {desc}")
        except Exception as ex:
            print(f"[{i}/{len(files)}] {name}: ERROR {ex}", flush=True)
            results.append(f"{name}: ERROR {ex}")
    with open(outfile, "w", encoding="utf-8") as f:
        f.write("\n".join(results))
    print(f"\nDONE {len(results)} files -> {outfile}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
