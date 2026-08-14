import json

import mcp_hosts


def _srv(monkeypatch):
    monkeypatch.setattr(mcp_hosts, "_python_cmd", lambda: "python")
    monkeypatch.setattr(mcp_hosts, "_server_args", lambda: ["C:/vision/mcp_server.py"])


def test_write_codex(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "config.toml"
    assert mcp_hosts.write_codex(str(p)) is True
    text = p.read_text(encoding="utf-8")
    assert "[mcp_servers.vision]" in text
    assert "C:/vision/mcp_server.py" in text
    assert mcp_hosts.write_codex(str(p)) is False  # 幂等


def test_write_opencode_merges(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "opencode.json"
    p.write_text('{"existing": 1}', encoding="utf-8")
    mcp_hosts.write_opencode(str(p))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["existing"] == 1
    m = data["mcp"]["vision"]
    assert m["type"] == "local"
    assert m["command"] == ["python", "C:/vision/mcp_server.py"]


def test_write_cline(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "cline_mcp_settings.json"
    mcp_hosts.write_cline(str(p))
    m = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["vision"]
    assert m["command"] == "python"
    assert m["disabled"] is False


def test_write_continue_merges_array(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "config.json"
    p.write_text('{"mcpServers": [{"name": "other", "command": "x"}]}', encoding="utf-8")
    mcp_hosts.write_continue(str(p))
    names = [s["name"] for s in json.loads(p.read_text(encoding="utf-8"))["mcpServers"]]
    assert "other" in names and "vision" in names


def test_write_copilot(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "mcp.json"
    mcp_hosts.write_copilot(str(p))
    m = json.loads(p.read_text(encoding="utf-8"))["servers"]["vision"]
    assert m["type"] == "stdio"
    assert m["command"] == "python"


def test_write_cursor(monkeypatch, tmp_path):
    _srv(monkeypatch)
    p = tmp_path / "mcp.json"
    mcp_hosts.write_cursor(str(p))
    m = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["vision"]
    assert m["args"] == ["C:/vision/mcp_server.py"]


def test_rules_append_idempotent(tmp_path):
    p = tmp_path / "AGENTS.md"
    p.write_text("# Project", encoding="utf-8")
    assert mcp_hosts.write_agents_md(str(p)) is True
    assert mcp_hosts.write_agents_md(str(p)) is False
    assert p.read_text(encoding="utf-8").count("describe_image") == 1


def test_host_status_shape(monkeypatch):
    monkeypatch.setattr(mcp_hosts, "file_has_vision", lambda p: False)
    monkeypatch.setattr(mcp_hosts, "_cmd", lambda *a, **k: (1, ""))
    rows = mcp_hosts.host_status()
    assert len(rows) >= 7
    assert all(len(r) == 3 for r in rows)
    assert rows[0][0] == "claude"


def test_claude_mcp_upsert_removes_then_adds(monkeypatch):
    calls = []

    def fake_cmd(args, timeout=20):
        calls.append(list(args))
        if args[:3] == ["claude", "mcp", "list"]:
            return 0, "vision ... mcp-vision.js"
        if "remove" in args:
            return 0, "Removed"
        if "add" in args:
            return 0, "Added"
        return 0, ""

    monkeypatch.setattr(mcp_hosts, "_cmd", fake_cmd)
    monkeypatch.setattr(mcp_hosts, "_python_cmd", lambda: "python")
    code, _ = mcp_hosts.claude_mcp_upsert("C:/srv/mcp_server.py")
    assert code == 0
    add = [c for c in calls if "add" in c]
    remove = [c for c in calls if "remove" in c]
    assert add and remove  # 同名已注册 → 先 remove 再 add
    assert add[0][-1] == "C:/srv/mcp_server.py"
    assert calls.index(remove[0]) < calls.index(add[0])


def test_claude_mcp_upsert_no_remove_when_absent(monkeypatch):
    calls = []

    def fake_cmd(args, timeout=20):
        calls.append(list(args))
        if args[:3] == ["claude", "mcp", "list"]:
            return 0, ""  # 未注册
        if "add" in args:
            return 0, "Added"
        return 0, ""

    monkeypatch.setattr(mcp_hosts, "_cmd", fake_cmd)
    monkeypatch.setattr(mcp_hosts, "_python_cmd", lambda: "python")
    code, _ = mcp_hosts.claude_mcp_upsert("C:/srv/mcp_server.py")
    assert code == 0
    assert not any("remove" in c for c in calls)  # 未注册不 remove
    assert any("add" in c for c in calls)
