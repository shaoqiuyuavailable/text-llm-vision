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
import threading
import httpx

import config_loader

_cache = {}
MAX_CACHE = 100  # 缓存上限：防内存无限膨胀，超出清最旧（FIFO）
_cache_lock = threading.Lock()  # 缓存读写锁：防并发重复请求 Ollama
# Ollama 并发限制：本地单卡 8B 模型无法处理高并发，多个消息同时识别会雪崩
# （请求排队堵死/显存溢出）。2 个并发足够防雪崩又允许 fast 档轻并发贴图。
_OLLAMA_SEM = threading.BoundedSemaphore(2)

STATE_FILE = os.path.expanduser("~/.claude/vision-eyes/state")


def clear_cache():
    """清空识别缓存。off 档时调用，释放内存。"""
    with _cache_lock:
        _cache.clear()


def _cache_on() -> bool:
    """缓存只在 deep 档(3)启用：fast/standard 各 1-2 次调用，缓存收益趋近于零；
    只有 deep（scan+zoom+guess+spatial，3-4 次调用）在「同图重试/重复粘贴」时
    缓存省时才值得。档位即开关——读 state 文件，与 proxy.vision_level() 同源。"""
    try:
        return open(STATE_FILE).read().strip() == "3"
    except OSError:
        return False


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


def _clouds() -> list:
    """所有已配置的云端平台（config.cloud.clouds）。"""
    return config_loader.get().get("cloud", {}).get("clouds", []) or []


def _active_cloud() -> dict | None:
    """按 cloud.active 选当前平台；未指定 active 时取第一个有 key 的。"""
    cloud_cfg = config_loader.get().get("cloud", {})
    active = cloud_cfg.get("active", "")
    clouds = _clouds()
    if not clouds:
        return None
    if active:
        for c in clouds:
            if c.get("name") == active:
                return c
        return None  # active 指定的平台不存在
    # 未指定 active：取第一个配了 key 的（key 从环境变量或 config 读）
    for c in clouds:
        if _cloud_key_of(c):
            return c
    return None


def _cloud_key_of(c: dict) -> str:
    """单个平台的 key：优先环境变量（name 大写 + _API_KEY），回退 config 的 api_key。"""
    name = (c.get("name") or "").strip().upper()
    env = os.environ.get(f"{name}_API_KEY") if name else ""
    if env:
        return env
    return c.get("api_key", "") or ""


def _cloud_key() -> str:
    """当前激活平台的 API key（供 use_cloud 判断：任一平台有 key 即走云端）。"""
    c = _active_cloud()
    return _cloud_key_of(c) if c else ""


def _use_cloud() -> bool:
    """是否走云端：任一平台有 key 默认云端；VISION_PROVIDER 可强制 local/cloud。"""
    provider = os.environ.get("VISION_PROVIDER", "").strip()
    if provider == "local":
        return False
    if provider == "cloud":
        return True
    return bool(_cloud_key())


def _post_cloud(b64: str, prompt: str, temperature: float) -> str:
    """云端通道：按当前激活平台发 OpenAI 兼容 /chat/completions。"""
    c = _active_cloud()
    if not c:
        raise RuntimeError("no active cloud platform configured")
    base = (c.get("base_url") or "").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_cloud_key_of(c)}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": c.get("model") or "qwen-vl-plus",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "temperature": temperature,
        "max_tokens": 2000,
    }
    r = httpx.post(url, json=payload, headers=headers, timeout=120, trust_env=False)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


