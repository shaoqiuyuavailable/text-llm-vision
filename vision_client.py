#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一视觉识别客户端：三次判定 + 混合方案（大类判定 + 组合分支兜底）。

    scan(path)                 -> (描述, 大类, 小类, 聚焦点)  第1次：描述+场景+聚焦点
    zoom(path, scene, sub)     -> 事实提取          第2次：按大类选清单，小类注入上下文
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


def _sanitize(s: str) -> str:
    """清洗文本中的孤立代理字符：Ollama 等返回无效 UTF-8 字节时 httpx 以 surrogateescape
    解码出孤立代理（\\udxxx），后续 json.dumps / HTTP 响应再编码必崩（UnicodeEncodeError）。
    统一替换掉（乱码字节→'?'），不让它传播到 MCP/proxy/CLI 任何输出路径。"""
    if not s:
        return s
    return s.encode("utf-8", "replace").decode("utf-8")


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
    return _sanitize(data["choices"][0]["message"]["content"].strip())


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
        # encode 用 replace 防任意输入（如 GBK 乱码遗留的孤立代理）在 key 生成处崩
        key = hashlib.sha256((eff_model + "|" + b64 + "|" + prompt + "|" + str(temperature))
                             .encode("utf-8", "replace")).hexdigest()
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
        text = _sanitize(r.json()["response"].strip())
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


def _parse_scene(text: str) -> tuple[str, str, list[tuple[str, str]]]:
    """从 scan 输出解析 (大类, 小类, 聚焦点列表)。

    大类为候选列表/回显（含「|」，模型在多个类间未定）时，用聚焦点[0]
    （视觉占比最大，模型自己排的序）当主类；聚焦点缺失（混合图常见）→ 候选
    第 1 项当主类、其余降级为聚焦点（保序双分支），绝不掉 generic。
    >3 个候选 = 整表抄回（回显）→ 保持 generic 兜底。"""
    main, sub = "generic", ""
    all_subs = [s for d in _scenes().values() for s in d.get("sub", [])]
    echo = False

    # 大类：候选列表/回显（含「|」，模型并列多个类）→ echo=True（稍后聚焦点兜底）。
    # 括号注解（person(real_single)）不是回显——提取括号里的合法小类。
    m = re.search(r"大类\s*[=:：]\s*(.+)", text)
    if m:
        val = m.group(1).strip()
        if "|" in val:
            echo = True
        else:
            cand = re.match(r"(\w+)", val)
            if cand and cand.group(1).lower() in _valid_mains():
                main = cand.group(1).lower()
                m2 = re.search(r"\((\w+)\)", val)
                if m2 and m2.group(1).lower() in all_subs:
                    sub = m2.group(1).lower()
    # 无标签兜底（回显时不走，避免任意捡词）
    if not echo and main == "generic" and "generic" not in text:
        for tok in re.findall(r"[A-Za-z]+", text):
            if tok.lower() in _valid_mains():
                main = tok.lower()
                break

    # 小类（回显的候选列表不解析，避免捡到乱填值；原始值另存供候选配对）
    sub_raw = ""
    if not echo:
        ms = re.search(r"小类\s*[=:：]\s*(\w+)", text)
        if ms and ms.group(1) != "无":
            cand = ms.group(1).lower()
            if cand in all_subs or cand in _valid_mains():
                sub = cand
    ms = re.search(r"小类\s*[=:：]\s*(.+)", text)
    if ms:
        sub_raw = ms.group(1).strip()
    if not sub and not echo:
        allowed = _scenes().get(main, {}).get("sub", [])
        for tok in re.findall(r"[A-Za-z]+", text):
            if tok.lower() in allowed:
                sub = tok.lower()
                break
    # 大类没有小类时强制清空
    if not _scenes().get(main, {}).get("sub"):
        sub = ""

    # 聚焦点：行级锚定防描述句误命中；缺失/「无」→ 空列表。
    focus = []
    for line in text.splitlines():
        m = re.search(r"聚焦点\s*[=:：]\s*(.+)", line)
        if m:
            focus = _parse_scene_list(m.group(1))

    # 大类候选列表（person|vehicle 未定）→ 用聚焦点[0]（视觉占比最大）当主类。
    # 聚焦点缺失（模型全无头绪，混合图常见）→ 候选列表第 1 项当主类、其余降级为
    # 聚焦点（位置配对小类），保证混合图仍走双分支——绝不掉成 generic（信息全丢）。
    if echo:
        if focus and focus[0][0] not in ("generic", "unknown"):
            main, sub = focus[0]
            focus = focus[1:]
        else:
            cands = _parse_candidates(val)
            subs = [t.strip().lower() for t in sub_raw.split("|") if t.strip()] if "|" in sub_raw else []
            # 候选 ≤3 才当真实的「骑墙」（混合图人+飞机）；>3 是模型把类别表整段抄回（回显），保持 generic
            if 0 < len(cands) <= 3:
                # 先按位置配对小类（基于未过滤顺序），再丢弃 unknown/generic 兜底项：
                # unknown 是「无法判断」桶，混在真实候选里是回显残留，真实主体优先
                paired = [(nm, subs[i] if i < len(subs) and subs[i] in _scenes().get(nm, {}).get("sub", []) else sm)
                          for i, (nm, sm) in enumerate(cands)]
                paired = [(nm, sm) for nm, sm in paired if nm not in ("unknown", "generic")]
                if paired:
                    main, sub = paired[0]
                    focus = paired[1:]
        if not _scenes().get(main, {}).get("sub"):
            sub = ""
    return main, sub, focus


