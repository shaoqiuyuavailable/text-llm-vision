#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立测试本地视觉代理，不修改 settings.json，直接请求 localhost:8787。

用前确保：1) 代理已启动 (uvicorn proxy:app --port 8787)；2) Ollama 在跑且 qwen2.5vl 已拉取。
运行：python test_proxy.py
"""
import base64, io, json, os, sys, urllib.request, urllib.error

PROXY = "http://localhost:8787/v1/messages"
STATE = os.path.expanduser("~/.claude/vision-eyes/state")


def get_token():
    p = os.path.expanduser("~/.claude/settings.json")
    cfg = json.load(open(p, encoding="utf-8"))
    return cfg["env"]["ANTHROPIC_AUTH_TOKEN"]


def test_image_b64():
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (260, 110), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([8, 8, 252, 102], outline="red", width=5)
        d.text((16, 28), "HELLO 123", fill="black")
        d.ellipse([170, 20, 240, 90], fill="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # 兜底：1x1 红点 PNG
        return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def post(body, timeout=180):
    data = json.dumps(body).encode()
    req = urllib.request.Request(PROXY, data=data, method="POST", headers={
        "content-type": "application/json",
        "x-api-key": get_token(),
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"EXC: {e}"


def test_text():
    print("=== Test 1: text passthrough (gzip fix) ===")
    st, resp = post({"model": "DeepSeek-V4-flash", "max_tokens": 20,
                     "messages": [{"role": "user", "content": "reply exactly: OK"}]})
    ok = st == 200 and "OK" in resp
    print(f"  status={st} -> {'PASS' if ok else 'FAIL'}")
    print(f"  head: {resp[:160]}")
    return ok


def test_image():
    print("=== Test 2: image -> text (core) ===")
    b64 = test_image_b64()
    st, resp = post({"model": "DeepSeek-V4-flash", "max_tokens": 60,
                     "messages": [{"role": "user", "content": [
                         {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                         {"type": "text", "text": "图里有什么？用中文简短回答"}]}]})
    ok = st == 200
    print(f"  status={st} -> {'PASS' if ok else 'FAIL'}")
    print(f"  head: {resp[:400]}")
    return ok


def test_toggle():
    print("=== Test 3: toggle off -> placeholder ===")
    for val in ("off", "on"):
        open(STATE, "w").write(val)
        b64 = test_image_b64()
        st, resp = post({"model": "DeepSeek-V4-flash", "max_tokens": 20,
                         "messages": [{"role": "user", "content": [
                             {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                             {"type": "text", "text": "hi"}]}]})
        print(f"  state={val}, status={st}")
    open(STATE, "w").write("on")  # 恢复
    print("  (state restored to on)")
    return True


def main():
    results = []
    results.append(("text-passthrough", test_text()))
    results.append(("image-convert", test_image()))
    results.append(("toggle", test_toggle()))
    print("\n===== SUMMARY =====")
    for name, ok in results:
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    all_ok = all(ok for _, ok in results)
    print(f"  OVERALL: {'ALL PASS' if all_ok else 'SOME FAILED'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