def _post_b64(b64: str, prompt: str, temperature: float) -> str:
    cfg = config_loader.get()
    o = cfg["ollama"]
    use_cloud = _use_cloud()  # 任一平台配了 key 走云端，否则纯本地（VISION_PROVIDER 可强制覆盖）
    # 缓存只在 deep 档(3)启用：fast/standard 收益趋近于零，deep 多次调用才值得。
    use_cache = _cache_on()
    ac = _active_cloud()
    model = (ac.get("model") if ac else "") or o["model"]  # key 区分本地/云端
    if use_cache:
        # 缓存 key 含 model + temperature：换模型/改温度后不命中旧缓存（防拿过期结果）
        key = hashlib.sha256((model + "|" + b64 + "|" + prompt + "|" + str(temperature)).encode()).hexdigest()
        with _cache_lock:
            if key in _cache:
                return _cache[key]
    if use_cloud:
        text = _post_cloud(b64, prompt, temperature)
    else:
        with _OLLAMA_SEM:  # 并发上限 2，防本地单卡雪崩
            r = httpx.post(o["url"], json={"model": o["model"], "prompt": prompt, "images": [b64],
                                           "stream": False, "options": {"temperature": temperature,
                                                                        "top_p": o["top_p"]}},
                           timeout=120, trust_env=False)
        r.raise_for_status()
        text = r.json()["response"].strip()
    if use_cache:
        with _cache_lock:
            _cache[key] = text
            while len(_cache) > MAX_CACHE:  # 超上限清最旧（FIFO）
                _cache.pop(next(iter(_cache)))
    return text


_RAPIDOCR = None  # 惰性加载：RapidOCR 单例（首次加载模型约 1-2s）


def _get_ocr_engine():
    """惰性初始化 RapidOCR（纯 Python ONNX，跨平台，支持中文）。首次调用加载模型。"""
    global _RAPIDOCR
    if _RAPIDOCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _RAPIDOCR = RapidOCR()
    return _RAPIDOCR


def ocr(path_or_b64: str, prompt: str = "") -> str:
    """RapidOCR 提取图片文字（本地 ONNX，离线免费，支持中英文）。

    仅提取文字本身（不含视觉理解），适合纯文字截图（聊天/代码/表格/文档页）。
    prompt 保留参数以兼容 describe 签名；OCR 不使用 prompt。
    失败（未装 rapidocr / 非图片）返回空串——调用方回退视觉模型。
    """
    import base64 as _b64
    import io as _io
    try:
        if os.path.exists(path_or_b64):
            img_path = path_or_b64
        else:
            # base64 → 临时文件
            from PIL import Image
            img = Image.open(_io.BytesIO(_b64.b64decode(path_or_b64))).convert("RGB")
            tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ocr_tmp.png")
            img.save(tmp, "PNG")
            img_path = tmp
        engine = _get_ocr_engine()
        result, _ = engine(img_path)
        if img_path != path_or_b64:
            try:
                os.remove(img_path)
            except OSError:
                pass
        if not result:
            return ""
        # 按从上到下顺序拼接文字（每行用换行分隔）
        lines = [item[1].strip() for item in result if item and item[1]]
        return "\n".join(lines).strip()
    except Exception:
        return ""  # OCR 不可用 → 回退


MAX_EDGE = 1280  # 缩放上限：图片最大边长超过此值则等比例缩到该值（省识别耗时/Tokens）


