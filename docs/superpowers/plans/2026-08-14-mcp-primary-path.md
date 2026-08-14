# MCP 主路径改造（方案 A）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MCP Tool Use 提为主路径——新增 Python MCP server（`mcp_server.py`）直接 import `vision_client` 复用 Scan/Zoom/Guess 流水线，5 个工具独立存活于任何 MCP 宿主；代理退为粘贴兜底；提供多宿主一键注册。

**Architecture:** `mcp_server.py`（Python 手写 MCP JSON-RPC，stdlib-only）import `vision_client`，后端经 `config_loader` 的 env>config>default 三层解析直连 Ollama/云端，完全脱离代理。`mcp_hosts.py` 承载多宿主注册（配置写入 + 触发规则），`install.py --mcp` 与 `toggle.py doctor` 共用。

**Tech Stack:** Python 3.10+（stdlib：json/subprocess/urllib；复用现有 httpx/PIL/rapidocr 运行时）。测试用 pytest。

**Spec:** `docs/superpowers/specs/2026-08-14-mcp-primary-path-design.md`

## Global Constraints

- Python ≥ 3.10（与 install.py 现有检查一致）。
- `mcp_server.py` / `mcp_hosts.py` **零第三方依赖**（stdlib only）。
- 配置优先级恒为 **env > config.json > 内置默认**；`OLLAMA_URL` 与 `OLLAMA_BASE_URL` 互为 alias。
- 新文件加到 `install.py` 的 `NEEDED_FILES`；部署目标目录 `TARGET = ~/.claude/vision-eyes`。
- 测试用 pytest，运行 `python -m pytest tests/ -v`；缺失时 `pip install pytest`。测试必须 monkeypatch `vision_client._post_b64` / 各分析函数，**不得真实请求 Ollama/云端**。
- 既有组件（proxy.py、control_api.py、mcp-vision.js、identify.py、VS Code 扩展）**不删除**，只新增/调整。
- Windows 子进程一律 `CREATE_NO_WINDOW`（`getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)`），命令失败回退 `cmd /c`——沿用 toggle.py / install.py 既有 `_cmd`/`run` 模式。
- 提交信息用 `feat:`/`fix:`/`docs:` 前缀。

---

### Task 1: config_loader env 覆盖 + resolve_backend()

**Files:**
- Modify: `config_loader.py`（`get()` 的 OLLAMA_URL 块、文件末尾新增 `resolve_backend()`）
- Test: `tests/test_config_loader.py`

**Interfaces:**
- Consumes: 现有 `_defaults()` / `get()` 结构。
- Produces: `config_loader.resolve_backend() -> dict`，返回 `{provider, active, model, url, api_key, base_url, precision}`。`get()` 新增 env 覆盖：`OLLAMA_URL`/`OLLAMA_BASE_URL`(alias)→`ollama.url`、`VISION_MODEL`→`ollama.model`、`VISION_API_KEY`(非空)→注入合成云平台 `{"name":"env",...}` 并设 `cloud.active="env"`。Task 3 的 `mcp_server.py` 靠它；Task 2 的 `vision_client` 靠 `get()` 的 env 注入自动生效。

- [ ] **Step 1: 写失败测试**

`tests/test_config_loader.py`：
```python
import config_loader


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(tmp_path / "nonexistent.json"))
    for k in ("OLLAMA_URL", "OLLAMA_BASE_URL", "VISION_MODEL", "VISION_API_KEY", "VISION_API_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_without_env(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    cfg = config_loader.get()
    assert cfg["ollama"]["model"] == "qwen2.5vl"
    assert cfg["ollama"]["url"] == "http://localhost:11434/api/generate"


def test_ollama_url_env_override(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
    assert config_loader.get()["ollama"]["url"] == "http://host.docker.internal:11434/api/generate"


def test_ollama_base_url_alias(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.5:11434/api/generate")
    assert config_loader.get()["ollama"]["url"] == "http://192.168.1.5:11434/api/generate"


def test_vision_model_env_override(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_MODEL", "llava")
    assert config_loader.get()["ollama"]["model"] == "llava"


def test_cloud_env_injects_synthetic_platform(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    monkeypatch.setenv("VISION_API_BASE_URL", "https://api.example.com/v1")
    cfg = config_loader.get()
    assert cfg["cloud"]["active"] == "env"
    assert cfg["cloud"]["clouds"][0]["api_key"] == "sk-test"


def test_resolve_backend_local(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    b = config_loader.resolve_backend()
    assert b["provider"] == "local"
    assert b["model"] == "qwen2.5vl"
    assert b["precision"] == "fast"


def test_resolve_backend_cloud_env(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    b = config_loader.resolve_backend()
    assert b["provider"] == "cloud"
    assert b["api_key"] == "sk-test"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_config_loader.py -v`
