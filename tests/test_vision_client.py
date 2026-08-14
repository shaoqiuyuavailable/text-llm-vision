import vision_client


def test_locate_injects_query(monkeypatch):
    monkeypatch.setattr(vision_client, "_grounding_enabled", lambda: True)
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return '[{"name":"提交按钮","bbox":[100,200,300,400]}]'

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "BASE64")
    out = vision_client.locate("/tmp/x.png", "提交按钮")
    assert "提交按钮" in captured["prompt"]
    assert "bbox" in out


def test_compare_three_calls(monkeypatch):
    calls = []

    def fake_analyze(path, precision=""):
        calls.append(("analyze", path))
        return f"desc:{path}"

    def fake_post(b64, prompt, temperature, model=""):
        calls.append(("post", prompt))
        return "差异要点"

    monkeypatch.setattr(vision_client, "analyze", fake_analyze)
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: p)
    out = vision_client.compare("a.png", "b.png", "fast")
    assert calls[0] == ("analyze", "a.png")
    assert calls[1] == ("analyze", "b.png")
    assert calls[2][0] == "post"
    assert "【图A】" in out and "【图B】" in out and "【对比】" in out
    assert "desc:b.png" in calls[2][1]  # 图B描述注入对比 prompt


def test_use_cloud_provider_override(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "cloud_key", lambda: "sk")
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    assert vision_client._use_cloud() is True
    monkeypatch.setenv("VISION_PROVIDER", "local")
    assert vision_client._use_cloud() is False
    monkeypatch.setenv("VISION_PROVIDER", "cloud")
    assert vision_client._use_cloud() is True
    monkeypatch.setattr(config_loader, "cloud_key", lambda: "")
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    assert vision_client._use_cloud() is False


def test_locate_grounding_disabled_returns_hint(monkeypatch):
    monkeypatch.setattr(vision_client, "_grounding_enabled", lambda: False)
    called = []
    monkeypatch.setattr(vision_client, "_post_b64", lambda *a, **k: called.append(1) or "x")
    out = vision_client.locate("/tmp/x.png", "按钮")
    assert "grounding 已关闭" in out
    assert not called  # 未调识别


def test_spatial_uses_grounding(monkeypatch):
    captured = {}
    monkeypatch.setattr(vision_client, "_grounding_enabled", lambda: True)
    monkeypatch.setattr(vision_client, "_grounding", lambda p, prompt, temp, model="": captured.update(prompt=prompt) or "OK")
    vision_client.spatial("/tmp/x.png")
    assert "spatial" in captured.get("prompt", "") or captured.get("prompt")  # _entry("spatial") 文本


# ---- v2：16 大类解析 / zoom 选型 / 动态温度 ----

def _v2_scenes():
    return {
        "person": {"sub": ["real_single", "real_group", "anime_character"], "default_sub": "real_single"},
        "vehicle": {"sub": ["car", "airplane"], "default_sub": "car"},
        "document": {"sub": ["chat", "report", "code"], "default_sub": "report"},
        "unknown": {"sub": [], "default_sub": ""},
        "generic": {"sub": [], "default_sub": ""},
    }


def _v2_mains():
    return ("person", "animal", "plant", "food", "vehicle", "machine", "architecture",
            "document", "chart", "diagram", "map", "screenshot", "object", "meme",
            "scene", "unknown", "generic")


def test_parse_scene_v2_new_categories(monkeypatch):
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _v2_scenes)
    assert vision_client._parse_scene("大类: vehicle\n小类: car") == ("vehicle", "car")
    assert vision_client._parse_scene("大类: person\n小类: anime_character") == ("person", "anime_character")
    assert vision_client._parse_scene("大类: unknown\n小类: 无") == ("unknown", "")
    # 未知词 → generic 兜底
    assert vision_client._parse_scene("这是一张截图。") == ("generic", "")
    # animal 无 sub → 强制清空
    monkeypatch.setattr(vision_client, "_scenes",
                        lambda: {"animal": {"sub": [], "default_sub": ""}, **dict(_v2_scenes())})
    monkeypatch.setattr(vision_client, "_valid_mains",
                        lambda: ("animal", "generic") + _v2_mains())
    assert vision_client._parse_scene("大类: animal\n小类: 无") == ("animal", "")


def test_scan_applies_default_sub(monkeypatch):
    monkeypatch.setattr(vision_client, "_post_b64", lambda b, p, t: "大类: vehicle\n小类: 无")
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _v2_scenes)
    _, scene, sub = vision_client.scan("/tmp/x.png")
    assert scene == "vehicle"
    assert sub == "car"  # default_sub 接线


def test_zoom_uses_new_scene_prompt(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "OK"

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client.zoom("/tmp/x.png", scene="vehicle")
    assert "交通工具" in captured["prompt"] or "zoom_vehicle" in captured["prompt"]


def test_zoom_missing_prompt_falls_back_generic(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "OK"

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client.zoom("/tmp/x.png", scene="nonexistent_scene")
    assert "generic" in captured["prompt"]  # 缺 key → 回退 zoom_generic


def test_mode_temperature(monkeypatch):
    assert vision_client._mode_temperature("") is None
    assert vision_client._mode_temperature("identity") == 0.5  # config modes 表
    assert vision_client._mode_temperature("bogus") is None    # 未知名 → None


def test_guess_mode_overrides_temperature(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["temperature"] = temperature
        return "推测"

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client.guess("/tmp/x.png", context="事实", mode="rigorous")
    assert captured["temperature"] == 0.3
    vision_client.guess("/tmp/x.png", context="事实")  # 无 mode → 提示词默认 0.5
    assert captured["temperature"] == 0.5


def test_analyze_passes_mode_to_guess(monkeypatch):
    captured = {}

    def fake_guess(path, context="", scene="", sub="", scan_desc="", mode="", model=""):
        captured["mode"] = mode
        captured["model"] = model
        return "推测"

    monkeypatch.setattr(vision_client, "guess", fake_guess)
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "generic", ""))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "事实")
    out = vision_client.analyze("/tmp/x.png", "deep", mode="anime")
    assert captured["mode"] == "anime"
    assert "推测" in out


