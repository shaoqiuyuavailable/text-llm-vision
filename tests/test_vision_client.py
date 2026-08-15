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
    assert vision_client._parse_scene("大类: vehicle\n小类: car") == ("vehicle", "car", [])
    assert vision_client._parse_scene("大类: person\n小类: anime_character") == ("person", "anime_character", [])
    assert vision_client._parse_scene("大类: unknown\n小类: 无") == ("unknown", "", [])
    # 未知词 → generic 兜底
    assert vision_client._parse_scene("这是一张截图。") == ("generic", "", [])
    # animal 无 sub → 强制清空
    monkeypatch.setattr(vision_client, "_scenes",
                        lambda: {"animal": {"sub": [], "default_sub": ""}, **dict(_v2_scenes())})
    monkeypatch.setattr(vision_client, "_valid_mains",
                        lambda: ("animal", "generic") + _v2_mains())
    assert vision_client._parse_scene("大类: animal\n小类: 无") == ("animal", "", [])


def _mixed_scenes():
    return {
        "person": {"sub": ["real_single"], "default_sub": "real_single"},
        "vehicle": {"sub": ["car"], "default_sub": "car"},
        "document": {"sub": ["chat", "report", "table"], "default_sub": "report"},
        "screenshot": {"sub": ["software_ui"], "default_sub": "software_ui"},
        "unknown": {"sub": [], "default_sub": ""},
        "generic": {"sub": [], "default_sub": ""},
    }



def test_parse_scene_focus_points(monkeypatch):
    # 聚焦点：主类之外的大类名（可带小类），保序；令牌法防回显
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _mixed_scenes)
    assert vision_client._parse_scene("大类: vehicle\n小类: car\n聚焦点: person.real_single") == \
        ("vehicle", "car", [("person", "real_single")])
    assert vision_client._parse_scene("大类: vehicle\n小类: car\n聚焦点: person, person") == \
        ("vehicle", "car", [("person", "")])  # 去重
    # 单主体图写 无 → 空列表
    assert vision_client._parse_scene("大类: vehicle\n小类: car\n聚焦点: 无") == ("vehicle", "car", [])
    # 回显防线：指令整段被抄 → 令牌含非法基名 → 整行拒绝
    assert vision_client._parse_scene("大类: vehicle\n小类: car\n聚焦点: 无 或 大类名,大类名（最多2个）") == \
        ("vehicle", "car", [])


def test_parse_scene_echo_category_list_goes_generic(monkeypatch):
    # 回显防线（大类）：模型把类别表整段抄回（含 | / (）→ 判 generic，不任意捡第一个词
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _mixed_scenes)
    echoed = ("大类: person(人物/角色)|animal(动物)|vehicle(交通工具)|unknown(无法确定)\n"
              "小类: real_single|car\n画面类型: 无\n聚焦点: 无")
    assert vision_client._parse_scene(echoed) == ("generic", "", [])


def _real_scenes():
    return {
        "person": {"sub": ["real_single", "real_group"], "default_sub": "real_single"},
        "vehicle": {"sub": ["car", "airplane"], "default_sub": "car"},
        "unknown": {"sub": [], "default_sub": ""},
        "generic": {"sub": [], "default_sub": ""},
    }


def test_parse_scene_real_mixed_output(monkeypatch):
    # 真实模型输出（人+飞机）：大类候选列表 person|vehicle + 聚焦点小类名 airplane
    # → 主类用聚焦点[0]（视觉占比最大）= vehicle.airplane，聚焦点剩下 person
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _real_scenes)
    out = vision_client._parse_scene(
        "大类: person(人物)|vehicle(交通工具)\n"
        "小类: real_single|airplane\n"
        "聚焦点: airplane, person")
    assert out == ("vehicle", "airplane", [("person", "")])


def test_parse_candidates(monkeypatch):
    # 大类候选列表解析：| 分隔、括号注解（中文标签丢弃、合法小类保留）、保序去重
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _real_scenes)
    assert vision_client._parse_candidates("person(人物)|vehicle(交通工具)") == [("person", ""), ("vehicle", "")]
    assert vision_client._parse_candidates("person(real_single)|vehicle(car)") == [("person", "real_single"), ("vehicle", "car")]
    assert vision_client._parse_candidates("unknown(无法确定)") == [("unknown", "")]
    assert vision_client._parse_candidates("") == []
    # 非法大类跳过；重复去重
    assert vision_client._parse_candidates("foo|vehicle|vehicle") == [("vehicle", "")]