def _parse_scene_list(val: str) -> list[tuple[str, str]]:
    """解析「大类名」/「大类.小类」/「小类名」逗号列表 → [(main, sub)]，保序去重。

    小类名直接命中（如 airplane → vehicle.airplane）自动归到所属大类；
    令牌法防回显：非法基名且非任何大类的小类 → 整行拒绝。"""
    out = []
    val = (val or "").strip().rstrip("。.")
    if not val or val in ("无", "none"):
        return out
    for tok in re.split(r"[，,、\s]+", val):
        tok = tok.strip().lower()
        if not tok or tok in ("无", "none"):
            continue
        parts = tok.split(".")
        m = parts[0].strip()
        s = parts[1].strip() if len(parts) > 1 else ""
        if m not in _valid_mains():
            # 小类名 → 找所属大类（airplane → vehicle.airplane）
            owner = next((om for om, d in _scenes().items() if m in d.get("sub", [])), None)
            if owner:
                m, s = owner, m
            else:
                return []  # 回显/乱填 → 整行拒绝
        elif s and s not in _scenes().get(m, {}).get("sub", []):
            s = ""  # 非法小类丢弃，保留大类
        if (m, s) not in out:
            out.append((m, s))
    return out


def _parse_candidates(val: str) -> list[tuple[str, str]]:
    """解析大类候选列表（person(人物)|vehicle(交通工具)）→ [(大类, 小类)] 保序去重。

    8B 对混合图（人+飞机）骑墙时可能并列多个类（「|」分隔），每项可带括号注解
    （中文标签或合法小类名）。非法大类跳过；无法解析返回空列表。"""
    out = []
    for tok in (val or "").split("|"):
        m = re.match(r"(\w+)\s*(?:\((\w+)\))?", tok.strip())
        if not m:
            continue
        name, sub = m.group(1).lower(), (m.group(2) or "").lower()
        if name not in _valid_mains():
            continue
        if sub and sub not in _scenes().get(name, {}).get("sub", []):
            sub = ""  # 括号里不是合法小类（中文标签等）→ 丢弃
        if (name, sub) not in out:
            out.append((name, sub))
    return out


def _dedupe_guess(text: str) -> str:
    """guess 输出去重：8B 在画面清晰/事实充分时可能沿「候选N:」模板重复输出几十个
    同质候选（重复循环）。按「候选N:」切块、按名称去重、最多保留 3 个；
    全同质时保留 1 个；无候选格式时原样返回。"""
    if not text:
        return text
    blocks = re.split(r"(?=候选\s*\d+\s*:)", text)
    if len(blocks) <= 2:
        return text  # 无「候选N:」格式（或只有引导句）→ 原样
    seen, kept = set(), []
    for b in blocks[1:]:
        m = re.search(r"名称\s*[:：]\s*(.+)", b)
        name = m.group(1).strip() if m else b.strip()[:20]
        if name in seen:
            continue
        seen.add(name)
        kept.append(b)
        if len(kept) >= 3:
            break
    if not kept:
        return text
    return blocks[0] + "".join(kept)


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


