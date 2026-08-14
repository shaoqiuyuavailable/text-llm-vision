#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""控制 API 纯逻辑：VS Code 插件通过 proxy /api/* 调用本模块。

只读写 config.json / state / ollama，全部复用 toggle.py 与 config_loader 的
既有模块级函数（不重写）。返回 dict（JSON 可序列化），不 print。
供 proxy.py 路由调用；也可独立 import 单测。
"""
import json
import os
import time

import config_loader
import toggle

STATE = toggle.STATE  # ~/.claude/vision-eyes/state
ALLOWED_CONFIG_KEYS = {"upstream", "ollama"}
OLLAMA_NUM_KEYS = ("temperature", "top_p")

# ollama 服务状态缓存：get_status 被插件每 5s 轮询，若每次都 spawn `ollama list`
# 频繁启动子进程（虽已 CREATE_NO_WINDOW 不弹窗）也是浪费。缓存 15s。
OLLAMA_CACHE_TTL = 15
_ollama_cache = {"ts": 0.0, "data": None}


def _read_level() -> int:
    """读档位（0-3），损坏/缺失回退 1。与 proxy.vision_level 同语义，但本地独立实现避免循环 import。"""
    try:
        raw = open(STATE, encoding="utf-8").read().strip()
        if raw.isdigit():
            lv = int(raw)
            return lv if 0 <= lv <= 3 else 1
        return 1 if raw != "off" else 0  # 旧 on/off 格式
    except FileNotFoundError:
        return 1
    except Exception:
        return 1


def _ollama_http_probe() -> dict:
    """HTTP 探测 ollama（GET {base}/api/tags），不依赖 ollama CLI——Docker 容器内无 CLI。

    从 config ollama.url（含 /api/generate）推导 base，顺带避免 spawn 子进程。"""
    import urllib.request
    url = (config_loader.get().get("ollama", {}) or {}).get("url", "")
    base = url.rsplit("/api/", 1)[0] if "/api/" in url else url
    if not base:
        return {"running": False, "model": ""}
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as r:
            if r.status != 200:
                return {"running": False, "model": ""}
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return {"running": True, "model": models[0] if models else ""}
    except Exception:
        return {"running": False, "model": ""}


def _ollama_service() -> dict:
    """ollama 服务状态（HTTP 探测 + 15s 缓存）。不 spawn 子进程，容器内也能用。"""
    global _ollama_cache
    now = time.time()
    if now - _ollama_cache["ts"] < OLLAMA_CACHE_TTL:
        return _ollama_cache["data"]
    result = _ollama_http_probe()
    _ollama_cache = {"ts": now, "data": result}
    return result


def _cloud_list(cfg) -> list:
    out = []
    for n in toggle._cloud_names(cfg):
        info = toggle._cloud_info(cfg, n)
        out.append({
            "name": n,
            "model": info.get("model", ""),
            "base_url": info.get("base_url", ""),
            "has_key": toggle._has_key(cfg, n),
        })
    return out


def get_status(proxy_info=None) -> dict:
    """合并状态：档位/后端/端口/上游/ollama 配置/云端厂商/proxy 健康/ollama 服务。"""
    cfg = config_loader.get()
    active = (cfg.get("cloud", {}) or {}).get("active", "")
    ollama = cfg.get("ollama", {}) or {}
    return {
        "level": _read_level(),
        "backend": "cloud" if active else "local",
        "active_provider": active,
        "port": cfg.get("port", 8787),
        "upstream": cfg.get("upstream", ""),
        "ollama": {
            "url": ollama.get("url", ""),
            "model": ollama.get("model", ""),
            "temperature": ollama.get("temperature"),
            "top_p": ollama.get("top_p"),
        },
        "cloud": _cloud_list(cfg),
        "proxy": proxy_info,
        "ollama_service": _ollama_service(),
    }


def set_level(level, proxy_info=None) -> dict:
    """设档位；0 → ollama stop 释放显存。返回最新状态。"""
    try:
        lv = int(level)
    except (TypeError, ValueError):
        raise ValueError(f"level must be 0-3, got {level!r}")
    lv = lv if 0 <= lv <= 3 else 1
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        f.write(str(lv))
    if lv == 0:
        toggle._unload_model()
    return get_status(proxy_info)


def set_backend(kind, port=None, provider=None, proxy_info=None) -> dict:
    """切后端 local/cloud（可选改端口/选厂商）。改端口 → 标记需重启。返回最新状态。"""
    kind = (kind or "").strip().lower()
    if kind not in ("local", "cloud"):
        raise ValueError("kind must be local|cloud")
    cfg = toggle._read_cfg()
    needs_restart = False
    if kind == "local":
        toggle._set_active(cfg, "")
        if port is not None:
            try:
                p = int(port)
            except (TypeError, ValueError):
                raise ValueError(f"invalid port: {port!r}")
            if not (1 <= p <= 65535):
                raise ValueError(f"invalid port: {port!r}")
            if p != cfg.get("port"):
                needs_restart = True
            cfg["port"] = p
            toggle._write_cfg(cfg)
    else:  # cloud
        names = toggle._cloud_names(cfg)
        target = provider or cfg.get("cloud", {}).get("active", "") or (names[0] if names else "")
        if not target:
            raise ValueError("no cloud provider configured (edit config.json cloud.clouds)")
        if target not in names:
            raise ValueError(f"unknown provider: {target}")
        toggle._set_active(cfg, target)
    st = get_status(proxy_info)
    if needs_restart:
        st["port_changed_requires_restart"] = True
    return st


def set_config(patch, proxy_info=None) -> dict:
    """白名单改配置：upstream / ollama.temperature / ollama.top_p。拒绝其它键。返回最新状态。"""
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    unknown = set(patch) - ALLOWED_CONFIG_KEYS
    if unknown:
        raise ValueError(f"not allowed: {sorted(unknown)}")
    cfg = toggle._read_cfg()
    if "upstream" in patch:
        cfg["upstream"] = str(patch["upstream"]).strip()
    o = patch.get("ollama") or {}
    if isinstance(o, dict):
        bad = set(o) - set(OLLAMA_NUM_KEYS)
        if bad:
            raise ValueError(f"not allowed under ollama: {sorted(bad)}")
        cfg.setdefault("ollama", {})
        for k in OLLAMA_NUM_KEYS:
            if k in o:
                try:
                    cfg["ollama"][k] = float(o[k])
                except (TypeError, ValueError):
                    raise ValueError(f"ollama.{k} must be numeric, got {o[k]!r}")
    toggle._write_cfg(cfg)
    return get_status(proxy_info)