def _downscale_b64(b64: str) -> str:
    """等比例缩小图片（最大边长 ≤ MAX_EDGE），减小 Ollama 识别耗时。

    实测 453→2000px 耗时 3.8s→10.6s；4K 图会更慢。缩放对 OCR/细节影响有限，
    但对超长/超高截图（长网页、4K 截图）收益明显。用 PIL 解码→缩放→重编码。
    小图（边长 ≤ 约 1.43×上限）直接原样返回，不增加解码开销。
    """
    if not b64:
        return b64
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        w, h = img.size
        longest = max(w, h)
        if longest <= MAX_EDGE:
            return b64  # 小图：不缩放，零开销
        ratio = MAX_EDGE / longest
        new_size = (max(1, round(w * ratio)), max(1, round(h * ratio)))
        img = img.convert("RGB")  # 统一模式，避免重编码异常
        img.thumbnail(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return b64  # 解码/缩放失败 → 原样（不阻塞识别）


def _to_b64(path_or_b64: str) -> str:
    if os.path.exists(path_or_b64):
        with open(path_or_b64, "rb") as f:
            return _downscale_b64(base64.b64encode(f.read()).decode())
    return _downscale_b64(path_or_b64)


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


def _image_size(path: str) -> str:
    """读图片尺寸（宽x高）。用于 grounding 输出里附原始尺寸，供主模型换算空间关系。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            return f"{w}x{h}"
    except Exception:
        return ""


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


def spatial(path_or_b64: str) -> str:
    """空间结构识别（grounding）：输出元素名 + bbox 坐标 + 图片尺寸。
    解决散文描述丢失空间坐标/拓扑的问题——主模型基于结构化坐标推理布局，
    而非脑补。bbox 为模型内部网格坐标，配合原图尺寸由主模型换算相对位置。"""
    e = _entry("spatial")
    img_path = path_or_b64 if os.path.exists(path_or_b64) else ""
    size = _image_size(img_path) if img_path else ""
    text = _post_b64(_to_b64(path_or_b64), e["text"], e["temperature"])
    if size:
        text += f"\n【原图尺寸】{size}"
    return text


def analyze(path_or_b64: str, precision: str = "") -> str:
    """按精度档位识别。统一入口，供 proxy / MCP 使用。
    - fast:     1 次 describe（单句描述）——快
    - standard: 2 次 scan + zoom（描述 + 按场景提取事实）
    - deep:     3 次 scan + zoom + guess（完整三次判定，含推测）
    precision 缺省时读 config.prompts.default（或 config ollama.precision）。
    """
    cfg = config_loader.get()
    if not precision:
        precision = cfg.get("ollama", {}).get("precision", "fast")
    precision = (precision or "fast").lower()
    if precision == "fast":
        # fast 也走 scan：1 次调用即含描述+类别（scan 提示词自带描述要求），供 scene 推理
        desc, scene, sub = scan(path_or_b64)
        return f"【初步判断】{desc}\n【场景】{scene}"
    # standard / deep：先 scan 判场景，再 zoom
    desc, scene, sub = scan(path_or_b64)
    # 自动路由：纯文字场景（document.chat/code）优先用 OCR 提取文字替代视觉 zoom。
    # 严格限定：OCR 必须提取到足够文字（≥20字符）才用，否则回退视觉 zoom（防含图/空白误判）。
    facts = None
    ocr_used = False
    if scene == "document" and sub in ("chat", "code"):
        ocr_text = ocr(path_or_b64)
        if len(ocr_text) >= 20:
            facts = f"[OCR 提取的文字（自动路由，未走视觉模型）]\n{ocr_text}"
            ocr_used = True
    if facts is None:
        facts = zoom(path_or_b64, scene, sub=sub, scan_desc=desc)
    if precision == "standard":
        tag = "[OCR]" if ocr_used else "[视觉]"
        return f"【初步判断】{desc}\n【场景】{scene}\n【细节({tag})】\n{facts}"
    # deep：再加 guess + 空间结构（grounding bbox）
    guess_out = guess(path_or_b64, context=facts, scene=scene, sub=sub, scan_desc=desc)
    # 模型无关：config ollama.grounding 控制是否启用 grounding（换不支持 bbox 的模型时设 false 跳过）
    spatial_out = ""
    if config_loader.get().get("ollama", {}).get("grounding", True):
        try:
            spatial_out = spatial(path_or_b64)
        except Exception:
            spatial_out = ""  # grounding 失败不影响主体
    tag = "[OCR]" if ocr_used else "[视觉]"
    base = f"【初步判断】{desc}\n【场景】{scene}\n【细节({tag})】\n{facts}\n\n【推测】\n{guess_out}"
    if spatial_out:
        base += f"\n\n【空间结构】\n{spatial_out}"
    return base


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
