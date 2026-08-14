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
import logging
import os
import re
import threading
import httpx

import config_loader

log = logging.getLogger("vision_client")  # 引擎回退/模型缺失日志；proxy/MCP 进程 attach handler

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


# 大类 -> 允许的小类（从 config 读，这里仅为默认兜底；v2 扩到 16 类 + generic 纯兜底）
MAIN_SCENES = ("person", "animal", "plant", "food", "vehicle", "machine",
               "architecture", "document", "chart", "diagram", "map",
               "screenshot", "object", "meme", "scene", "unknown", "generic")


def _valid_mains() -> tuple:
    """有效大类单一事实源 = config scenes 的键（让 config 新增的类也能被分类）。"""
    return tuple(config_loader.get().get("scenes", {}).keys())


def _scenes() -> dict:
    return config_loader.get().get("scenes", {})


def _entry(prompt_key: str) -> dict:
    """取提示词条目，附上生效温度。"""
    cfg = config_loader.get()
    entry = cfg["prompts"].get(prompt_key, {"text": "", "temperature": None})
    temp = entry["temperature"] if entry.get("temperature") is not None else cfg["ollama"]["temperature"]
    return {"text": entry.get("text", ""), "temperature": temp}


def _mode_temperature(mode: str) -> float | None:
    """--mode 动态温度：查 config modes 表；未知/未配置返回 None（调用方回退提示词温度）。"""
    if not mode:
        return None
    try:
        return float(config_loader.get().get("modes", {}).get(mode))
    except (TypeError, ValueError):
        return None


def _active_cloud() -> dict | None:
    return config_loader.active_cloud()


def _cloud_key_of(c: dict) -> str:
    return config_loader.cloud_key_of(c)


def _cloud_key() -> str:
    return config_loader.cloud_key()


def _use_cloud() -> bool:
    return config_loader.use_cloud()


def _cloud_of(name: str) -> dict | None:
    """按名字找 cloud.clouds 里的厂商（v1.5 模型级云端路由）。"""
    for c in config_loader.get().get("cloud", {}).get("clouds", []):
        if c.get("name") == name:
            return c
    return None


def _post_cloud(b64: str, prompt: str, temperature: float, provider: str = "") -> str:
    """云端通道：按当前激活平台（provider 空）或指定厂商（v1.5 模型级）发 OpenAI 兼容 /chat/completions。"""
    c = _active_cloud()
    if provider:
        c = _cloud_of(provider)
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


