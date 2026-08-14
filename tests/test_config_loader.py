import config_loader


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(tmp_path / "nonexistent.json"))
    for k in ("OLLAMA_URL", "OLLAMA_BASE_URL", "VISION_MODEL", "VISION_API_KEY", "VISION_API_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_without_env(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    cfg = config_loader.get()
    assert cfg["ollama"]["model"] == "qwen2.5vl"
    assert cfg["ollama"]["url"] == "http://localhost:11434/api/generate"


def test_ollama_url_env_override(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
    assert config_loader.get()["ollama"]["url"] == "http://host.docker.internal:11434/api/generate"


def test_ollama_base_url_alias(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.5:11434/api/generate")
    assert config_loader.get()["ollama"]["url"] == "http://192.168.1.5:11434/api/generate"


def test_vision_model_env_override(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_MODEL", "llava")
    assert config_loader.get()["ollama"]["model"] == "llava"


def test_cloud_env_injects_synthetic_platform(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    monkeypatch.setenv("VISION_API_BASE_URL", "https://api.example.com/v1")
    cfg = config_loader.get()
    assert cfg["cloud"]["active"] == "env"
    assert cfg["cloud"]["clouds"][0]["api_key"] == "sk-test"


def test_env_key_without_base_url_stays_local(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    # 不设 VISION_API_BASE_URL
    cfg = config_loader.get()
    assert cfg["cloud"].get("active") != "env"
    urls = [c.get("base_url", "") for c in cfg["cloud"].get("clouds", [])]
    assert "https://api.example.com/v1" not in urls


def test_resolve_backend_local(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    b = config_loader.resolve_backend()
    assert b["provider"] == "local"
    assert b["model"] == "qwen2.5vl"
    assert b["precision"] == "fast"


def test_resolve_backend_cloud_env(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    monkeypatch.setenv("VISION_API_BASE_URL", "https://api.example.com/v1")
    b = config_loader.resolve_backend()
    assert b["provider"] == "cloud"
    assert b["api_key"] == "sk-test"


def test_active_cloud_falls_back_to_first_with_key(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    import json
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"cloud": {"active": "",
        "clouds": [{"name": "a", "base_url": "https://a", "model": "m", "api_key": ""},
                   {"name": "b", "base_url": "https://b", "model": "m", "api_key": "bkey"}]}}),
        encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(conf))
    monkeypatch.delenv("A_API_KEY", raising=False)
    monkeypatch.delenv("B_API_KEY", raising=False)
    c = config_loader.active_cloud()
    assert c is not None and c.get("name") == "b"


def test_cloud_key_env_name_support(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env")
    assert config_loader.cloud_key_of({"name": "dashscope", "api_key": ""}) == "sk-env"
    assert config_loader.cloud_key_of({"name": "dashscope", "api_key": "cfg"}) == "sk-env"
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    assert config_loader.cloud_key_of({"name": "dashscope", "api_key": "cfg"}) == "cfg"


def test_use_cloud_provider_force(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "k")
    monkeypatch.setenv("VISION_API_BASE_URL", "https://b")
    assert config_loader.use_cloud() is True
    monkeypatch.setenv("VISION_PROVIDER", "local")
    assert config_loader.use_cloud() is False
    monkeypatch.setenv("VISION_PROVIDER", "cloud")
    assert config_loader.use_cloud() is True


def test_resolve_backend_model_cloud(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "k")
    monkeypatch.setenv("VISION_API_BASE_URL", "https://b")
    monkeypatch.setenv("VISION_MODEL", "qwen-vl-plus")
    b = config_loader.resolve_backend()
    assert b["provider"] == "cloud"
    assert b["model"] == "qwen-vl-plus"  # 云时取云模型，非 ollama.model
    monkeypatch.delenv("VISION_API_KEY")
    monkeypatch.delenv("VISION_API_BASE_URL")
    monkeypatch.delenv("VISION_MODEL")
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    b2 = config_loader.resolve_backend()
    assert b2["provider"] == "local"
    assert b2["model"] == "qwen2.5vl"


# ---- v2：prompts_version 门 + modes ----

def _write_config(monkeypatch, tmp_path, data):
    import json
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(conf))
    for k in ("OLLAMA_URL", "OLLAMA_BASE_URL", "VISION_MODEL", "VISION_API_KEY", "VISION_API_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def test_modes_default_from_baseline(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {})
    cfg = config_loader.get()
    assert cfg["modes"]["rigorous"] == 0.3
    assert cfg["modes"]["identity"] == 0.5
    assert cfg["modes"]["open"] == 0.8


def test_prompts_version_gate_skips_old_structure(monkeypatch, tmp_path):
    # v1 config：scenes/prompts/modes 是旧 5 类，门应跳过它们，用 v2 基线
    _write_config(monkeypatch, tmp_path, {
        "prompts_version": 1,
        "scenes": {"oldscene": {"sub": ["a"], "default_sub": "a"}},
        "prompts": {"scan": {"text": "旧提示词", "temperature": 0.9}},
        "modes": {"custom": 0.1},
    })
    cfg = config_loader.get()
    assert "oldscene" not in cfg["scenes"]
    assert "vehicle" in cfg["scenes"]          # v2 基线 16 类在
    assert "zoom_vehicle" in cfg["prompts"]     # v2 基线 17 zoom 在
    assert cfg["prompts"]["scan"]["text"] != "旧提示词"
    assert "custom" not in cfg["modes"]


def test_prompts_version_2_overlays(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {
        "prompts_version": 2,
        "modes": {"identity": 0.55},
        "scenes": {"extra": {"sub": ["x"], "default_sub": "x"}},
        "prompts": {"scan": {"text": "新提示词", "temperature": 0.2}},
    })
    cfg = config_loader.get()
    assert cfg["prompts"]["scan"]["text"] == "新提示词"
    assert cfg["modes"]["identity"] == 0.55
    assert "extra" in cfg["scenes"]
    assert "vehicle" in cfg["scenes"]  # 基线 + 覆盖并存
