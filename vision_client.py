#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一视觉识别客户端：三次判定 + 混合方案（大类判定 + 组合分支兜底）。

    scan(path)                 -> (描述, 大类, 小类)   第1次：描述+场景判断
    zoom(path, scene, sub)     -> 事实提取               第2次：按大类选清单，小类注入上下文
    guess(path, context, scene, sub, scan_desc) -> 推测   第3次：基于事实+场景

提示词与采样参数从 config.json 读取（缺失回退 prompts.py）。
每条提示词可带自己的 temperature，无则用全局 ollama.temperature。
"""
import base64
import hashlib
import os
import re
import httpx

import config_loader

_cache = {}

# 大类 -> 允许的小类（从 config 读，这里仅为默认兜底）
MAIN_SCENES = ("person", "animal", "document", "chart", "generic")


def _scenes() -> dict:
    return config_loader.get().get("scenes", {})


def _entry(prompt_key: str) -> dict:
    """取提示词条目，附上生效温度。"""
    cfg = config_loader.get()
    entry = cfg["prompts"].get(prompt_key, {"text": "", "temperature": None})
    temp = entry["temperature"] if entry.get("temperature") is not None else cfg["ollama"]["temperature"]
    return {"text": entry.get("text", ""), "temperature": temp}


def _post_b64(b64: str, prompt: str, temperature: float) -> str:
    cfg = config_loader.get()
    o = cfg["ollama"]
    key = hashlib.sha256((b64 + "|" + prompt).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    r = httpx.post(o["url"], json={"model": o["model"], "prompt": prompt, "images": [b64],
                                   "stream": False, "options": {"temperature": temperature,
                                                                "top_p": o["top_p"]}},
                   timeout=300, trust_env=False)
    r.raise_for_status()
    text = r.json()["response"].strip()
    _cache[key] = text
    return text


def _to_b64(path_or_b64: str) -> str:
    if os.path.exists(path_or_b64):
        with open(path_or_b64, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return path_or_b64


def _parse_scene(text: str) -> tuple[str, str]:
    """从 scan 输出解析 (大类, 小类)。容忍有标签(大类: X)与无标签(纯值)两种输出。"""
    main, sub = "generic", ""
    all_subs = [s for d in _scenes().values() for s in d.get("sub", [])]

    # 优先找「大类: X」标签
    m = re.search(r"大类\s*[=:：]\s*(\w+)", text)
    if m:
        cand = m.group(1).lower()
        if cand in MAIN_SCENES:
            main = cand
    # 无标签时：从文本里挑第一个出现在 MAIN_SCENES 里的词
    if main == "generic" and "generic" not in text:
        for tok in re.findall(r"[A-Za-z]+", text):
            if tok.lower() in MAIN_SCENES:
                main = tok.lower()
                break

    # 小类：优先找「小类: X」标签
    ms = re.search(r"小类\s*[=:：]\s*(\w+)", text)
    if ms and ms.group(1) != "无":
        cand = ms.group(1).lower()
        if cand in all_subs or cand in MAIN_SCENES:
            sub = cand
    if not sub:
        # 无标签：在 main 允许的小类里挑第一个出现的
        allowed = _scenes().get(main, {}).get("sub", [])
        for tok in re.findall(r"[A-Za-z]+", text):
            if tok.lower() in allowed:
                sub = tok.lower()
                break
    # 兜底：大类没有小类时（animal/chart）强制清空，避免 8B 乱填
    if not _scenes().get(main, {}).get("sub"):
        sub = ""
    return main, sub


def describe(path_or_b64: str, prompt: str = "") -> str:
    """用指定的完整提示词识别（prompt 直接作为提示词，温度用全局）。
    prompt 缺省时用 config.prompts.default。"""
    if not prompt:
        prompt = config_loader.get()["prompts"].get("default", {}).get("text", "简短描述这张图片。")
    temp = config_loader.get()["ollama"]["temperature"]
    return _post_b64(_to_b64(path_or_b64), prompt, temp)


def scan(path_or_b64: str) -> tuple[str, str, str]:
    """第1次：一句话描述 + 判断大类+小类。返回 (描述, 大类, 小类)。"""
    e = _entry("scan")
    text = _post_b64(_to_b64(path_or_b64), e["text"], e["temperature"])
    main, sub = _parse_scene(text)
    return text, main, sub


def zoom(path_or_b64: str, scene: str = "generic", sub: str = "", scan_desc: str = "") -> str:
    """第2次：按大类选 zoom 清单，小类作为上下文注入。"""
    if scene not in MAIN_SCENES:
        scene = "generic"
    e = _entry(f"zoom_{scene}")
    prompt = e["text"]
    header = f"场景大类：{scene}"
    if sub:
        header += f"，小类：{sub}"
    if scan_desc.strip():
        header += f"\n第1次扫描的初步判断：\n{scan_desc.strip()}"
    prompt = f"{header}\n\n请基于以上信息，按以下清单逐项提取更精确的事实：\n{e['text']}"
    return _post_b64(_to_b64(path_or_b64), prompt, e["temperature"])


def guess(path_or_b64: str, context: str = "", scene: str = "", sub: str = "", scan_desc: str = "") -> str:
    """第3次：基于 scan 描述 + zoom 事实 + 场景，大胆推测。"""
    e = _entry("guess")
    prompt = e["text"]
    if scene:
        prompt += f"\n场景大类：{scene}"
    if sub:
        prompt += f"，小类：{sub}"
    if scan_desc.strip():
        prompt += f"\n\n第1次扫描的初步判断：\n{scan_desc.strip()}"
    if context.strip():
        prompt += f"\n\n已提取的事实特征：\n{context.strip()}"
    return _post_b64(_to_b64(path_or_b64), prompt, e["temperature"])
