import vision_client


def test_locate_injects_query(monkeypatch):
    monkeypatch.setattr(vision_client, "_grounding_enabled", lambda: True)
    captured = {}

    def fake_post(b64, prompt, temperature):
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

    def fake_post(b64, prompt, temperature):
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
    monkeypatch.setattr(vision_client, "_grounding", lambda p, prompt, temp: captured.update(prompt=prompt) or "OK")
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

    def fake_post(b64, prompt, temperature):
        captured["prompt"] = prompt
        return "OK"

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client.zoom("/tmp/x.png", scene="vehicle")
    assert "交通工具" in captured["prompt"] or "zoom_vehicle" in captured["prompt"]


def test_zoom_missing_prompt_falls_back_generic(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature):
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

    def fake_post(b64, prompt, temperature):
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

    def fake_guess(path, context="", scene="", sub="", scan_desc="", mode=""):
        captured["mode"] = mode
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
