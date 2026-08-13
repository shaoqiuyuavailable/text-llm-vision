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
import logging
import os

import prompts as _prompts

CONFIG_PATH = os.path.expanduser("~/.claude/vision-eyes/config.json")
DEFAULT_PORT = 8787  # 代理默认监听端口（config.json 的 port 可覆盖）
log = logging.getLogger("vision_proxy")  # 与 proxy.py 同 logger，写同一日志文件


def _defaults() -> dict:
    return {
        "port": DEFAULT_PORT,  # 代理监听端口（config.json 可覆盖）
        # 上游默认地址（纯文本模型真实端点）。代理按请求头 token 反查 CC Switch
        # provider 真实上游；查不到时回退此处。解绑 DeepSeek 写死，可配任意上游。
        "upstream": "https://api.deepseek.com/anthropic",
        "ollama": {
            "url": _prompts.OLLAMA,
            "model": _prompts.VISION_MODEL,
            "temperature": _prompts.TEMPERATURE,
            "top_p": _prompts.TOP_P,
        },
        # 可选云端通道（多平台轮换）：clouds 数组 + cloud.active 选当前平台。
        # 任一平台配了 key 就走云端（OpenAI 兼容），否则纯本地 Ollama。
        # 保留"默认零配置纯本地"的定位，云端是手动开关。
        "cloud": {
            "active": "",
            "clouds": [],
        },
        "scenes": {k: dict(v) for k, v in getattr(_prompts, "SCENES", {}).items()},
        "prompts": {k: dict(v) for k, v in _prompts.PROMPTS.items()},
    }


def get_port() -> int:
    """代理监听端口（config.json 的 port，缺失/非法回退默认 8787）。"""
    try:
        return int(get().get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT


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
            if "port" in data:
                cfg["port"] = data["port"]
            if "upstream" in data:
                cfg["upstream"] = data["upstream"]
            if isinstance(data.get("cloud"), dict):
                cfg["cloud"].update(data["cloud"])
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
    except FileNotFoundError:
        log.debug("config.json missing, using built-in defaults")  # 首次部署常见，debug 即可
    except (OSError, ValueError, json.JSONDecodeError):
        log.warning("config.json corrupt/unreadable, using built-in defaults")  # 损坏 → warning（#15）
    return cfg
