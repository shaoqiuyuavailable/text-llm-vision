import control_api
import config_loader


def _cfg(router=None, ollama=None, models=None):
    return {
        "router": router or {"document.chat": "ocr", "_default": "vlm"},
        "ollama": ollama or {"model": "qwen2.5vl"},
        "models": models or {"qwen2.5vl": {"type": "ollama"}},
    }


def test_scene_exec_ocr_is_builtin_not_model(monkeypatch):
    # ocr 引擎走内置 RapidOCR，model 参数被忽略——绝不能标成全局模型（防误导）
    monkeypatch.setattr(config_loader, "get", lambda: _cfg())
    monkeypatch.setattr(config_loader, "use_cloud", lambda: False)
    out = control_api._scene_exec(config_loader.get())
    assert out["document.chat"] == "RapidOCR(内置)"
    assert out["_default"] == "qwen2.5vl"  # vlm 无显式模型 → 本地 ollama.model


def test_scene_exec_explicit_model_wins(monkeypatch):
    monkeypatch.setattr(config_loader, "get", lambda: _cfg(router={"table": "vlm:qwen-vl-plus"}))
    monkeypatch.setattr(config_loader, "use_cloud", lambda: False)
    out = control_api._scene_exec(config_loader.get())
    assert out["table"] == "qwen-vl-plus"  # engine:model 显式优先


def test_scene_exec_cloud_uses_active_provider_model(monkeypatch):
    monkeypatch.setattr(config_loader, "get", lambda: _cfg())
    monkeypatch.setattr(config_loader, "use_cloud", lambda: True)
    monkeypatch.setattr(config_loader, "active_cloud", lambda: {"name": "dashscope", "model": "qwen-vl-max"})
    out = control_api._scene_exec(config_loader.get())
    assert out["_default"] == "qwen-vl-max"  # 云端 → active 厂商 model


def test_scene_exec_missing_local_model_marks_missing(monkeypatch):
    monkeypatch.setattr(config_loader, "get", lambda: _cfg(ollama={"model": ""}))
    monkeypatch.setattr(config_loader, "use_cloud", lambda: False)
    out = control_api._scene_exec(config_loader.get())
    assert "未配" in out["_default"]


def test_get_status_includes_scene_exec(monkeypatch):
    monkeypatch.setattr(config_loader, "get", lambda: _cfg())
    monkeypatch.setattr(config_loader, "use_cloud", lambda: False)
    st = control_api.get_status()
    assert st["scene_exec"]["document.chat"] == "RapidOCR(内置)"
    assert st["scene_exec"]["_default"] == "qwen2.5vl"
