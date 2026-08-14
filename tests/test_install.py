import install


def test_needed_files_include_new():
    assert "mcp_server.py" in install.NEEDED_FILES
    assert "mcp_hosts.py" in install.NEEDED_FILES


def test_ensure_mcp_skips_when_python_registered(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "_mcp_list", lambda: "vision  claude-2  python  C:/x/mcp_server.py")
    monkeypatch.setattr(install, "run", lambda cmd, timeout=30: calls.append(cmd) or (0, ""))
    assert install.ensure_mcp() is True
    assert not any("mcp add" in " ".join(c) for c in calls)


def test_ensure_mcp_overwrites_old_node(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "_mcp_list", lambda: "vision  claude-2  node  C:/x/mcp-vision.js")
    monkeypatch.setattr(install, "run", lambda cmd, timeout=30: calls.append(cmd) or (0, ""))
    ok = install.ensure_mcp()
    assert ok is True
    add = [c for c in calls if "mcp add" in " ".join(c)]
    assert add and any("mcp_server.py" in " ".join(a) for a in add)


def test_ensure_mcp_registers_when_absent(monkeypatch):
    calls = []
    registered = []

    def fake_list():
        return "vision  claude-2  python  C:/x/mcp_server.py" if registered else ""

    def fake_run(cmd, timeout=30):
        calls.append(cmd)
        if "mcp add" in " ".join(cmd):
            registered.append(True)
        return (0, "")

    monkeypatch.setattr(install, "_mcp_list", fake_list)
    monkeypatch.setattr(install, "run", fake_run)
    assert install.ensure_mcp() is True
    assert any("mcp add" in " ".join(c) for c in calls)


def test_check_node_advisory(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "run", lambda cmd, timeout=30: calls.append(cmd) or (1, ""))
    ok = install.check_node()
    assert ok is True  # 无 node 不阻断


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
