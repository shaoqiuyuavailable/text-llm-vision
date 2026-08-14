import io
import sys

import identify


def test_identify_full_passes_mode(monkeypatch):
    calls = {}

    def fake_analyze(path, precision="", mode=""):
        calls["mode"] = mode
        return f"result:{mode}"

    monkeypatch.setattr(identify.vision_client, "analyze", fake_analyze)
    monkeypatch.setattr(sys, "argv", ["identify.py", "/tmp/x.png", "--mode", "identity"])
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    identify.main()
    assert calls["mode"] == "identity"
    assert "result:identity" in captured.getvalue()


def test_identify_no_mode_stays_empty(monkeypatch):
    calls = {}

    def fake_analyze(path, precision="", mode=""):
        calls["mode"] = mode
        return "ok"

    monkeypatch.setattr(identify.vision_client, "analyze", fake_analyze)
    monkeypatch.setattr(sys, "argv", ["identify.py", "/tmp/x.png"])
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    identify.main()
    assert calls["mode"] == ""  # 无 --mode 不覆盖


def test_identify_unknown_mode_warns(monkeypatch):
    calls = {}

    def fake_analyze(path, precision="", mode=""):
        calls["mode"] = mode
        return "ok"

    monkeypatch.setattr(identify.vision_client, "analyze", fake_analyze)
    monkeypatch.setattr(sys, "argv", ["identify.py", "/tmp/x.png", "--mode", "bogus"])
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    identify.main()
    assert "未知 --mode" in captured.getvalue()
    assert calls["mode"] == "bogus"