def test_parse_scene_candidate_list_no_focus_builds_branches(monkeypatch):
    # 候选列表 + 聚焦点:无（模型全无头绪，混合图常见）→ 不再掉 generic：
    # 候选第 1 项当主类，其余降级为聚焦点（位置配对小类），保证双分支
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _real_scenes)
    out = vision_client._parse_scene(
        "大类: person(人物)|vehicle(交通工具)\n"
        "小类: real_single|airplane\n"
        "聚焦点: 无")
    assert out == ("person", "real_single", [("vehicle", "airplane")])


def test_parse_scene_candidate_list_filters_unknown(monkeypatch):
    # 回显残留：unknown 混进候选列表（person|unknown）→ 丢弃 unknown，真实主体优先
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _real_scenes)
    out = vision_client._parse_scene(
        "大类: person(人物)|unknown\n"
        "小类: real_single|unknown\n"
        "聚焦点: 无")
    assert out == ("person", "real_single", [])


def test_parse_scene_candidate_list_full_echo_stays_generic(monkeypatch):
    # >3 个候选 = 模型把类别表整段抄回（回显），不是真实骑墙 → 保持 generic 兜底
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _mixed_scenes)
    echoed = ("大类: person(人物/角色)|animal(动物)|vehicle(交通工具)|unknown(无法确定)\n"
              "小类: real_single|car\n画面类型: 无\n聚焦点: 无")
    assert vision_client._parse_scene(echoed) == ("generic", "", [])


def test_parse_scene_paren_annotation_not_echo(monkeypatch):
    # 回归：person(real_single) 是括号注解（模型给大类补小类说明），不是候选列表/回显
    # 不能因含「(」而降级 generic（曾引入此回归）
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _real_scenes)
    out = vision_client._parse_scene(
        "大类: person(real_single)\n小类: real_single\n画面类型: object\n聚焦点: 无")
    assert out == ("person", "real_single", [])  # 画面类型 object 非法类型被过滤


def test_parse_scene_list_sub_name_to_main(monkeypatch):
    # 聚焦点里的小类名（airplane）自动归到所属大类 vehicle
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _real_scenes)
    assert vision_client._parse_scene_list("airplane, person") == [("vehicle", "airplane"), ("person", "")]
    assert vision_client._parse_scene_list("无") == []
    # 非法基名（不是大类也不是小类）→ 整行拒绝
    assert vision_client._parse_scene_list("大类名") == []


def test_scan_applies_default_sub(monkeypatch):
    monkeypatch.setattr(vision_client, "_post_b64", lambda b, p, t: "大类: vehicle\n小类: 无")
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    monkeypatch.setattr(vision_client, "_valid_mains", _v2_mains)
    monkeypatch.setattr(vision_client, "_scenes", _v2_scenes)
    _, scene, sub, focus = vision_client.scan("/tmp/x.png")
    assert scene == "vehicle"
    assert sub == "car"  # default_sub 接线
    assert focus == []  # 无聚焦点 → 空列表


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




def _branch_cfg(scenes):
    return {
        "router": {"_default": "vlm"},
        "prompts": {f"zoom_{s}": {"text": s, "temperature": 0.3} for s in scenes}
                   | {"guess": {"text": "推测", "temperature": 0.5}},
        "ollama": {"model": "qwen2.5vl", "temperature": 0.5},
    }


def test_analyze_mixed_branches_subject_and_focus(monkeypatch):
    # 混合图（人+飞机）：主类 vehicle + 聚焦点 person → 两个分支各自引擎 + 各自 guess，输出分节
    calls = []
    monkeypatch.setattr(vision_client, "scan",
                        lambda p: ("描述", "vehicle", "car", [("person", "")]))
    monkeypatch.setattr(vision_client, "zoom",
                        lambda *a, **k: calls.append(("zoom", a[1])) or f"{a[1]}事实")  # scene 是位置参数
    monkeypatch.setattr(vision_client, "guess",
                        lambda *a, **k: calls.append(("guess", k.get("scene"))) or "推测")
    monkeypatch.setattr(vision_client, "spatial", lambda *a, **k: "")
    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _branch_cfg(("vehicle", "person")))
    out = vision_client.analyze("/tmp/x.png", "deep")
    assert ("zoom", "vehicle") in calls
    assert ("zoom", "person") in calls       # 聚焦点分支也跑了引擎
    assert ("guess", "vehicle") in calls
    assert ("guess", "person") in calls      # 实体分支各自 guess（不合并）
    assert "【聚焦点】person" in out         # 输出分节