Expected: FAIL（`resolve_backend` 不存在 / env 覆盖未实现）

- [ ] **Step 3: 实现**

`config_loader.py` 把 get() 末尾的 OLLAMA_URL 块替换为完整 env 覆盖块：
```python
    # ---- env 覆盖（MCP server / Docker 直连）：env > config > 默认 ----
    env_url = os.environ.get("OLLAMA_URL", "").strip() or os.environ.get("OLLAMA_BASE_URL", "").strip()
    if env_url:
        cfg["ollama"]["url"] = env_url
    env_model = os.environ.get("VISION_MODEL", "").strip()
    if env_model:
        cfg["ollama"]["model"] = env_model
    env_key = os.environ.get("VISION_API_KEY", "").strip()
    if env_key:
        # 注入合成云平台（env 驱动），vision_client 经 cloud 通道走云端
        cfg["cloud"]["clouds"] = [{
            "name": "env",
            "base_url": os.environ.get("VISION_API_BASE_URL", "").strip() or "https://api.example.com/v1",
            "model": env_model or "qwen-vl-plus",
            "api_key": env_key,
        }]
        cfg["cloud"]["active"] = "env"
    return cfg
```

文件末尾新增：
```python
def resolve_backend() -> dict:
    """归一化后端信息（env > config > 默认），供 MCP server 调试/选后端。

    返回 {provider, active, model, url, api_key, base_url, precision}；
    provider: "local"(Ollama) 或 "cloud"（有云 key 时）。
    """
    cfg = get()
    o = cfg.get("ollama", {})
    cloud = cfg.get("cloud", {}) or {}
    active = cloud.get("active", "")
    api_key = os.environ.get("VISION_API_KEY", "").strip()
    base_url = os.environ.get("VISION_API_BASE_URL", "").strip()
    if not api_key:
        for c in cloud.get("clouds", []) or []:
            if c.get("name") == active:
                api_key = c.get("api_key", "") or ""
                base_url = c.get("base_url", "") or ""
    provider = os.environ.get("VISION_PROVIDER", "").strip() or ("cloud" if api_key else "local")
    return {
        "provider": provider,
        "active": active,
        "model": o.get("model", ""),
        "url": o.get("url", ""),
        "api_key": api_key,
        "base_url": base_url,
        "precision": o.get("precision", "fast"),
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_config_loader.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add config_loader.py tests/test_config_loader.py
git commit -m "feat(config_loader): env>config>default 三层覆盖 + resolve_backend()"
```

---

### Task 2: vision_client 新增 locate() 与 compare()

**Files:**
- Modify: `vision_client.py`（`_image_size` 之后新增两个公开函数）
- Test: `tests/test_vision_client.py`

**Interfaces:**
- Consumes: 现有 `_entry()`、`_post_b64()`、`_to_b64()`、`_image_size()`、`analyze()`、`config_loader.get()`。
- Produces:
  - `locate(path_or_b64: str, query: str, precision: str = "") -> str` —— 复用 spatial 提示词注入 query，返回 bbox JSON + 原图尺寸。
  - `compare(path_a: str, path_b: str, precision: str = "") -> str` —— 各自 `analyze()` 后以图A为锚、注入图B描述，调用视觉模型逐点对比，返回 `【图A】【图B】【对比】` 三段。Task 3 的 `locate_object`/`compare_images` 工具调它们。

- [ ] **Step 1: 写失败测试**

`tests/test_vision_client.py`：
```python
import vision_client


def test_locate_injects_query(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature):
        captured["prompt"] = prompt
        return '[{"name":"提交按钮","bbox":[100,200,300,400]}]'

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "BASE64")
    out = vision_client.locate("/tmp/x.png", "提交按钮")
    assert "提交按钮" in captured["prompt"]
    assert "bbox" in out


def test_compare_three_calls(monkeypatch):
    calls = []

    def fake_analyze(path, precision=""):
        calls.append(("analyze", path))
        return f"desc:{path}"

    def fake_post(b64, prompt, temperature):
        calls.append(("post", prompt))
        return "差异要点"

    monkeypatch.setattr(vision_client, "analyze", fake_analyze)
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: p)
    out = vision_client.compare("a.png", "b.png", "fast")
    assert calls[0] == ("analyze", "a.png")
    assert calls[1] == ("analyze", "b.png")
    assert calls[2][0] == "post"
    assert "【图A】" in out and "【图B】" in out and "【对比】" in out
    assert "desc:b.png" in calls[2][1]  # 图B描述注入对比 prompt
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_vision_client.py -v`
Expected: FAIL（AttributeError: module 'vision_client' has no attribute 'locate'/'compare'）

- [ ] **Step 3: 实现**