def test_analyze_unknown_scene_appends_conclusion(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "unknown", ""))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "事实")
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: "推测")
    out = vision_client.analyze("/tmp/x.png", "deep")
    assert "无法归类" in out


# ---- 视觉路由器 v1：路由表查表 / 引擎分发 / 回退 ----

def _router_table():
    return {"document.chat": "ocr", "document.code": "ocr", "_default": "vlm"}


def test_route_engine_lookup(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: {"router": _router_table()})
    assert vision_client._route_engine("document", "chat") == "ocr"
    assert vision_client._route_engine("document", "report") == "vlm"  # 无精确项 → _default
    assert vision_client._route_engine("table", "") == "vlm"
    assert vision_client._route_engine("unknown", "") == "vlm"


def test_analyze_document_chat_routes_to_ocr(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("文档", "document", "chat"))
    monkeypatch.setattr(vision_client, "ocr", lambda p: "这是OCR提取的文字内容示例，超过二十个字符。")
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "不应走 zoom")
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: "推测")
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "OCR" in out and "不应走 zoom" not in out


def test_analyze_table_routes_to_vlm(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("表格", "table", ""))
    monkeypatch.setattr(vision_client, "ocr", lambda p: "不应走 OCR")
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "表格VLM细节")
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "表格VLM细节" in out and "不应走 OCR" not in out


def test_analyze_unregistered_engine_falls_back_to_vlm(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("图", "table", ""))
    monkeypatch.setattr(vision_client, "_route_engine", lambda s, sb: "rapidtable")  # 未注册引擎
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "回退VLM细节")
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "回退VLM细节" in out  # 未注册 → 回退 vlm，不报错


def test_analyze_ocr_empty_falls_back_to_vlm(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("文档", "document", "chat"))
    monkeypatch.setattr(vision_client, "ocr", lambda p: "")  # OCR 提取不足
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "回退VLM文档细节")
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "回退VLM文档细节" in out


def test_analyze_vlm_engine_error_returns_placeholder(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("图", "table", ""))
    monkeypatch.setattr(vision_client, "_route_engine", lambda s, sb: "vlm")
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "识别失败" in out or "回退" in out


# ---- v1.5：路由值解析 / 模型级覆盖 ----

def test_parse_route_value():
    assert vision_client._parse_route_value("vlm:qwen2.5vl") == ("vlm", "qwen2.5vl")
    assert vision_client._parse_route_value("ocr") == ("ocr", "")
    assert vision_client._parse_route_value("") == ("vlm", "")
    assert vision_client._parse_route_value("vlm:qwen-vl-plus") == ("vlm", "qwen-vl-plus")


def test_post_b64_model_local_overrides(monkeypatch):
    import threading as _th
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "text"}

    def fake_post(url, json=None, timeout=None, trust_env=None):
        captured.update(model=json["model"], images=json["images"])
        return FakeResp()

    monkeypatch.setattr(vision_client, "_use_cloud", lambda: False)
    monkeypatch.setattr(vision_client, "_cache_on", lambda: False)
    monkeypatch.setattr(vision_client, "_active_cloud", lambda: None)
    monkeypatch.setattr(vision_client, "_OLLAMA_SEM", _th.BoundedSemaphore(1))
    monkeypatch.setattr(vision_client, "httpx", type("H", (), {"post": staticmethod(fake_post)})())
    import config_loader
    monkeypatch.setattr(config_loader, "get",
                        lambda: {"ollama": {"url": "http://localhost:11434/api/generate",
                                            "model": "qwen2.5vl", "top_p": 0.8},
                                 "models": {"llava": {"type": "ollama"}}})
    vision_client._post_b64("B64", "prompt", 0.3, model="llava")
    assert captured["model"] == "llava"  # 模型级覆盖本地 ollama 模型


def test_post_b64_model_cloud_routes_to_provider(monkeypatch):
    captured = {}

    def fake_cloud(b64, prompt, temperature, provider=""):
        captured["provider"] = provider
        return "cloud_text"

    monkeypatch.setattr(vision_client, "_post_cloud", fake_cloud)
    monkeypatch.setattr(vision_client, "_cache_on", lambda: False)
    import config_loader
    monkeypatch.setattr(config_loader, "get",
                        lambda: {"ollama": {}, "models": {"qwen-vl-plus": {"type": "cloud", "provider": "dashscope"}}})
    out = vision_client._post_b64("B64", "prompt", 0.3, model="qwen-vl-plus")
    assert out == "cloud_text"
    assert captured["provider"] == "dashscope"  # cloud 模型 → 云端该厂商


def test_run_engine_unregistered_logs_warning(monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="vision_client"):
        out = vision_client._run_engine("nope", "B64", "table", "", "desc")
    assert out == ""
    assert any("未注册" in r.message for r in caplog.records)
