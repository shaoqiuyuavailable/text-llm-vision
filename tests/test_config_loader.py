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
    b = config_loader.resolve_backend()
    assert b["provider"] == "cloud"
    assert b["api_key"] == "sk-test"