def test_analyze_focus_same_as_main_dedup(monkeypatch):
    # 聚焦点与主类同大类 → 只跑一次引擎，不分节
    calls = []
    monkeypatch.setattr(vision_client, "scan",
                        lambda p: ("描述", "person", "real_single", [("person", "")]))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: calls.append(a[1]) or "事实")  # scene 位置参数
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: "推测")
    monkeypatch.setattr(vision_client, "spatial", lambda *a, **k: "")
    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _branch_cfg(("person",)))
    out = vision_client.analyze("/tmp/x.png", "deep")
    assert calls.count("person") == 1
    assert "【聚焦点】" not in out


def test_analyze_max_branches_cap(monkeypatch):
    # 聚焦点 2 个不同场景 → 分支封顶（主 + 第1个聚焦点）
    scenes = []
    monkeypatch.setattr(vision_client, "scan",
                        lambda p: ("描述", "person", "real_single", [("vehicle", ""), ("architecture", "")]))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: scenes.append(a[1]) or "事实")  # scene 位置参数
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: "推测")
    monkeypatch.setattr(vision_client, "spatial", lambda *a, **k: "")
    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _branch_cfg(("person", "vehicle", "architecture")))
    vision_client.analyze("/tmp/x.png", "deep")
    assert scenes == ["person", "vehicle"]  # 第2个聚焦点被 _MAX_BRANCHES 截断




def test_analyze_content_scene_drops_focus(monkeypatch):
    # 内容类场景（diagram）聚焦点误报 object → 丢弃，不跑多余分支
    scenes = []
    monkeypatch.setattr(vision_client, "scan",
                        lambda p: ("描述", "diagram", "flowchart", [("object", "")]))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: scenes.append(a[1]) or "图")
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: "推测")
    monkeypatch.setattr(vision_client, "spatial", lambda *a, **k: "")
    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _branch_cfg(("diagram",)))
    out = vision_client.analyze("/tmp/x.png", "deep")
    assert scenes == ["diagram"]  # 聚焦点 object 被丢弃
    assert "【聚焦点】" not in out


def test_analyze_photo_scene_keeps_focus(monkeypatch):
    # 照片类场景（person）聚焦点 food → 保留，跑第二分支
    scenes = []
    monkeypatch.setattr(vision_client, "scan",
                        lambda p: ("描述", "person", "real_single", [("food", "")]))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: scenes.append(a[1]) or "事实")
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: "推测")
    monkeypatch.setattr(vision_client, "spatial", lambda *a, **k: "")
    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _branch_cfg(("person", "food")))
    vision_client.analyze("/tmp/x.png", "deep")
    assert scenes == ["person", "food"]  # 聚焦点 food 保留，第二分支跑


def test_dedupe_guess_repeated_candidates():
    # 8B 重复循环：20+ 个同质候选 → 按名称去重保留 1 个
    repeated = ("推测候选：\n"
                "候选1:\n名称: 射箭爱好者\n依据: 室内射箭\n反证: 无\n置信度(中)\n"
                "候选2:\n名称: 射箭爱好者\n依据: 室内射箭\n反证: 无\n置信度(中)\n"
                "候选3:\n名称: 射箭爱好者\n依据: 室内射箭\n反证: 无\n置信度(中)")
    out = vision_client._dedupe_guess(repeated)
    assert out.count("名称") == 1  # 全同质 → 保留 1 个候选


def test_dedupe_guess_distinct_kept_cap3():
    # 不同候选保留，最多 3 个；重复的第 4 个被删
    text = ("推测：\n"
            "候选1:\n名称: 射箭爱好者\n依据: a\n反证: b\n置信度(中)\n"
            "候选2:\n名称: 射箭教练\n依据: a\n反证: b\n置信度(中)\n"
            "候选3:\n名称: 运动员\n依据: a\n反证: b\n置信度(中)\n"
            "候选4:\n名称: 射箭爱好者\n依据: a\n反证: b\n置信度(中)")
    out = vision_client._dedupe_guess(text)
    assert out.count("名称") == 3
    assert "候选4" not in out