def _prompt_call(path_or_b64: str, prompt_key: str, scene: str, sub: str, scan_desc: str,
                 model: str = "") -> str:
    """引擎通用调用：取指定提示词条目，拼场景 header（与 zoom 同款结构），_post_b64 发送。

    供 _engine_table/_engine_gui/_engine_code 等提示词类引擎复用——接入专业引擎
    （rapid-table/OmniParser）时替换引擎函数体，本助手保留给其它提示词类引擎用。"""
    e = _entry(prompt_key)
    header = f"场景大类：{scene}"
    if sub:
        header += f"，小类：{sub}"
    if scan_desc.strip():
        header += f"\n第1次扫描的初步判断：\n{scan_desc.strip()}"
    prompt = f"{header}\n\n请基于以上信息，按以下要求处理：\n{e['text']}"
    return _post_b64(_to_b64(path_or_b64), prompt, e["temperature"], model=model)


def _engine_ocr(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "",
                question: str = "") -> str:
    """OCR 引擎（RapidOCR）：只提取文字。提取不足返回空串 → 调用方回退 vlm。
    逐字引擎不掺用户提问（question 忽略）——转写必须纯净，语义问题由文本 LLM 答。"""
    text = ocr(path_or_b64)
    if len(text) >= 20:
        return f"[OCR 提取的文字（自动路由，未走视觉模型）]\n{text}"
    return ""


def _engine_vlm(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "",
                question: str = "") -> str:
    """VLM 引擎：走 zoom 按类提示词，question 透传（拼回答节）。
    model 非空时用指定模型（本地 ollama / 云端厂商）。"""
    return zoom(path_or_b64, scene, sub=sub, scan_desc=scan_desc, model=model, question=question)


def _engine_table(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "",
                  question: str = "") -> str:
    """表格引擎：完整提取表格 → Markdown。逐字引擎，question 忽略（转写纯净）。

    当前实现：VLM + 表格提取提示词（extract_table）。接入专业引擎（rapid-table /
    PP-StructureV3）时替换本函数体，_ENGINES 与路由不用动；模型由用户按需配置（engine:model）。"""
    return _prompt_call(path_or_b64, "extract_table", scene, sub, scan_desc, model=model)


def _engine_gui(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "",
                question: str = "") -> str:
    """GUI 引擎：枚举界面可交互元素（VLM + extract_gui 提示词）。结构化枚举，question 忽略。

    接入专业引擎（OmniParser / UI-TARS）时替换本函数体，_ENGINES 与路由不用动；
    模型由用户按需配置（engine:model）。"""
    return _prompt_call(path_or_b64, "extract_gui", scene, sub, scan_desc, model=model)


def _engine_code(path_or_b64: str, scene: str, sub: str, scan_desc: str, model: str = "",
                 question: str = "") -> str:
    """代码引擎：逐字符转写代码原文（VLM + extract_code 提示词，温度 0.1）。
    逐字引擎，question 忽略（转写必须逐字纯净）。

    通用 OCR 对代码保真差（数字1↔字母l、空格丢失、`);`被误读），代码必须逐字；
    VLM 懂代码语义、按提示词严格保真，好于通用 OCR。模型由用户按需配置。"""
    return _prompt_call(path_or_b64, "extract_code", scene, sub, scan_desc, model=model)


# 引擎注册表：后续换专业引擎 = 加函数进注册表 + 改路由表指向，analyze 不动
_ENGINES = {
    "ocr": _engine_ocr,
    "vlm": _engine_vlm,
    "table": _engine_table,  # 表格引擎（接 rapid-table 时换函数体）
    "gui": _engine_gui,      # 界面元素引擎（接 OmniParser 时换函数体）
    "code": _engine_code,    # 代码逐字转写引擎（VLM 提示词）
}

# guess（第3次推测）只对实体/对象类场景有意义（身份/型号/品牌）。
# 文本/数据/界面/无法归类类场景的内容已在 OCR/事实里，guess 会基于文字幻觉
# （把对话框/文档文字当身份证据），跳过——deep 档对这类场景只跑 scan + zoom。
_GUESS_SCENES = ("person", "animal", "plant", "food", "vehicle", "machine",
                 "architecture", "object", "meme", "scene")

# 聚焦点（显著次主体）只对"照片/实体类"场景有意义——内容类场景（文档/截图/图表/图示/地图）
# 没有可分离的照片次主体，模型可能误报 object（同 _GUESS_SCENES 集合）。
_PHOTO_SCENES = _GUESS_SCENES

# 分析分支上限：主类 + 最多 1 个聚焦点场景（2 分支）。每分支=一次引擎（+一次 guess），
# 成本随分支线性涨；混合图（人+飞机）正好 2 分支，普通图自动 1 分支。要更多可调大。
_MAX_BRANCHES = 2