`vision_client.py` 在 `_image_size` 之后新增：
```python
def locate(path_or_b64: str, query: str, precision: str = "") -> str:
    """按 query 定位图中元素（grounding bbox）。复用 spatial 提示词注入查询。
    返回元素名 + bbox JSON + 原图尺寸。模型不支持 grounding 时仅返回元素名列表。"""
    e = _entry("spatial")
    img_path = path_or_b64 if os.path.exists(path_or_b64) else ""
    size = _image_size(img_path) if img_path else ""
    prompt = f"用户在图中查找：{query}\n\n{e['text']}"
    text = _post_b64(_to_b64(path_or_b64), prompt, e["temperature"])
    if size:
        text += f"\n【原图尺寸】{size}"
    return text


def compare(path_a: str, path_b: str, precision: str = "") -> str:
    """双图对比：各自按精度识别，再以图A为锚、注入图B描述，调用视觉模型逐点对比。"""
    cfg = config_loader.get()
    temp = cfg["ollama"]["temperature"]
    b64_a = _to_b64(path_a)
    desc_a = analyze(path_a, precision)
    desc_b = analyze(path_b, precision)
    prompt = (
        f"下面是图B的识别描述，请以当前（图A）为基础逐点对比：\n\n"
        f"【图B描述】\n{desc_b}\n\n"
        f"请输出：1) 图A与图B的相同点 2) 不同点（内容/布局/颜色/文字）3) 结论"
    )
    text = _post_b64(b64_a, prompt, temp)
    return f"【图A】\n{desc_a}\n\n【图B】\n{desc_b}\n\n【对比】\n{text}"
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_vision_client.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add vision_client.py tests/test_vision_client.py
git commit -m "feat(vision_client): 新增 locate()/compare() 供 MCP 工具复用"
```

---

### Task 3: mcp_server.py（Python MCP server，5 工具）

**Files:**
- Create: `mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: Task 1 的 `config_loader.resolve_backend()`；Task 2 的 `vision_client.locate/compare`；现有 `vision_client.analyze/ocr/describe`。
- Produces: `mcp_server.serve(instream, outstream)`（stdio 循环入口）、`mcp_server._handle(msg, out)`（JSON-RPC 分发，供测试直接调用）、模块常量 `RULES_TEXT`（触发规则母版，Task 4 的 `mcp_hosts` 复用同文）。`main()` 供宿主 spawn。

- [ ] **Step 1: 写失败测试**

`tests/test_mcp_server.py`：
```python
import io
import json

import mcp_server


def _call(msg):
    out = io.StringIO()
    mcp_server._handle(msg, out)
    lines = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    return lines[0] if lines else None


def test_initialize():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "vision-mcp"


def test_tools_list_has_five():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in r["result"]["tools"]]
    assert names == ["describe_image", "extract_text", "locate_object", "compare_images", "vision_rules"]


def test_unknown_method():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "foo/bar"})
    assert r["error"]["code"] == -32601


def test_unknown_tool():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "nope", "arguments": {}}})
    assert r["error"]["code"] == -32602


def test_describe_image_calls_analyze(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "analyze", lambda p: f"识别:{p}")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "describe_image", "arguments": {"image": str(img)}}})
    assert r["result"]["content"][0]["text"] == f"识别:{img}"
    assert r["result"]["isError"] is False


def test_describe_image_missing_or_bad_path(tmp_path):
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "describe_image", "arguments": {}}})
    assert r["result"]["isError"] is True
    r2 = _call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "describe_image", "arguments": {"image": str(tmp_path / "nope.png")}}})
    assert r2["result"]["isError"] is True


def test_extract_text_ocr_empty_falls_back(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "ocr", lambda p: "")
    monkeypatch.setattr(mcp_server.vision_client, "describe", lambda p, prompt="": "OCR 回退文字")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "extract_text", "arguments": {"image": str(img)}}})
    assert "OCR 回退文字" in r["result"]["content"][0]["text"]


def test_extract_text_ocr_hit(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "ocr", lambda p: "报错栈文字")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "extract_text", "arguments": {"image": str(img)}}})
    assert r["result"]["content"][0]["text"] == "报错栈文字"


def test_locate_object_calls_locate(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "locate",
                        lambda p, q: f'[{{"name":"{q}","bbox":[0,0,1,1]}}]')
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "locate_object", "arguments": {"image": str(img), "query": "按钮"}}})
    assert "按钮" in r["result"]["content"][0]["text"]


def test_compare_images_calls_compare(monkeypatch, tmp_path):
    a = tmp_path / "a.png"; a.write_bytes(b"x")
    b = tmp_path / "b.png"; b.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "compare", lambda x, y: f"diff:{x}|{y}")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "compare_images", "arguments": {"image_a": str(a), "image_b": str(b)}}})
    assert f"diff:{a}|{b}" in r["result"]["content"][0]["text"]