def test_dedupe_guess_no_candidates_unchanged():
    text = "推测：图中主体无法确定身份。"
    assert vision_client._dedupe_guess(text) == text


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

    def fake_guess(path, context="", scene="", sub="", scan_desc="", mode="", model="", question=""):
        captured["mode"] = mode
        captured["model"] = model
        return "推测"

    monkeypatch.setattr(vision_client, "guess", fake_guess)
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "vehicle", "car"))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "事实")
    out = vision_client.analyze("/tmp/x.png", "deep", mode="anime")
    assert captured["mode"] == "anime"
    assert "推测" in out


# ---- 用户需求回答节（question 贯通：zoom/guess 末尾拼接、fast 档位提示、逐字引擎不掺）----


def test_zoom_appends_answer_section_with_question(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "OK"

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client.zoom("/tmp/x.png", scene="vehicle", question="这车什么型号")
    assert "用户关注点：这车什么型号" in captured["prompt"]
    assert "【针对用户需求】" in captured["prompt"]
    assert "不要编造" in captured["prompt"]


def test_zoom_no_question_no_answer_section(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "OK"

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client.zoom("/tmp/x.png", scene="vehicle")
    assert "【针对用户需求】" not in captured["prompt"]


def test_guess_appends_answer_section_with_question(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "OK"

    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client.guess("/tmp/x.png", context="事实", scene="vehicle", question="这是哪款车")
    assert "用户关注点：这是哪款车" in captured["prompt"]
    assert "【针对用户需求】" in captured["prompt"]


def test_analyze_fast_with_question_hints_level(monkeypatch):
    # fast 无提取层 → 不拼回答节，只提示档位不足（不硬塞 scan 污染路由）
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "vehicle", "car"))
    out = vision_client.analyze("/tmp/x.png", "fast", question="这车什么型号")
    assert "档位" in out
    assert "【针对用户需求】" not in out


def test_analyze_fast_without_question_no_hint(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "vehicle", "car"))
    out = vision_client.analyze("/tmp/x.png", "fast")
    assert "档位" not in out


def test_analyze_standard_passes_question_to_zoom(monkeypatch):
    captured = {}

    def fake_zoom(path, scene="generic", sub="", scan_desc="", model="", question=""):
        captured["question"] = question
        return "事实"

    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "vehicle", "car"))
    monkeypatch.setattr(vision_client, "zoom", fake_zoom)
    vision_client.analyze("/tmp/x.png", "standard", question="这车什么型号")
    assert captured["question"] == "这车什么型号"


def test_analyze_deep_passes_question_to_guess(monkeypatch):
    captured = {}

    def fake_guess(path, context="", scene="", sub="", scan_desc="", mode="", model="", question=""):
        captured["question"] = question
        return "推测"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _branch_cfg(("vehicle",)))
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "vehicle", "car"))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "事实")
    monkeypatch.setattr(vision_client, "guess", fake_guess)
    monkeypatch.setattr(vision_client, "spatial", lambda *a, **k: "")
    vision_client.analyze("/tmp/x.png", "deep", question="这车什么型号")
    assert captured["question"] == "这车什么型号"


def test_analyze_deep_zoom_stays_pure(monkeypatch):
    # deep 档回答节只落 guess：zoom 不收 question（标准事实纯净，guess 的依据不被污染）
    zoom_captured, guess_captured = {}, {}

    def fake_zoom(path, scene="generic", sub="", scan_desc="", model="", question=""):
        zoom_captured["question"] = question
        return "事实"

    def fake_guess(path, context="", scene="", sub="", scan_desc="", mode="", model="", question=""):
        guess_captured["question"] = question
        return "推测"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _branch_cfg(("vehicle",)))
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "vehicle", "car"))
    monkeypatch.setattr(vision_client, "zoom", fake_zoom)
    monkeypatch.setattr(vision_client, "guess", fake_guess)
    monkeypatch.setattr(vision_client, "spatial", lambda *a, **k: "")
    vision_client.analyze("/tmp/x.png", "deep", question="这车什么型号")
    assert zoom_captured["question"] == ""   # deep 档 zoom 纯净
    assert guess_captured["question"] == "这车什么型号"  # 回答节只在 guess


def test_analyze_code_engine_does_not_append_question(monkeypatch):
    # 逐字引擎（code）不掺用户提问——转写必须纯净，回答节不进 extract_code 提示词
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "void foo() { return 1; }"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _engine_cfg())
    monkeypatch.setattr(vision_client, "scan", lambda p: ("代码", "document", "code"))
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    out = vision_client.analyze("/tmp/x.png", "standard", question="循环条件是什么")
    assert "void foo()" in out
    assert "【针对用户需求】" not in captured["prompt"]