def _build_branches(main: str, sub: str, focus: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """分析分支队列：主类必含，聚焦点中与主类不同的场景按序追加；去重 + _MAX_BRANCHES 封顶。"""
    seen = {main}
    out = [("主", main, sub)]
    for m, s in focus:
        if m in seen or m in ("generic", "unknown"):
            continue
        seen.add(m)
        out.append(("聚焦点", m, s))
        if len(out) >= _MAX_BRANCHES:
            break
    return out


def _run_engine(engine: str, path_or_b64: str, scene: str, sub: str, scan_desc: str,
                model: str = "", question: str = "") -> str:
    """调引擎：未注册 / 异常返回空串（调用方回退 vlm，不报错）。回退记 warning 日志（兜底 + 可诊断）。"""
    fn = _ENGINES.get(engine)
    if fn is None:
        log.warning("router: 引擎 %r 未注册（scene=%s.%s），回退 vlm", engine, scene, sub)
        return ""
    try:
        return fn(path_or_b64, scene, sub, scan_desc, model=model, question=question)
    except Exception as e:
        log.warning("router: 引擎 %s 异常 %s: %s（scene=%s.%s model=%s），回退 vlm",
                    engine, type(e).__name__, e, scene, sub, model or "-")
        return ""


def analyze(path_or_b64: str, precision: str = "", mode: str = "", question: str = "") -> str:
    """按精度档位识别。统一入口，供 proxy / MCP 使用。
    - fast:     1 次 describe（单句描述）——快；带提问时不给回答节，仅提示档位不足
    - standard: scan + 主类引擎 + 聚焦点引擎（混合图各分支独立提取）；回答节落在 zoom
    - deep:     standard + 实体分支各自 guess（不合并）+ 空间结构；回答节落在 guess
    precision 缺省时读 config.prompts.default（或 config ollama.precision）。
    mode 走 config modes 表动态覆盖 guess 温度（v2 --mode）。
    question（用户具体需求）非空时拼到 zoom/guess 提示词末尾；逐字引擎（ocr/code/table/gui）
    不掺提问（转写纯净），语义问题由文本 LLM 答。
    """
    cfg = config_loader.get()
    if not precision:
        precision = cfg.get("ollama", {}).get("precision", "fast")
    precision = (precision or "fast").lower()
    if precision == "fast":
        # fast 也走 scan：1 次调用即含描述+类别（scan 提示词自带描述要求），供 scene 推理
        parsed = scan(path_or_b64)
        desc, scene = parsed[0], parsed[1]
        base = f"【初步判断】{desc}\n【场景】{scene}"
        if question:
            # fast 无提取层可挂回答节；硬塞 scan 会污染路由 → 只提示档位不足
            base += ("\n【提示】当前为 fast 档（仅初步判断），未针对你的提问定向回答；"
                     "如需定向回答请升级档位（/vision 2 或 3）。")
        return base
    # standard / deep：先 scan 判场景 + 聚焦点，再按路由表选引擎（视觉路由器 v1.5）
    parsed = scan(path_or_b64)
    desc, scene, sub = parsed[0], parsed[1], parsed[2]
    focus = parsed[3] if len(parsed) > 3 else []   # 聚焦点（显著次主体）
    if scene not in _PHOTO_SCENES:
        focus = []  # 内容类场景（diagram 等）的聚焦点误报（object）→ 丢弃，不跑多余分支
    # 分析分支：主类 + 聚焦点中与主类不同的场景（去重 + _MAX_BRANCHES 封顶）。
    # 混合图（如人+飞机）→ 两个分支各自路由引擎、各提各的，互不干扰；普通图自动 1 分支。
    # 回答节落点：standard → zoom（引擎层）；deep → 只 guess 拼（zoom 保持纯净，
    # 标准事实是 guess 的依据，回答节渗入会污染它）。
    zoom_question = question if precision != "deep" else ""
    branches = _build_branches(scene, sub, focus)
    branch_out = []
    ocr_used = False
    for label, m, s in branches:
        eng, mdl = _parse_route_value(_route_engine(m, s))
        facts = _run_engine(eng, path_or_b64, m, s, desc, model=mdl, question=zoom_question)
        if facts and eng == "ocr":
            ocr_used = True
        if not facts:
            mdl = ""  # 指定模型失败/缺失 → 兜底用全局模型
            facts = _run_engine("vlm", path_or_b64, m, s, desc, question=zoom_question) or ""
        if not facts and label == "主":
            facts = "[识别失败（引擎异常），已回退]"  # 主分支失败必须有占位；聚焦点分支空则丢弃
        if facts:
            branch_out.append((label, m, s, facts, mdl))
    if not branch_out:
        branch_out.append(("主", scene, sub, "[识别失败（引擎异常），已回退]", ""))
    # 细节分节：主类无头，聚焦点加节标题（【聚焦点】场景名）
    parts = [facts if label == "主" else f"【{label}】{m}\n{facts}" for label, m, _s, facts, _mdl in branch_out]
    facts_block = "\n\n".join(parts)
    if precision == "standard":
        tag = "[OCR]" if ocr_used else "[视觉]"
        return f"【初步判断】{desc}\n【场景】{scene}\n【细节({tag})】\n{facts_block}"
    # deep：实体分支各自 guess（不合并，各自用本分支事实+场景）；空间结构整图一次
    guess_parts = []
    for label, m, s, facts, mdl in branch_out:
        if m in _GUESS_SCENES and facts.strip():
            g = guess(path_or_b64, context=facts, scene=m, sub=s, scan_desc=desc,
                      mode=mode, model=mdl, question=question)
            if g:
                head = "【推测】" if label == "主" else f"【推测·{label}】"
                guess_parts.append(f"{head}\n{g}")
    # 模型无关：config ollama.grounding 控制是否启用 grounding（换不支持 bbox 的模型时设 false 跳过）
    spatial_out = ""
    if config_loader.get().get("ollama", {}).get("grounding", True):
        try:
            spatial_out = spatial(path_or_b64, model=branch_out[0][4])
        except Exception:
            spatial_out = ""  # grounding 失败不影响主体
    tag = "[OCR]" if ocr_used else "[视觉]"
    base = f"【初步判断】{desc}\n【场景】{scene}\n【细节({tag})】\n{facts_block}"
    if guess_parts:
        base += "\n\n" + "\n\n".join(guess_parts)
    if scene == "unknown":
        base += "\n【结论】模型判定无法归类，以下信息可能不完整，引用时请降级置信度。"
    if spatial_out:
        base += f"\n\n【空间结构】\n{spatial_out}"
    return base


def scan(path_or_b64: str) -> tuple[str, str, str, list[tuple[str, str]]]:
    """第1次：一句话描述 + 判断大类/小类 + 聚焦点。返回 (描述, 大类, 小类, 聚焦点)。"""
    e = _entry("scan")
    text = _post_b64(_to_b64(path_or_b64), e["text"], e["temperature"])
    main, sub, focus = _parse_scene(text)
    if not sub:
        # 接线 default_sub：scan 未给出小类时用该大类的默认小类（document 默认 report 不触发 OCR 路由）
        sub = _scenes().get(main, {}).get("default_sub", "") or ""
    return text, main, sub, focus


def zoom(path_or_b64: str, scene: str = "generic", sub: str = "", scan_desc: str = "",
         model: str = "", question: str = "") -> str:
    """第2次：按大类选 zoom 清单，小类作为上下文注入。

    scene 能否 zoom 取决于有没有 zoom_<scene> 提示词；缺失（如 config 新增类漏配）回退 generic，
    避免发「只有 header、正文空」的请求。model 非空时用指定模型（v1.5 场景模型）。
    question 非空时在提示词末尾拼接「用户关注点 + 回答节」——标准提取原样，提问单独成节。"""
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
    if question:
        prompt += (f"\n\n用户关注点：{question}\n"
                   f"最后单独输出【针对用户需求】部分回答该问题；"
                   f"图中不可见或无法确认的，明确说明，不要编造。")
    return _post_b64(_to_b64(path_or_b64), prompt, e["temperature"], model=model)


def guess(path_or_b64: str, context: str = "", scene: str = "", sub: str = "",
          scan_desc: str = "", mode: str = "", model: str = "", question: str = "") -> str:
    """第3次：基于 scan 描述 + zoom 事实 + 场景，大胆推测。

    mode 走 config modes 表动态覆盖 guess 温度（--mode / describe_image mode）；缺省用提示词温度。
    model 非空时用指定模型（v1.5 场景模型）。
    question 非空时在提示词末尾拼接「用户关注点 + 回答节」（deep 档回答节落点）。"""
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
    if question:
        prompt += (f"\n\n用户关注点：{question}\n"
                   f"最后单独输出【针对用户需求】部分回答该问题；"
                   f"图中不可见或无法确认的，明确说明，不要编造。")
    return _dedupe_guess(_post_b64(_to_b64(path_or_b64), prompt, temp, model=model))
