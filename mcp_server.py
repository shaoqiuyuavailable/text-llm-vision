#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""text-llm-vision MCP server（Python，零第三方依赖，stdlib only）。

标准 Tool Use 主路径：给纯文本模型看图。模型主动调用工具识图，
识别引擎直接 import vision_client（Scan/Zoom/Guess 流水线），不依赖代理，
独立存活，可挂任何支持 MCP 的宿主。

注册：宿主以 stdio spawn 本文件。各宿主配置文件由 install.py --mcp 写入。
运行：python mcp_server.py
"""
import json
import os
import sys

import vision_client
import config_loader

VERSION = "2.0.0"

# 触发规则文本（vision_rules 工具返回；与 mcp_hosts 写入各宿主的规则同源）
RULES_TEXT = """你的模型没有视觉能力。出现以下情况必须调用相应工具：
- 用户引用本地图片路径 / 粘贴截图 / 你看到 [Unsupported Image] → describe_image(图片路径)
- 终端红字、报错栈、文档扫描 → extract_text(图片路径)
- 图中某元素在哪里 → locate_object(图片路径, 元素名)
- 前后两张图对比 → compare_images(图A路径, 图B路径)"""


def _err(msg):
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def _image_arg(args, *names):
    for n in names:
        v = args.get(n) or ""
        if v:
            return v
    return ""


def tool_describe(args):
    path = _image_arg(args, "image", "path")
    if not path:
        return _err("缺少 image 参数（图片路径）")
    if not os.path.isfile(path):
        return _err(f"图片不存在: {path}")
    prompt = (args.get("prompt") or "").strip()
    if prompt:
        text = vision_client.describe(path, prompt=prompt)
    else:
        text = vision_client.analyze(path)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def tool_extract_text(args):
    path = _image_arg(args, "image", "path")
    if not path:
        return _err("缺少 image 参数（图片路径）")
    if not os.path.isfile(path):
        return _err(f"图片不存在: {path}")
    text = vision_client.ocr(path)
    if not text:
        text = vision_client.describe(path, prompt="只提取图片中的全部文字，原文照抄，不要解释、不要评论。")
    text = text.strip()
    if not text:
        text = "未提取到文字（OCR 与视觉模型均无结果）"
    return {"content": [{"type": "text", "text": text}], "isError": False}


def tool_locate(args):
    path = _image_arg(args, "image", "path")
    query = (args.get("query") or "").strip()
    if not path:
        return _err("缺少 image 参数（图片路径）")
    if not os.path.isfile(path):
        return _err(f"图片不存在: {path}")
    if not query:
        return _err("缺少 query 参数（要定位的元素）")
    return {"content": [{"type": "text", "text": vision_client.locate(path, query)}], "isError": False}


def tool_compare(args):
    a = _image_arg(args, "image_a", "path_a")
    b = _image_arg(args, "image_b", "path_b")
    if not a or not b:
        return _err("缺少 image_a / image_b 参数（两张图片路径）")
    if not os.path.isfile(a):
        return _err(f"图A不存在: {a}")
    if not os.path.isfile(b):
        return _err(f"图B不存在: {b}")
    return {"content": [{"type": "text", "text": vision_client.compare(a, b)}], "isError": False}


def tool_rules(args):
    return {"content": [{"type": "text", "text": RULES_TEXT}], "isError": False}


TOOLS = [
    {"name": "describe_image",
     "description": "识别本地图片，用本地/云端视觉模型返回图片内容的文字描述（Scan→Zoom→Guess 三阶段）。当需要查看图片内容、分析截图、识别图片中的文字/物体/场景时使用。传入图片文件的本地绝对路径。",
     "inputSchema": {"type": "object", "properties": {
         "image": {"type": "string", "description": "图片文件本地绝对路径（如 D:/xxx/photo.jpg）"},
         "prompt": {"type": "string", "description": "可选的识图指令，指定要关注的内容"}},
         "required": ["image"]}},
    {"name": "extract_text",
     "description": "提取图片中的全部文字（OCR 优先，回退视觉模型）。用于截图报错、终端红字、文档扫描等纯文字场景。",
     "inputSchema": {"type": "object", "properties": {
         "image": {"type": "string", "description": "图片文件本地绝对路径"}},
         "required": ["image"]}},
    {"name": "locate_object",
     "description": "在图中定位指定元素，返回元素名与边界框坐标（grounding bbox JSON）。用于『图中某元素在哪』『点击哪个按钮』等需要坐标的场景。",
     "inputSchema": {"type": "object", "properties": {
         "image": {"type": "string", "description": "图片文件本地绝对路径"},
         "query": {"type": "string", "description": "要定位的元素（如『提交按钮』『错误提示框』）"}},
         "required": ["image", "query"]}},
    {"name": "compare_images",
     "description": "对比两张图片（各自识别后逐点对比差异）。用于 UI 前后对比、图找不同。",
     "inputSchema": {"type": "object", "properties": {
         "image_a": {"type": "string", "description": "图A本地绝对路径"},
         "image_b": {"type": "string", "description": "图B本地绝对路径"}},
         "required": ["image_a", "image_b"]}},
    {"name": "vision_rules",
     "description": "返回『何时该调用识图工具』的规则文本。当宿主规则文件缺失、不确定何时该调用识图工具时调用本工具，把返回文本写入你的规则文件（CLAUDE.md/AGENTS.md 等）。",
     "inputSchema": {"type": "object", "properties": {}}},
]

HANDLERS = {
    "describe_image": tool_describe,
    "extract_text": tool_extract_text,
    "locate_object": tool_locate,
    "compare_images": tool_compare,
    "vision_rules": tool_rules,
}


# ---- MCP JSON-RPC（stdio，newline-delimited）----

def _send(out, obj):
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def _handle(msg, out):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        _send(out, {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "vision-mcp", "version": VERSION}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        _send(out, {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            _send(out, {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32602, "message": f"unknown tool: {name}"}})
        else:
            try:
                _send(out, {"jsonrpc": "2.0", "id": mid, "result": handler(args)})
            except Exception as e:
                _send(out, {"jsonrpc": "2.0", "id": mid, "result": _err(f"识别失败: {e}")})
    elif method == "ping":
        _send(out, {"jsonrpc": "2.0", "id": mid, "result": {}})
    else:
        if mid is not None:
            _send(out, {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": f"method not found: {method}"}})


def serve(instream, outstream):
    """stdio 主循环：逐行读 JSON-RPC 请求并回写响应。EOF 时自然退出。"""
    buf = ""
    for chunk in instream:
        buf += chunk
        while "\n" in buf:
            raw, buf = buf.split("\n", 1)
            raw = raw.strip()
            if not raw:
                continue
            try:
                _handle(json.loads(raw), outstream)
            except Exception:
                pass


def main():
    # stdout 是 MCP 协议通道，必须 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    # 启动日志写 stderr（stdout 是协议通道，不能污染）
    try:
        backend = config_loader.resolve_backend()
        sys.stderr.write(f"[vision-mcp] backend={backend['provider']} model={backend['model']}\n")
    except Exception:
        pass
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
