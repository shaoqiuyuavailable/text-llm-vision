#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-vision 控制面板服务。

参考 visual-ds 的 vscode-ext 控制面板，改为本地 Web 控制台：
- 复用 visual-ds 的 config.json / state / Ollama 环境
- 通过 /api/* 读写配置，不重复实现视觉逻辑

用法：
  python panel/server.py [--port 8790]
然后浏览器打开 http://127.0.0.1:8790
"""
import argparse
import os
import sys
import time
from pathlib import Path

# 让控制面板能直接复用 visual-ds 的 control_api / toggle / config_loader
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VISUAL_DS_CANDIDATES = [
    r"F:\code of PY\visual-ds",
    str(Path.home() / ".claude" / "vision-eyes"),
]
for p in VISUAL_DS_CANDIDATES:
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

# dsh-vision 自己的 python 目录也加入，便于读取本地 config 或将来扩展
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

import control_api  # noqa: E402
import config_loader  # noqa: E402

app = FastAPI(title="dsh-vision Control Panel", version="0.1.0")
STARTED_AT = time.time()

INDEX = HERE / "index.html"


def _proxy_info() -> dict:
    return {
        "status": "ok",
        "version": "dsh-vision-panel",
        "pid": os.getpid(),
        "uptime": round(time.time() - STARTED_AT, 1),
        "code_mtime": os.path.getmtime(__file__),
    }


@app.get("/")
async def index():
    return FileResponse(INDEX)


@app.get("/api/status")
async def api_status():
    try:
        return JSONResponse(control_api.get_status(_proxy_info()))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/level")
async def api_level(request: Request):
    try:
        body = await request.json()
        return JSONResponse(control_api.set_level(body.get("level"), _proxy_info()))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/backend")
async def api_backend(request: Request):
    try:
        body = await request.json()
        return JSONResponse(control_api.set_backend(
            body.get("kind"),
            body.get("port"),
            body.get("provider"),
            _proxy_info(),
        ))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/config")
async def api_config(request: Request):
    try:
        body = await request.json()
        patch = body.get("patch", body)
        return JSONResponse(control_api.set_config(patch, _proxy_info()))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/mount")
async def api_mount():
    """检查 dsh-vision 插件挂载相关的静态文件是否存在。"""
    cordis = ROOT / "cordis.yml"
    plugin = ROOT / "src" / "index.ts"
    cli = PYTHON_DIR / "vision_cli.py"
    cfg = Path(config_loader.CONFIG_PATH)
    return {
        "cordis_yml": cordis.exists(),
        "plugin_ts": plugin.exists(),
        "vision_cli": cli.exists(),
        "config_path": str(cfg),
        "config_exists": cfg.exists(),
        "visual_ds_importable": _importable("vision_client"),
    }


def _importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    import uvicorn
    print(f"dsh-vision control panel: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