def test_vision_rules_returns_text():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "vision_rules", "arguments": {}}})
    assert "describe_image" in r["result"]["content"][0]["text"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL（ModuleNotFoundError: No module named 'mcp_server'）

- [ ] **Step 3: 实现**

Create `mcp_server.py`（stdlib-only）：
```python
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
    return {"content": [{"type": "text", "text": vision_client.analyze(path)}], "isError": False}


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
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: 11 passed

- [ ] **Step 5: 手工冒烟（不要求进测试套件）**

Run: `printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | python mcp_server.py`
Expected: 单行 JSON 响应含 5 个工具名，进程正常退出。

- [ ] **Step 6: 提交**

```bash
git add mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp_server): Python MCP server，5 工具，import vision_client 独立存活"
```

---

### Task 4: mcp_hosts.py（多宿主注册 + 触发规则）

**Files:**
- Create: `mcp_hosts.py`
- Test: `tests/test_mcp_hosts.py`

**Interfaces:**
- Consumes: Task 3 的 `RULES_TEXT`（同文常量，本模块自带一份，防止跨模块 import 耦合）。
- Produces:
  - `HOSTS = ["claude", "codex", "opencode", "cline", "continue", "copilot", "cursor"]`
  - `_python_cmd() -> str`（`sys.executable`）、`_server_args() -> [str]`（`SERVER_PATH`）
  - 写配置：`write_codex(path) -> bool`、`write_opencode(path)`、`write_cline(path)`、`write_continue(path)`、`write_copilot(path)`、`write_cursor(path)`（均幂等，merge 不覆盖既有键）
  - 写规则：`write_agents_md(path) -> bool`、`write_clinerules(path) -> bool`、`write_copilot_rules(path) -> bool`、`write_continue_rules(path)`
  - `register_host(host) -> list[str]`（含 claude 的 `claude mcp add`）
  - `host_status() -> list[tuple[host, ok: bool, detail: str]]`（只读，供 doctor）
  - 路径发现：`codex_path()/opencode_path()/cline_paths()/continue_path()/copilot_path()/cursor_path()`
  - `file_has_vision(path) -> bool`（探测 `mcp_server.py` 标记）

- [ ] **Step 1: 写失败测试**

`tests/test_mcp_hosts.py`：
```python
import json

import mcp_hosts


def _srv(monkeypatch):
    monkeypatch.setattr(mcp_hosts, "_python_cmd", lambda: "python")
    monkeypatch.setattr(mcp_hosts, "_server_args", lambda: ["C:/vision/mcp_server.py"])


