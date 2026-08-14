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
        # OpenAI 兼容上游基础 URL（/v1/chat/completions 由它拼接）。
        # OpenAI 协议入站（/v1/chat/completions）走这里；Anthropic 协议走 upstream。
        # 默认留空（不绑定任何厂商），需在 config.json 显式配置；未配置时 OpenAI 入站返回 400 提示。
        "upstream_openai": "",
        "ollama": {
            "url": _prompts.OLLAMA,
            "model": _prompts.VISION_MODEL,
            "temperature": _prompts.TEMPERATURE,
            "top_p": _prompts.TOP_P,
            # 空间结构识别（deep 档 grounding bbox）。模型无关：支持 grounding 的模型
            # 才开（如 Qwen2.5-VL）；换不支持 bbox 定位的模型时设 false 跳过 spatial。
            "grounding": True,
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
        "modes": dict(getattr(_prompts, "MODES", {})),  # --mode 动态温度表（v2）
        "router": dict(getattr(_prompts, "ROUTER", {})),  # 视觉路由器：scene → 引擎
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
            if "upstream_openai" in data:
                cfg["upstream_openai"] = data["upstream_openai"]
            if isinstance(data.get("cloud"), dict):
                cfg["cloud"].update(data["cloud"])
            if isinstance(data.get("ollama"), dict):
                cfg["ollama"].update(data["ollama"])
            # v2 门：仅当 prompts_version>=2 才叠加 scenes/prompts/modes。
            # 旧 config（无此键=1）的 5 类提示词会压住 v2 基线，跳过高版本结构用基线。
            pv = data.get("prompts_version", 1)
            if not isinstance(pv, int):
                pv = 1
            if pv >= 2:
                if isinstance(data.get("modes"), dict):
                    cfg["modes"].update(data["modes"])
                if isinstance(data.get("router"), dict):
                    cfg["router"].update(data["router"])
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
    # ---- env 覆盖（MCP server / Docker 直连）：env > config > 默认 ----
    env_url = os.environ.get("OLLAMA_URL", "").strip() or os.environ.get("OLLAMA_BASE_URL", "").strip()
    if env_url:
        cfg["ollama"]["url"] = env_url
    env_model = os.environ.get("VISION_MODEL", "").strip()
    if env_model:
        cfg["ollama"]["model"] = env_model
    env_key = os.environ.get("VISION_API_KEY", "").strip()
    env_base = os.environ.get("VISION_API_BASE_URL", "").strip()
    if env_key and env_base:
        # 注入合成云平台（env 驱动，需 key+base_url 成对），vision_client 经 cloud 通道走云端
        cfg["cloud"]["clouds"] = [{
            "name": "env",
            "base_url": env_base,
            "model": env_model or "qwen-vl-plus",
            "api_key": env_key,
        }]
        cfg["cloud"]["active"] = "env"
    elif env_key:
        # 只有 key 无 base_url：不注入（避免 key 被 POST 到占位主机），保持纯本地
        log.warning("VISION_API_KEY set but VISION_API_BASE_URL missing; staying local (env cloud requires both)")
    return cfg


def cloud_key_of(c: dict) -> str:
    """单个平台的 key：优先 <NAME>_API_KEY 环境变量，回退 config api_key。"""
    name = (c.get("name") or "").strip().upper()
    env = os.environ.get(f"{name}_API_KEY") if name else ""
    if env:
        return env
    return c.get("api_key", "") or ""


def active_cloud() -> dict | None:
    """当前激活云平台：cloud.active 匹配；未指定 active 时取第一个有 key 的平台。"""
    cloud_cfg = get().get("cloud", {})
    active = cloud_cfg.get("active", "")
    clouds = cloud_cfg.get("clouds", []) or []
    if not clouds:
        return None
    if active:
        for c in clouds:
            if c.get("name") == active:
                return c
        return None
    for c in clouds:
        if cloud_key_of(c):
            return c
    return None


def cloud_key() -> str:
    """当前激活平台的 API key（供 use_cloud 判断）。"""
    c = active_cloud()
    return cloud_key_of(c) if c else ""


def use_cloud() -> bool:
    """是否走云端：VISION_PROVIDER 强制 local/cloud；否则任一平台有 key 即云端。"""
    provider = os.environ.get("VISION_PROVIDER", "").strip()
    if provider == "local":
        return False
    if provider == "cloud":
        return True
    return bool(cloud_key())


def resolve_backend() -> dict:
    """归一化后端信息（env > config > 默认），供 mcp_server 调试/选后端。

    返回 {provider, active, model, url, api_key, base_url, precision}；
    provider 与 vision_client 实际路由同源（use_cloud）；model 云端时取云厂商模型。
    """
    cfg = get()
    o = cfg.get("ollama", {})
    ac = active_cloud()
    provider = "cloud" if use_cloud() else "local"
    model = o.get("model", "")
    if provider == "cloud" and ac:
        model = ac.get("model") or model
    return {
        "provider": provider,
        "active": (ac or {}).get("name", ""),
        "model": model,
        "url": o.get("url", ""),
        "api_key": cloud_key(),
        "base_url": (ac or {}).get("base_url", ""),
        "precision": o.get("precision", "fast"),
    }
