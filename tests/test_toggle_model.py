import json

import toggle


def _tmp_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(toggle, "CONFIG", str(tmp_path / "config.json"))
    cfg = {
        "models": {"qwen2.5vl": {"type": "ollama", "purpose": "default"}},
        "router": {"table": "vlm:qwen2.5vl", "_default": "vlm"},
    }
    with open(toggle.CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def _read(tmp_path):
    return json.load(open(toggle.CONFIG, encoding="utf-8"))


def test_model_add_writes_config(tmp_path, monkeypatch, capsys):
    _tmp_cfg(tmp_path, monkeypatch)
    rc = toggle.model_add(["llava:7b", "--type", "ollama", "--purpose", "light"])
    assert rc == 0
    cfg = _read(tmp_path)
    assert cfg["models"]["llava:7b"]["type"] == "ollama"
    assert cfg["models"]["llava:7b"]["purpose"] == "light"


def test_model_add_cloud_requires_provider(tmp_path, monkeypatch, capsys):
    _tmp_cfg(tmp_path, monkeypatch)
    rc = toggle.model_add(["qwen-vl-plus", "--type", "cloud"])
    assert rc == 1  # cloud 缺 --provider → 拒绝


def test_model_rm_logical(tmp_path, monkeypatch, capsys):
    _tmp_cfg(tmp_path, monkeypatch)
    rc = toggle.model_rm(["qwen2.5vl"])  # 有引用 → 警告但逻辑删
    assert rc == 0
    cfg = _read(tmp_path)
    assert "qwen2.5vl" not in cfg["models"]


def test_model_rm_physical_requires_yes(tmp_path, monkeypatch, capsys):
    _tmp_cfg(tmp_path, monkeypatch)
    rc = toggle.model_rm(["qwen2.5vl", "--physical"])  # 无 --yes → 拒绝，不删
    assert rc == 1
    cfg = _read(tmp_path)
    assert "qwen2.5vl" in cfg["models"]


def test_model_rm_physical_with_yes(tmp_path, monkeypatch, capsys):
    _tmp_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(toggle, "_cmd", lambda *a, **k: (0, "removed"))
    rc = toggle.model_rm(["qwen2.5vl", "--physical", "--yes"])
    assert rc == 0
    cfg = _read(tmp_path)
    assert "qwen2.5vl" not in cfg["models"]


def test_model_replace_rewrites_router(tmp_path, monkeypatch, capsys):
    _tmp_cfg(tmp_path, monkeypatch)
    toggle.model_replace(["qwen2.5vl", "llava"])
    cfg = _read(tmp_path)
    assert cfg["router"]["table"] == "vlm:llava"
    assert cfg["router"]["_default"] == "vlm"  # 无 ":" 引用的不动


def test_model_log_written(tmp_path, monkeypatch, capsys):
    _tmp_cfg(tmp_path, monkeypatch)
    logf = tmp_path / "vision-model.log"
    monkeypatch.setattr(toggle, "MODEL_LOG", str(logf))
    toggle.model_add(["llava", "--type", "ollama"])
    content = open(logf, encoding="utf-8").read()
    assert "add llava" in content and "OK" in content