def test_write_codex(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "config.toml"
    assert mcp_hosts.write_codex(str(p)) is True
    text = p.read_text(encoding="utf-8")
    assert "[mcp_servers.vision]" in text
    assert "C:/vision/mcp_server.py" in text
    assert mcp_hosts.write_codex(str(p)) is False  # 幂等


def test_write_opencode_merges(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "opencode.json"
    p.write_text('{"existing": 1}', encoding="utf-8")
    mcp_hosts.write_opencode(str(p))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["existing"] == 1
    m = data["mcp"]["vision"]
    assert m["type"] == "local"
    assert m["command"] == ["python", "C:/vision/mcp_server.py"]


def test_write_cline(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "cline_mcp_settings.json"
    mcp_hosts.write_cline(str(p))
    m = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["vision"]
    assert m["command"] == "python"
    assert m["disabled"] is False


def test_write_continue_merges_array(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "config.json"
    p.write_text('{"mcpServers": [{"name": "other", "command": "x"}]}', encoding="utf-8")
    mcp_hosts.write_continue(str(p))
    names = [s["name"] for s in json.loads(p.read_text(encoding="utf-8"))["mcpServers"]]
    assert "other" in names and "vision" in names


def test_write_copilot(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "mcp.json"
    mcp_hosts.write_copilot(str(p))
    m = json.loads(p.read_text(encoding="utf-8"))["servers"]["vision"]
    assert m["type"] == "stdio"
    assert m["command"] == "python"


def test_write_cursor(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "mcp.json"
    mcp_hosts.write_cursor(str(p))
    m = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["vision"]
    assert m["args"] == ["C:/vision/mcp_server.py"]


def test_rules_append_idempotent(tmp_path):
    p = tmp_path / "AGENTS.md"
    p.write_text("# Project", encoding="utf-8")
    assert mcp_hosts.write_agents_md(str(p)) is True
    assert mcp_hosts.write_agents_md(str(p)) is False
    assert p.read_text(encoding="utf-8").count("describe_image") == 1


def test_host_status_shape(monkeypatch):
    monkeypatch.setattr(mcp_hosts, "file_has_vision", lambda p: False)
    monkeypatch.setattr(mcp_hosts, "_cmd", lambda *a, **k: (1, ""))
    rows = mcp_hosts.host_status()
    assert len(rows) >= 7
    assert all(len(r) == 3 for r in rows)
    assert rows[0][0] == "claude"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_mcp_hosts.py -v`
Expected: FAIL（ModuleNotFoundError: No module named 'mcp_hosts'）

- [ ] **Step 3: 实现**

Create `mcp_hosts.py`（stdlib-only）：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多宿主 MCP 注册 + 触发规则（install.py --mcp 与 toggle.py doctor 共用）。

设计要点：
- 零第三方依赖（TOML 手写追加；JSON 用 json 库 merge，不覆盖宿主既有配置）。
- 幂等：已注册/已含规则时不再重复写。
- 触发规则文本与 mcp_server.py 的 RULES_TEXT 同源（视觉能力规范母版）。
"""
import json
import os
import subprocess
import sys

SERVER_PATH = os.path.join(os.path.expanduser("~/.claude/vision-eyes"), "mcp_server.py")
HOSTS = ["claude", "codex", "opencode", "cline", "continue", "copilot", "cursor"]

RULES_TEXT = """# 视觉能力（text-llm-vision）
你的模型没有视觉能力。出现以下情况必须调用相应工具：
- 用户引用本地图片路径 / 粘贴截图 / 你看到 [Unsupported Image] → describe_image(图片路径)
- 终端红字、报错栈、文档扫描 → extract_text(图片路径)
- 图中某元素在哪里 → locate_object(图片路径, 元素名)
- 前后两张图对比 → compare_images(图A路径, 图B路径)
"""


def _python_cmd() -> str:
    return sys.executable


def _server_args() -> list:
    return [SERVER_PATH]


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _cmd(args, timeout=20):
    kwargs = dict(capture_output=True, text=True, timeout=timeout,
                  encoding="utf-8", errors="replace")
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        p = subprocess.run(args, **kwargs)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        if os.name == "nt":
            try:
                p = subprocess.run(["cmd", "/c", *args], **kwargs)
                return p.returncode, (p.stdout or "") + (p.stderr or "")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return 127, f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, (dict, list)) else {}
    except (ValueError, OSError):
        return {}


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _entry_stdio() -> dict:
    return {"command": _python_cmd(), "args": _server_args()}


def _append_if_missing(path, text, marker) -> bool:
    """文件不存在或未含 marker 时追加 text；返回是否真的写入。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if marker in f.read():
                    return False
        except OSError:
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + text if os.path.exists(path) and text.startswith("#") else text)
    return True


# ---- 路径发现 ----

def _home() -> str:
    return os.path.expanduser("~")


def codex_path() -> str:
    return os.path.join(_home(), ".codex", "config.toml")


def opencode_path() -> str:
    return os.path.join(_home(), ".config", "opencode", "opencode.json")


def cline_paths() -> list:
    ap = os.environ.get("APPDATA", "")
    vs_code = (os.path.join(ap, "Code", "User", "globalStorage",
                            "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json")
               if ap else "")
    cli = os.path.join(_home(), ".cline", "data", "settings", "cline_mcp_settings.json")
    return [p for p in (vs_code, cli) if p]


def continue_path() -> str:
    return os.path.join(_home(), ".continue", "config.json")


def copilot_path() -> str:
    return os.path.join(os.getcwd(), ".vscode", "mcp.json")


def cursor_path() -> str:
    return os.path.join(_home(), ".cursor", "mcp.json")


# ---- 写 MCP 配置（各宿主格式差异见 spec §7） ----

def write_codex(path: str) -> bool:
    """~/.codex/config.toml 追加 [mcp_servers.vision]（TOML 手写）。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if "[mcp_servers.vision]" in f.read():
                    return False
        except OSError:
            pass
    block = (f'\n[mcp_servers.vision]\ncommand = "{_python_cmd()}"\n'
             f'args = ["{_norm(_server_args()[0])}"]\n')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    return True


def write_opencode(path: str):
    """opencode.json 用 `mcp` 键 + 数组 command + `environment`（非 mcpServers 格式）。"""
    data = _load_json(path)
    data.setdefault("mcp", {})["vision"] = {
        "type": "local",
        "command": [_python_cmd(), _server_args()[0]],
        "enabled": True,
    }
    _save_json(path, data)


def write_cline(path: str):
    data = _load_json(path)
    data.setdefault("mcpServers", {})["vision"] = {
        **_entry_stdio(), "disabled": False, "autoApprove": [],
    }
    _save_json(path, data)


def write_continue(path: str):
    data = _load_json(path)
    servers = data.setdefault("mcpServers", [])
    if isinstance(servers, dict):  # 兼容对象形态 → 转数组
        servers = data["mcpServers"] = []
    if not any(isinstance(s, dict) and s.get("name") == "vision" for s in servers):
        servers.append({"name": "vision", **_entry_stdio()})
    _save_json(path, data)


def write_copilot(path: str):
    """.vscode/mcp.json（VS Code Copilot 用 `servers` 键 + type:stdio）。"""
    data = _load_json(path)
    data.setdefault("servers", {})["vision"] = {"type": "stdio", **_entry_stdio()}
    _save_json(path, data)


def write_cursor(path: str):
    """.cursor/mcp.json（与 Claude Code 同构：mcpServers + command/args）。"""
    data = _load_json(path)
    data.setdefault("mcpServers", {})["vision"] = _entry_stdio()
    _save_json(path, data)


# ---- 触发规则 ----

def write_agents_md(path: str) -> bool:
    return _append_if_missing(path, RULES_TEXT, "describe_image")


def write_clinerules(path: str) -> bool:
    return _append_if_missing(path, RULES_TEXT, "describe_image")


def write_copilot_rules(path: str) -> bool:
    return _append_if_missing(path, RULES_TEXT, "describe_image")


def write_continue_rules(path: str):
    data = _load_json(path)
    rules = data.setdefault("rules", [])
    if not any("describe_image" in r for r in rules):
        rules.append(RULES_TEXT)
    _save_json(path, data)


# ---- 注册入口 ----

def register_host(host: str) -> list:
    """注册一个宿主：写 MCP 配置 + 写触发规则。返回描述行列表。"""
    msgs = []
    if host == "claude":
        code, _ = _cmd(["claude", "mcp", "add", "--scope", "user", "vision",
                        "--", _python_cmd(), _server_args()[0]])
        msgs.append(f"claude: {'✓' if code == 0 else '✗'} claude mcp add")
        _append_if_missing(os.path.join(_home(), "CLAUDE.md"), RULES_TEXT, "describe_image")
    elif host == "codex":
        msgs.append(f"codex: {'✓' if write_codex(codex_path()) else '已注册'} config.toml")
        msgs.append(f"codex: {'✓' if write_agents_md(os.path.join(os.getcwd(), 'AGENTS.md')) else '已含规则'} AGENTS.md")
    elif host == "opencode":
        write_opencode(opencode_path())
        msgs.append("opencode: ✓ opencode.json")
        msgs.append(f"opencode: {'✓' if write_agents_md(os.path.join(os.getcwd(), 'AGENTS.md')) else '已含规则'} AGENTS.md")
    elif host == "cline":
        wrote = False
        for p in cline_paths():
            write_cline(p)
            wrote = True
        msgs.append(f"cline: {'✓' if wrote else '✗ 未找到配置路径'} cline_mcp_settings.json")
        msgs.append(f"cline: {'✓' if write_clinerules(os.path.join(os.getcwd(), '.clinerules')) else '已含规则'} .clinerules")
    elif host == "continue":
        write_continue(continue_path())
        write_continue_rules(continue_path())
        msgs.append("continue: ✓ config.json")
    elif host == "copilot":
        write_copilot(copilot_path())
        msgs.append("copilot: ✓ .vscode/mcp.json")
        msgs.append(f"copilot: {'✓' if write_copilot_rules(os.path.join(os.getcwd(), '.github', 'copilot-instructions.md')) else '已含规则'} copilot-instructions.md")
    elif host == "cursor":
        write_cursor(cursor_path())
        msgs.append("cursor: ✓ .cursor/mcp.json")
        msgs.append(f"cursor: {'✓' if write_agents_md(os.path.join(os.getcwd(), 'AGENTS.md')) else '已含规则'} AGENTS.md")
    else:
        msgs.append(f"未知宿主: {host}（可用: {', '.join(HOSTS + ['all'])})")
    return msgs


def register_all() -> list:
    msgs = []
    for h in HOSTS:
        msgs.extend(register_host(h))
    return msgs


# ---- 只读状态（doctor 用） ----

def file_has_vision(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return "mcp_server.py" in f.read()
    except OSError:
        return False


def host_status() -> list:
    """[(宿主, 是否就绪, 详情)]，只读检查。"""
    rows = []
    code, out = _cmd(["claude", "mcp", "list"])
    rows.append(("claude", code == 0 and "vision" in out, "claude mcp list"))
    rows.append(("codex", file_has_vision(codex_path()), codex_path()))
    rows.append(("opencode", file_has_vision(opencode_path()), opencode_path()))
    cline_rows = [p for p in cline_paths() if file_has_vision(p)]
    rows.append(("cline", bool(cline_rows), "; ".join(cline_paths()) or "(未找到路径)"))
    rows.append(("continue", file_has_vision(continue_path()), continue_path()))
    rows.append(("copilot", file_has_vision(copilot_path()), copilot_path()))
    rows.append(("cursor", file_has_vision(cursor_path()), cursor_path()))
    return rows
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_mcp_hosts.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add mcp_hosts.py tests/test_mcp_hosts.py
git commit -m "feat(mcp_hosts): 多宿主注册 + 触发规则（opencode mcp 键 / Cline 双路径 / Continue 数组）"
```

---

### Task 5: install.py 接线 + toggle.py doctor 扩展

**Files:**
- Modify: `install.py`（NEEDED_FILES、`ensure_mcp()` 换 Python server、`--mcp` 参数、`main()` 分发）
- Modify: `toggle.py`（`doctor()` 增加多宿主检查）
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: Task 4 的 `mcp_hosts.register_host/register_all/host_status`。
- Produces: `install.py --mcp <host>` / `--mcp all` 命令；`toggle.py doctor` 输出「多宿主 MCP 注册」段。

- [ ] **Step 1: 写失败测试**

`tests/test_install.py`：
```python
import os

import install


def test_needed_files_include_new():
    assert "mcp_server.py" in install.NEEDED_FILES
    assert "mcp_hosts.py" in install.NEEDED_FILES


def test_ensure_mcp_uses_python_server(monkeypatch, tmp_path):
    import mcp_hosts
    server = os.path.join(tmp_path, "mcp_server.py")
    monkeypatch.setattr(install, "TARGET", str(tmp_path))
    monkeypatch.setattr(mcp_hosts, "SERVER_PATH", server)
    calls = []

    def fake_mcp_registered():
        return False

    def fake_run(cmd, timeout=30):
        calls.append(cmd)
        return 0, ""

    def fake_mcp_registered_after():
        return True

    monkeypatch.setattr(install, "mcp_registered", fake_mcp_registered)
    monkeypatch.setattr(install, "run", fake_run)
    # 第一次注册后第二次检测成功
    monkeypatch.setattr(install, "mcp_registered", lambda: calls and calls[0].index("mcp add") >= 0)
    ok = install.ensure_mcp()
    assert ok is True
    assert "mcp_server.py" in calls[0]  # 用 python server，非 node


def test_register_all_dispatch(monkeypatch):
    import mcp_hosts
    fake = {}
    for h in mcp_hosts.HOSTS:
        fake[h] = None
    monkeypatch.setattr(mcp_hosts, "register_host", lambda h: fake.__setitem__(h, True) or [h])
    rows = install.register_mcp("all")
    assert len(rows) >= 7
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_install.py -v`
Expected: FAIL（`mcp_server.py` 不在 NEEDED_FILES / 无 `install.register_mcp` / `ensure_mcp` 仍用 node）

- [ ] **Step 3: 实现**

`install.py` 修改点：

1) `NEEDED_FILES` 加两项：
```python
NEEDED_FILES = [
    "proxy.py", "config_loader.py", "control_api.py", "prompts.py", "vision_client.py",
    "mcp-vision.js", "mcp_server.py", "mcp_hosts.py",
    "identify.py", "batch_identify.py", "collect_images.py",
    "scan_one.py", "read_port.py", "toggle.py", "start_proxy.py",
    "start-proxy.bat", "status.bat", "requirements.txt", "install.py", "README.md",
]
```

2) `ensure_mcp()` 改为注册 Python server：
```python
def ensure_mcp() -> bool:
    if mcp_registered():
        mark(True, "MCP server `vision` 已注册")
        return True
    server = os.path.join(TARGET, "mcp_server.py")
    cmd = ["claude", "mcp", "add", "--scope", "user", "vision", "--", sys.executable, server]
    code, out = run(cmd, timeout=30)
    ok = code == 0 and mcp_registered()
    mark(ok, "注册 MCP server `vision`（mcp_server.py）")
    if not ok:
        print(f"   → 修复: claude mcp add --scope user vision -- {sys.executable} \"{server}\"")
    return ok
```

3) 新增注册入口函数与 argparse 参数：
```python
def register_mcp(host_arg: str) -> int:
    """--mcp 入口：注册到指定宿主或 all。"""
    import mcp_hosts
    mcp_hosts.SERVER_PATH = os.path.join(TARGET, "mcp_server.py")
    if host_arg == "all":
        rows = mcp_hosts.register_all()
    else:
        rows = mcp_hosts.register_host(host_arg)
    for r in rows:
        print(r)
    return 0
```
```python
    ap.add_argument("--mcp", metavar="HOST", default="",
                    help="注册 MCP 到宿主: claude|codex|opencode|cline|continue|copilot|cursor|all")
```
```python
    if args.mcp:
        return register_mcp(args.mcp)
```

`toggle.py` `doctor()` 末尾（`print("\n== 体检完成 ==")` 之前）加：
```python
    # 7. 多宿主 MCP 注册
    try:
        import mcp_hosts
        mcp_hosts.SERVER_PATH = os.path.join(os.path.expanduser("~/.claude/vision-eyes"), "mcp_server.py")
        print("\n-- 多宿主 MCP 注册（install.py --mcp <host> 补全）--")
        for host, ok, detail in mcp_hosts.host_status():
            print(f"{'✓' if ok else '✗'}  {host}: {detail}")
    except ImportError:
        pass  # 旧部署无 mcp_hosts.py，忽略（doctor 其余项仍有效）
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_install.py -v`
Expected: 3 passed

- [ ] **Step 5: 手动验证 --mcp 幂等（不要求进测试套件）**

Run: `python install.py --mcp codex` 两次
Expected: 第一次写 `~/.codex/config.toml` 含 `[mcp_servers.vision]`；第二次提示「已注册」不重复追加。运行 `python install.py --check` 或 `python toggle.py doctor`，输出含「多宿主 MCP 注册」段。

- [ ] **Step 6: 提交**

```bash
git add install.py toggle.py tests/test_install.py
git commit -m "feat(install): --mcp 多宿主注册 + ensure_mcp 换 Python server + doctor 宿主检查"
```

---

### Task 6: README 重定位 + Dockerfile + 全量回归

**Files:**
- Modify: `README.md`
- Modify: `Dockerfile`

**Interfaces:** 无（文档 + 构建文件）。

- [ ] **Step 1: README 重定位**

把「MCP Tool Use 为主路径、代理拦截为粘贴兜底」写进 README 开头与目录结构：
- 新增章节「MCP 主路径（推荐）」：一句话定位 + 5 工具表 + 注册方式（`install.py --mcp all`）+ env 配置表（`VISION_MODEL`/`OLLAMA_BASE_URL`/`OLLAMA_URL` alias/`VISION_API_KEY`/`VISION_API_BASE_URL`）。
- 新增「多宿主注册矩阵」表（spec §7）：宿主 × 配置文件 × 触发规则文件，标注 opencode `mcp` 键、Cline CLI/VS Code 双路径、Continue 数组三个格式坑。
- 重定位现有「代理」章节为「代理（兜底路径）」：仅对话内粘贴场景 + 操作层（/identify、面板）。标注 mcp-vision.js 为旧形态（新部署用 mcp_server.py，向后兼容）。
- 触发规则章节：母版文本 + `vision_rules` 工具说明。

- [ ] **Step 2: Dockerfile 加运行时文件**

`Dockerfile` 的 COPY 行追加两个新文件：
```dockerfile
COPY proxy.py config_loader.py control_api.py prompts.py vision_client.py toggle.py \
     mcp_server.py mcp_hosts.py ./
```

- [ ] **Step 3: 全量回归**

Run: `python -m pytest tests/ -v`
Expected: 全部通过（Task 1-5 测试 + 无既有测试冲突）。若 `test_proxy.py` 存在且依赖真实服务，跳过或单独跑：
```bash
python -m pytest tests/ -v -m "not integration" 2>/dev/null || python -m pytest tests/ -v
```
再跑 `python install.py --check`：除 BASE_URL 外应全 ✓，且含「多宿主 MCP 注册」段。

- [ ] **Step 4: 提交**

```bash
git add README.md Dockerfile
git commit -m "docs(README): MCP 主路径定位 + 多宿主注册矩阵；feat(Dockerfile): 打包 mcp_server/mcp_hosts"
```
（若回归失败，先回到对应 Task 修复再提交。）

---

## Self-Review 结果

- **Spec 覆盖**：§2 架构（Task 3 mcp_server + Task 4 mcp_hosts）、§3 数据流（Task 2/3 工具实现）、§4 5 工具（Task 3）、§5 配置源（Task 1）、§6 代理兜底（Task 6 README 重定位，代码不动）、§7 注册矩阵（Task 4）、§8 触发规则（Task 4 RULES_TEXT + vision_rules）、§9 部署改动（Task 5 + Task 6 Docker）、§10 测试计划（各 Task 测试 + Task 6 回归）——全部有对应任务。
- **占位符扫描**：无 TBD/TODO；所有步骤含实际代码。
- **类型一致性**：`resolve_backend()` 返回键（provider/model/url/api_key/base_url/precision）在 Task 1 定义、Task 3 消费（`backend['provider']`/`backend['model']`）；`locate/compare` 签名在 Task 2 定义、Task 3 调用；`register_host/register_all/host_status` 在 Task 4 定义、Task 5 调用；`_python_cmd/_server_args` 在 Task 4 定义、Task 4/5 测试 monkeypatch——前后一致。
