import os

import install


def test_needed_files_include_new():
    assert "mcp_server.py" in install.NEEDED_FILES
    assert "mcp_hosts.py" in install.NEEDED_FILES


def test_ensure_mcp_uses_python_server(monkeypatch, tmp_path):
    import mcp_hosts
    server = os.path.join(tmp_path, "mcp_server.py")
    monkeypatch.setattr(install, "TARGET", str(tmp_path))
    monkeypatch.setattr(mcp_hosts, "SERVER_PATH", server)
    calls = []

    def fake_run(cmd, timeout=30):
        calls.append(cmd)
        return 0, ""

    def fake_mcp_registered():
        # 首次（calls 为空）→ False 触发注册；注册后（calls 非空）→ True
        return bool(calls) and "mcp" in calls[0]

    monkeypatch.setattr(install, "run", fake_run)
    monkeypatch.setattr(install, "mcp_registered", fake_mcp_registered)
    ok = install.ensure_mcp()
    assert ok is True
    assert "mcp_server.py" in calls[0][-1]  # 用 python server，非 node


def test_register_all_dispatch(monkeypatch):
    import mcp_hosts
    fake = {}
    for h in mcp_hosts.HOSTS:
        fake[h] = None
    monkeypatch.setattr(mcp_hosts, "register_host", lambda h: fake.__setitem__(h, True) or [h])
    install.register_mcp("all")
    assert all(fake[h] for h in mcp_hosts.HOSTS)


def test_check_ollama_uses_config_model(monkeypatch):
    import config_loader
    calls = []

    def fake_run(cmd, timeout=600):
        calls.append(cmd)
        if cmd[0] == "ollama" and cmd[1] == "list":
            return 0, "NAME\nllava:latest\n"  # 驻留 llava，非 qwen2.5vl
        if cmd[0] == "ollama" and cmd[1] == "--version":
            return 0, "ollama version 0.1.0\n"
        return 0, ""

    monkeypatch.setattr(install, "run", fake_run)
    monkeypatch.setattr(config_loader, "get", lambda: {"ollama": {"model": "llava"}})
    ok = install.check_ollama(auto=False)
    assert ok is True  # llava 已驻留 → ✓，不触发 pull
    assert not any("pull" in c for c in calls)
    assert getattr(install, "VISION_MODEL", None) is None  # 模块级硬编码已删
