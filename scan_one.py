#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对单张图片跑第1次 scan（描述+场景+混合内容类型判断），输出干净 JSON 供脚本/代理使用。

用法：python scan_one.py <图片路径>
输出：{"path":..., "main":..., "sub":..., "extra":[...], "desc":...}
"""
import json
import sys

import vision_client


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "no path"}, ensure_ascii=False))
        return 1
    path = sys.argv[1]
    try:
        parsed = vision_client.scan(path)
        desc, main, sub = parsed[0], parsed[1], parsed[2]
        focus = parsed[3] if len(parsed) > 3 else []
        print(json.dumps({"path": path, "main": main, "sub": sub, "focus": focus,
                          "desc": desc}, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"path": path, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