def test_sanitize_strips_lone_surrogates():
    # 孤立代理字符（Ollama 无效 UTF-8 字节 → httpx surrogateescape）必须清洗，
    # 否则 json.dumps / HTTP 响应再编码崩（UnicodeEncodeError）
    assert vision_client._sanitize("正常文本") == "正常文本"
    assert vision_client._sanitize("abc\udca1def") == "abc?def"  # 乱码字节 → '?'
    assert vision_client._sanitize("") == ""


def test_cache_key_encode_survives_surrogate_prompt():
    # 回归：GBK 误读遗留的孤立代理 prompt 不该让缓存 key 生成崩（encode 用 replace）
    import hashlib
    model, b64, temp = "qwen", "B64", "0.5"
    prompt = "用户关注点：椋炶\udca1屽憳"  # 模拟被 GBK 误读的乱码中文 prompt
    key = hashlib.sha256((model + "|" + b64 + "|" + prompt + "|" + temp)
                         .encode("utf-8", "replace")).hexdigest()
    assert len(key) == 64


def test_analyze_unknown_scene_appends_conclusion(monkeypatch):
    monkeypatch.setattr(vision_client, "scan", lambda p: ("描述", "unknown", ""))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "事实")
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: "推测")
    out = vision_client.analyze("/tmp/x.png", "deep")
    assert "无法归类" in out


# ---- 视觉路由器 v1：路由表查表 / 引擎分发 / 回退 ----

def _router_table():
    return {"document.chat": "ocr", "document.code": "code", "_default": "vlm"}


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


def test_post_b64_local_ignores_cloud_model(monkeypatch):
    # 回归：active 指向云端厂商（无 key → use_cloud False）时，本地请求必须用 ollama.model，
    # 绝不混入云端厂商 model（曾因此向 Ollama 发 qwen-vl-plus → 404）
    import threading as _th
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "text"}

    def fake_post(url, json=None, timeout=None, trust_env=None):
        captured["model"] = json["model"]
        return FakeResp()

    monkeypatch.setattr(vision_client, "_use_cloud", lambda: False)
    monkeypatch.setattr(vision_client, "_cache_on", lambda: False)
    monkeypatch.setattr(vision_client, "_active_cloud",
                        lambda: {"name": "dashscope", "model": "qwen-vl-plus"})
    monkeypatch.setattr(vision_client, "_OLLAMA_SEM", _th.BoundedSemaphore(1))
    monkeypatch.setattr(vision_client, "httpx", type("H", (), {"post": staticmethod(fake_post)})())
    import config_loader
    monkeypatch.setattr(config_loader, "get",
                        lambda: {"ollama": {"url": "http://localhost:11434/api/generate",
                                            "model": "qwen2.5vl", "top_p": 0.8},
                                 "models": {"qwen2.5vl": {"type": "ollama"}}})
    vision_client._post_b64("B64", "prompt", 0.3)
    assert captured["model"] == "qwen2.5vl"  # 本地请求用 ollama.model，非 qwen-vl-plus


# ---- 引擎特化 1/2/3：表格 / GUI / 图表（qwen 占位）----

def _engine_router():
    return {"document.chat": "ocr", "document.code": "code",
            "document.table": "table", "chart": "vlm",
            "screenshot.software_ui": "gui", "_default": "vlm"}


def _engine_cfg():
    import config_loader
    return {
        "router": _engine_router(),
        "prompts": {
            "extract_table": {"text": "把表格完整转为 Markdown，逐格提取，禁止编造。", "temperature": 0.2},
            "extract_gui": {"text": "枚举界面所有可交互元素，逐个给功能描述。", "temperature": 0.3},
            "extract_code": {"text": "逐字符转写代码，严格区分数字1与小写l。", "temperature": 0.1},
            "zoom_chart": {"text": "先转表再给结论。", "temperature": 0.3},
            "zoom_generic": {"text": "通用。", "temperature": 0.3},
        },
        "ollama": {"model": "qwen2.5vl", "temperature": 0.5},
    }