def _post_b64(b64: str, prompt: str, temperature: float, model: str = "") -> str:
    """识别请求。model 非空（v1.5 模型级覆盖，router 值 "引擎:模型"）时优先于全局后端判断：
    models 表 type=cloud → 走云端该厂商；否则本地 Ollama 用该模型（覆盖全局 ollama.model）。"""
    cfg = config_loader.get()
    o = cfg["ollama"]
    mdef = cfg.get("models", {}).get(model, {}) if model else {}
    # 显式指定模型：cloud 类型 → 云端该厂商；ollama/未注册 → 本地该模型。空 model 用全局判断。
    use_cloud = (mdef.get("type") == "cloud") if model else _use_cloud()
    # 缓存只在 deep 档(3)启用：fast/standard 收益趋近于零，deep 多次调用才值得。
    use_cache = _cache_on()
    ac = _active_cloud()
    # 生效模型（缓存 key 用）：显式模型优先；云端时用 active 厂商 model；本地用全局 ollama.model
    eff_model = model or ((ac.get("model") if (ac and use_cloud) else "") or o["model"])
    if use_cache:
        # 缓存 key 含 model + temperature：换模型/改温度后不命中旧缓存（防拿过期结果）
        key = hashlib.sha256((eff_model + "|" + b64 + "|" + prompt + "|" + str(temperature)).encode()).hexdigest()
        with _cache_lock:
            if key in _cache:
                return _cache[key]
    if use_cloud:
        provider = mdef.get("provider", "") if model else ""
        text = _post_cloud(b64, prompt, temperature, provider=provider)
    else:
        # 本地路径：显式模型（v1.5 模型级，如 vlm:llava）或全局 ollama.model；绝不混入云端厂商 model（防 404）
        local_model = model or o["model"]
        with _OLLAMA_SEM:  # 并发上限 2，防本地单卡雪崩
            r = httpx.post(o["url"], json={"model": local_model, "prompt": prompt, "images": [b64],
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

    实测 2600×3200 图识别耗时 20.9s→5.0s（等比例缩放至 1280px，省 76%）。
    缩放对 OCR/细节影响有限，但对超长/超高截图（长网页、4K 截图）收益明显。
    用 PIL 解码→缩放→重编码。小图（边长 ≤ 约 1.43×上限）直接原样返回，不增加解码开销。
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
        if cand in _valid_mains():
            main = cand
    # 无标签时：从文本里挑第一个出现在有效大类里的词
    if main == "generic" and "generic" not in text:
        for tok in re.findall(r"[A-Za-z]+", text):
            if tok.lower() in _valid_mains():
                main = tok.lower()
                break

    # 小类：优先找「小类: X」标签
    ms = re.search(r"小类\s*[=:：]\s*(\w+)", text)
    if ms and ms.group(1) != "无":
        cand = ms.group(1).lower()
        if cand in all_subs or cand in _valid_mains():
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


def _grounding_enabled() -> bool:
    """模型无关：config ollama.grounding 控制是否启用 grounding（换不支持 bbox 的模型时设 false 跳过）。"""
    return config_loader.get().get("ollama", {}).get("grounding", True)


def _grounding(path_or_b64: str, prompt: str, temperature: float, model: str = "") -> str:
    """grounding 请求：计算图片尺寸 + _post_b64 + 追加【原图尺寸】。spatial()/locate() 共用。"""
    img_path = path_or_b64 if os.path.exists(path_or_b64) else ""
    size = _image_size(img_path) if img_path else ""
    text = _post_b64(_to_b64(path_or_b64), prompt, temperature, model=model)
    if size:
        text += f"\n【原图尺寸】{size}"
    return text


def locate(path_or_b64: str, query: str) -> str:
    """按 query 定位图中元素（grounding bbox）。复用 spatial 提示词注入查询。
    返回元素名 + bbox JSON + 原图尺寸。模型不支持 grounding 时返回明确提示。"""
    if not _grounding_enabled():
        return "视觉 grounding 已关闭（config ollama.grounding=false），无法输出边界框；如需定位请将 grounding 设为 true。"
    e = _entry("spatial")
    prompt = f"用户在图中查找：{query}\n\n{e['text']}"
    try:
        return _grounding(path_or_b64, prompt, e["temperature"])
    except Exception:
        return "定位失败（grounding 请求异常）"


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


def spatial(path_or_b64: str, model: str = "") -> str:
    """空间结构识别（grounding）：输出元素名 + bbox 坐标 + 图片尺寸。model 指定时用该模型。"""
    e = _entry("spatial")
    return _grounding(path_or_b64, e["text"], e["temperature"], model=model)


# ---- 视觉路由器 v1：按 scan 场景选引擎（当前 VLM 统一 qwen2.5vl，后续换专业模型只改路由表）----


def _route_engine(scene: str, sub: str) -> str:
    """路由表查引擎：scene.sub 精确优先 → scene 大类 → _default。"""
    table = config_loader.get().get("router", {})
    if f"{scene}.{sub}" in table:
        return table[f"{scene}.{sub}"]
    if scene in table:
        return table[scene]
    return table.get("_default", "vlm")


def _parse_route_value(value: str) -> tuple[str, str]:
    """路由值 "引擎" | "引擎:模型" → (engine, model)。模型空 = 用全局（场景-模型解耦，用户自行配置）。"""
    if not value:
        return "vlm", ""
    if ":" in value:
        engine, model = value.split(":", 1)
        return (engine.strip() or "vlm"), model.strip()
    return value.strip(), ""


def _prompt_call(path_or_b64: str, prompt_key: str, scene: str, sub: str, scan_desc: str, model: str = "") -> str:
    """引擎通用调用：取指定提示词条目，拼场景 header（与 zoom 同款结构），_post_b64 发送。

    供 _engine_table/_engine_gui 等提示词类引擎复用——接入专业引擎（rapid-table/OmniParser）
    时替换引擎函数体，本助手保留给其它提示词类引擎用。"""
    e = _entry(prompt_key)
    header = f"场景大类：{scene}"
    if sub:
        header += f"，小类：{sub}"
    if scan_desc.strip():
        header += f"\n第1次扫描的初步判断：\n{scan_desc.strip()}"
    prompt = f"{header}\n\n请基于以上信息，按以下要求处理：\n{e['text']}"
    return _post_b64(_to_b64(path_or_b64), prompt, e["temperature"], model=model)


def _engine_ocr(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "") -> str:
    """OCR 引擎（RapidOCR）：只提取文字。提取不足返回空串 → 调用方回退 vlm。"""
    text = ocr(path_or_b64)
    if len(text) >= 20:
        return f"[OCR 提取的文字（自动路由，未走视觉模型）]\n{text}"
    return ""


def _engine_vlm(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "") -> str:
    """VLM 引擎：走 zoom 按类提示词。model 非空时用指定模型（本地 ollama / 云端厂商）。"""
    return zoom(path_or_b64, scene, sub=sub, scan_desc=scan_desc, model=model)


def _engine_table(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "") -> str:
    """表格引擎：完整提取表格 → Markdown。

    当前实现：VLM + 表格提取提示词（extract_table）。接入专业引擎（rapid-table /
    PP-StructureV3）时替换本函数体，_ENGINES 与路由不用动；模型由用户按需配置（engine:model）。"""
    return _prompt_call(path_or_b64, "extract_table", scene, sub, scan_desc, model=model)


def _engine_gui(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "") -> str:
    """GUI 引擎：枚举界面可交互元素（VLM + extract_gui 提示词）。

    接入专业引擎（OmniParser / UI-TARS）时替换本函数体，_ENGINES 与路由不用动；
    模型由用户按需配置（engine:model）。"""
    return _prompt_call(path_or_b64, "extract_gui", scene, sub, scan_desc, model=model)


# 引擎注册表：后续换专业引擎 = 加函数进注册表 + 改路由表指向，analyze 不动
_ENGINES = {
    "ocr": _engine_ocr,
    "vlm": _engine_vlm,
    "table": _engine_table,  # 表格引擎（接 rapid-table 时换函数体）
    "gui": _engine_gui,      # 界面元素引擎（接 OmniParser 时换函数体）
}


def _run_engine(engine: str, path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "") -> str:
    """调引擎：未注册 / 异常返回空串（调用方回退 vlm，不报错）。回退记 warning 日志（兜底 + 可诊断）。"""
    fn = _ENGINES.get(engine)
    if fn is None:
        log.warning("router: 引擎 %r 未注册（scene=%s.%s），回退 vlm", engine, scene, sub)
        return ""
    try:
        return fn(path_or_b64, scene, sub, scan_desc, model=model)
    except Exception as e:
        log.warning("router: 引擎 %s 异常 %s: %s（scene=%s.%s model=%s），回退 vlm",
                    engine, type(e).__name__, e, scene, sub, model or "-")
        return ""


def analyze(path_or_b64: str, precision: str = "", mode: str = "") -> str:
    """按精度档位识别。统一入口，供 proxy / MCP 使用。
    - fast:     1 次 describe（单句描述）——快
    - standard: 2 次 scan + zoom（描述 + 按场景提取事实）
    - deep:     3 次 scan + zoom + guess（完整三次判定，含推测）
    precision 缺省时读 config.prompts.default（或 config ollama.precision）。
    mode 走 config modes 表动态覆盖 guess 温度（v2 --mode）。
    """
    cfg = config_loader.get()
    if not precision:
        precision = cfg.get("ollama", {}).get("precision", "fast")
    precision = (precision or "fast").lower()
    if precision == "fast":
        # fast 也走 scan：1 次调用即含描述+类别（scan 提示词自带描述要求），供 scene 推理
        desc, scene, sub = scan(path_or_b64)
        return f"【初步判断】{desc}\n【场景】{scene}"
    # standard / deep：先 scan 判场景，再按路由表选引擎（视觉路由器 v1.5）
    desc, scene, sub = scan(path_or_b64)
    # 路由：scene(.sub) → "引擎" | "引擎:模型"（用户自行配置，场景与模型解耦）。
    # 引擎未注册 / 异常 / 输出不足 / 模型缺失 → 回退全局 vlm（兜底 + log.warning）。
    engine, model = _parse_route_value(_route_engine(scene, sub))
    facts = _run_engine(engine, path_or_b64, scene, sub, desc, model=model)
    ocr_used = bool(facts and engine == "ocr")
    if not facts:
        model = ""  # 指定模型失败/缺失 → guess/spatial 也用全局模型
        facts = _run_engine("vlm", path_or_b64, scene, sub, desc) or "[识别失败（引擎异常），已回退]"
    if precision == "standard":
        tag = "[OCR]" if ocr_used else "[视觉]"
        return f"【初步判断】{desc}\n【场景】{scene}\n【细节({tag})】\n{facts}"
    # deep：再加 guess + 空间结构（grounding bbox），透传场景模型
    guess_out = guess(path_or_b64, context=facts, scene=scene, sub=sub, scan_desc=desc, mode=mode, model=model)
    # 模型无关：config ollama.grounding 控制是否启用 grounding（换不支持 bbox 的模型时设 false 跳过）
    spatial_out = ""
    if config_loader.get().get("ollama", {}).get("grounding", True):
        try:
            spatial_out = spatial(path_or_b64, model=model)
        except Exception:
            spatial_out = ""  # grounding 失败不影响主体
    tag = "[OCR]" if ocr_used else "[视觉]"
    base = f"【初步判断】{desc}\n【场景】{scene}\n【细节({tag})】\n{facts}\n\n【推测】\n{guess_out}"
    if scene == "unknown":
        base += "\n【结论】模型判定无法归类，以下信息可能不完整，引用时请降级置信度。"
    if spatial_out:
        base += f"\n\n【空间结构】\n{spatial_out}"
    return base


def scan(path_or_b64: str) -> tuple[str, str, str]:
    """第1次：一句话描述 + 判断大类+小类。返回 (描述, 大类, 小类)。"""
    e = _entry("scan")
    text = _post_b64(_to_b64(path_or_b64), e["text"], e["temperature"])
    main, sub = _parse_scene(text)
    if not sub:
        # 接线 default_sub：scan 未给出小类时用该大类的默认小类（document 默认 report 不触发 OCR 路由）
        sub = _scenes().get(main, {}).get("default_sub", "") or ""
    return text, main, sub


def zoom(path_or_b64: str, scene: str = "generic", sub: str = "", scan_desc: str = "", model: str = "") -> str:
    """第2次：按大类选 zoom 清单，小类作为上下文注入。

    scene 能否 zoom 取决于有没有 zoom_<scene> 提示词；缺失（如 config 新增类漏配）回退 generic，
    避免发「只有 header、正文空」的请求。model 非空时用指定模型（v1.5 场景模型）。"""
    if f"zoom_{scene}" not in config_loader.get()["prompts"]:
        scene = "generic"
    e = _entry(f"zoom_{scene}")
    prompt = e["text"]
    header = f"场景大类：{scene}"
    if sub:
        header += f"，小类：{sub}"
    if scan_desc.strip():
        header += f"\n第1次扫描的初步判断：\n{scan_desc.strip()}"
    prompt = f"{header}\n\n请基于以上信息，按以下清单逐项提取更精确的事实：\n{e['text']}"
    return _post_b64(_to_b64(path_or_b64), prompt, e["temperature"], model=model)


def guess(path_or_b64: str, context: str = "", scene: str = "", sub: str = "",
          scan_desc: str = "", mode: str = "", model: str = "") -> str:
    """第3次：基于 scan 描述 + zoom 事实 + 场景，大胆推测。

    mode 走 config modes 表动态覆盖 guess 温度（--mode / describe_image mode）；缺省用提示词温度。
    model 非空时用指定模型（v1.5 场景模型）。"""
    e = _entry("guess")
    temp = _mode_temperature(mode)
    if temp is None:
        temp = e["temperature"]
    prompt = e["text"]
    if scene:
        prompt += f"\n场景大类：{scene}"
    if sub:
        prompt += f"，小类：{sub}"
    if scan_desc.strip():
        prompt += f"\n\n第1次扫描的初步判断：\n{scan_desc.strip()}"
    if context.strip():
        prompt += f"\n\n已提取的事实特征：\n{context.strip()}"
    return _post_b64(_to_b64(path_or_b64), prompt, temp, model=model)
