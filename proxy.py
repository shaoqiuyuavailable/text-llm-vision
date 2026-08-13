import os, httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

import vision_client

# 本代理只服务 DeepSeek（纯文本模型）：通过 CC Switch 让 DeepSeek 的 ANTHROPIC_BASE_URL 指向本代理。
# 分类器请求也必须能正常通过：本代理只在 body 里确实含 image 块时才做图片→文字转换，
# 其它一切请求（含分类器）原样透传，绝不影响转发。
UPSTREAM = "https://api.deepseek.com/anthropic"
STATE = os.path.expanduser("~/.claude/vision-eyes/state")

_HOP = {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}


def vision_on() -> bool:
    try:
        return open(STATE).read().strip() != "off"
    except Exception:
        return True  # 默认开


def fwd_headers(request: Request) -> dict:
    return {k: v for k, v in request.headers.items() if k.lower() not in _HOP}


def _forward(resp, client) -> StreamingResponse:
    headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding")
    }
    return StreamingResponse(
        resp.aiter_bytes(),
        status_code=resp.status_code,
        headers=headers,
        background=BackgroundTask(client.aclose),
    )


def _convert_images(body: dict) -> dict:
    """把 body 里 messages 中的 image 块转成文字。返回 (新body, 是否发生了转换)。
    任何异常都抛给调用方兜底，这里只做纯转换。"""
    changed = False
    n = 0
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        has_image = any(isinstance(b, dict) and b.get("type") == "image" for b in content)
        if not has_image:
            continue
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                n += 1
                if vision_on():
                    try:
                        b64 = block.get("source", {}).get("data", "")
                        desc = vision_client.describe(b64)
                        out.append({"type": "text",
                                    "text": f"[用户粘贴的图片{n}，已转文字]\n{desc}"})
                    except Exception:
                        # 图片处理失败不影响转发：用占位文字兜底
                        out.append({"type": "text", "text": f"[图片{n}（识别失败，请重试）]"})
                else:
                    out.append({"type": "text", "text": f"[图片{n}（视觉已关闭，未识别）]"})
            else:
                out.append(block)
        msg["content"] = out
        changed = True
    return body, changed


app = FastAPI()


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    # 只有含 image 块才转换；转换全程 try 兜底，异常则原样转发
    try:
        body, changed = _convert_images(body)
    except Exception:
        changed = False  # 解析失败 → 原样转发
    client = httpx.AsyncClient(timeout=None, trust_env=False)
    req = client.build_request("POST", f"{UPSTREAM}/v1/messages", headers=fwd_headers(request), json=body)
    resp = await client.send(req, stream=True)
    return _forward(resp, client)


@app.post("/identify")
async def identify(request: Request):
    """本地识别接口：供 PreToolUse hook 调用，识别图片路径并返回描述。
    body: {"path": "图片文件路径"} -> {"desc": "识别描述", "main": ..., "sub": ...}
    """
    try:
        body = await request.json()
        img_path = body.get("path", "")
        if not img_path or not os.path.exists(img_path):
            return JSONResponse({"error": "bad path"}, status_code=400)
        # 描述图片内容（给模型看的）。只调一次，避免慢。
        desc = vision_client.describe(img_path)
        return JSONResponse({"desc": desc})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.api_route("/{path:path}", methods=["POST", "GET"])
async def passthrough(request: Request, path: str):
    # 非 /v1/messages 的所有请求（分类器、count_tokens 等）：原样透传，绝不干预
    client = httpx.AsyncClient(timeout=None, trust_env=False)
    req = client.build_request(request.method, f"{UPSTREAM}/{path}",
                               headers=fwd_headers(request), content=await request.body())
    resp = await client.send(req, stream=True)
    return _forward(resp, client)