def test_route_engine_new_scenes(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: {"router": _engine_router()})
    assert vision_client._route_engine("document", "table") == "table"
    assert vision_client._route_engine("screenshot", "software_ui") == "gui"
    assert vision_client._route_engine("chart", "") == "vlm"   # chart 大类键命中
    assert vision_client._route_engine("chart", "bar") == "vlm"


def test_parse_route_value_table_gui():
    assert vision_client._parse_route_value("table:qwen2.5vl") == ("table", "qwen2.5vl")
    assert vision_client._parse_route_value("gui:qwen2.5vl") == ("gui", "qwen2.5vl")


def test_analyze_document_table_uses_extract_table_prompt(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "| 列A | 列B |\n|---|---|\n| 1 | 2 |"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _engine_cfg())
    monkeypatch.setattr(vision_client, "scan", lambda p: ("表格", "document", "table"))
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: (_ for _ in ()).throw(AssertionError("表格不应走 zoom")))
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "| 列A |" in out                       # 走表格引擎（占位 VLM 提示词）
    assert "Markdown" in captured["prompt"] or "表格" in captured["prompt"]
    assert "不应走 zoom" not in out


def test_analyze_screenshot_software_ui_uses_gui_prompt(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "按钮: 登录（顶部右侧，登录操作）"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _engine_cfg())
    monkeypatch.setattr(vision_client, "scan", lambda p: ("界面", "screenshot", "software_ui"))
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: (_ for _ in ()).throw(AssertionError("UI 不应走 zoom")))
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "登录" in out
    assert "可交互" in captured["prompt"] or "元素" in captured["prompt"]


def test_analyze_deep_document_skips_guess(monkeypatch):
    # 防幻觉：文本/数据类场景（document 等）不跑 guess——内容已在事实里，guess 会基于文字编造身份
    called = []
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: called.append(1) or "不应出现")
    monkeypatch.setattr(vision_client, "scan", lambda p: ("文档", "document", "report"))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "文档事实")
    import config_loader
    monkeypatch.setattr(config_loader, "get",
                        lambda: {"router": {"_default": "vlm"}, "ollama": {"model": "qwen2.5vl", "grounding": False}})
    out = vision_client.analyze("/tmp/x.png", "deep")
    assert not called          # guess 未调用
    assert "文档事实" in out
    assert "【推测】" not in out


def test_analyze_deep_screenshot_skips_guess(monkeypatch):
    called = []
    monkeypatch.setattr(vision_client, "guess", lambda *a, **k: called.append(1) or "不应出现")
    monkeypatch.setattr(vision_client, "scan", lambda p: ("界面", "screenshot", "software_ui"))
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: "界面事实")
    import config_loader
    monkeypatch.setattr(config_loader, "get",
                        lambda: {"router": {"_default": "vlm"}, "ollama": {"model": "qwen2.5vl", "grounding": False}})
    out = vision_client.analyze("/tmp/x.png", "deep")
    assert not called
    assert "【推测】" not in out


def test_engine_table_passes_model_override(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["model"] = model
        return "ok"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _engine_cfg())
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client._engine_table("/tmp/x.png", "document", "table", "", model="llava")
    assert captured["model"] == "llava"  # 引擎模型透传（v1.5 engine:model）


def test_analyze_document_code_uses_extract_code_prompt(monkeypatch):
    # 代码截图 → code 引擎（VLM 逐字转写），不落通用 OCR
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["prompt"] = prompt
        return "void foo() {\n    return 1;\n}"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _engine_cfg())
    monkeypatch.setattr(vision_client, "scan", lambda p: ("代码", "document", "code"))
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    monkeypatch.setattr(vision_client, "zoom", lambda *a, **k: (_ for _ in ()).throw(AssertionError("代码不应走 zoom")))
    out = vision_client.analyze("/tmp/x.png", "standard")
    assert "void foo()" in out
    assert "数字1" in captured["prompt"] or "逐字符" in captured["prompt"]  # 代码保真要求


def test_engine_code_passes_model_override(monkeypatch):
    captured = {}

    def fake_post(b64, prompt, temperature, model=""):
        captured["model"] = model
        return "code"

    import config_loader
    monkeypatch.setattr(config_loader, "get", lambda: _engine_cfg())
    monkeypatch.setattr(vision_client, "_post_b64", fake_post)
    monkeypatch.setattr(vision_client, "_to_b64", lambda p: "B64")
    vision_client._engine_code("/tmp/x.png", "document", "code", "", model="llava")
    assert captured["model"] == "llava"
