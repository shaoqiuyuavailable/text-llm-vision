#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""读取 config.json；缺失或损坏时回退到 prompts.py 内置默认值。

对外暴露 get()：
    cfg = config_loader.get()
    cfg["ollama"]["url"]            # Ollama 地址
    cfg["ollama"]["model"]          # 视觉模型名
    cfg["ollama"]["temperature"]    # 全局默认采样温度
    cfg["ollama"]["top_p"]          # 核采样
    cfg["scenes"]                   # {大类: {sub: [...], default_sub: ...}}
    cfg["prompts"]["scan"]          # {"text":..., "temperature":...}
    cfg["prompts"]["zoom_person"]   # 按大类 zoom 提示词
    cfg["prompts"]["guess"]         # 大胆推测

提示词条目两种形态都兼容：
    "scan": "旧式纯字符串"            → 温度用全局
    "scan": {"text": "...", "temperature": 0.3}  → 温度用条目自带
"""
import json
import os

import prompts as _prompts

CONFIG_PATH = os.path.expanduser("~/.claude/vision-eyes/config.json")


def _defaults() -> dict:
    return {
        "ollama": {
            "url": _prompts.OLLAMA,
            "model": _prompts.VISION_MODEL,
            "temperature": _prompts.TEMPERATURE,
            "top_p": _prompts.TOP_P,
        },
        "scenes": {k: dict(v) for k, v in getattr(_prompts, "SCENES", {}).items()},
        "prompts": {k: dict(v) for k, v in _prompts.PROMPTS.items()},
    }


def _normalize_entry(entry) -> dict:
    """把提示词条目统一成 {"text", "temperature"|None}。"""
    if isinstance(entry, str):
        return {"text": entry, "temperature": None}
    if isinstance(entry, dict):
        return {"text": entry.get("text", ""), "temperature": entry.get("temperature")}
    return {"text": str(entry), "temperature": None}


def get() -> dict:
    cfg = _defaults()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if isinstance(data.get("ollama"), dict):
                cfg["ollama"].update(data["ollama"])
            if isinstance(data.get("scenes"), dict):
                for k, v in data["scenes"].items():
                    if isinstance(v, dict):
                        cfg["scenes"][k] = {
                            "sub": v.get("sub", []),
                            "default_sub": v.get("default_sub", ""),
                        }
            if isinstance(data.get("prompts"), dict):
                for k, v in data["prompts"].items():
                    cfg["prompts"][k] = _normalize_entry(v)
    except (OSError, ValueError, json.JSONDecodeError):
        pass  # 缺失/损坏 → 用内置默认
    return cfg
