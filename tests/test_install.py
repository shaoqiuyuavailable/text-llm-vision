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
    import mcp_hosts
    calls = []
    removed = []

    def fake_list():
        # remove 执行后 = 新注册完成（mcp_server.py 出现）
        return "vision  claude-2  python  C:/x/mcp_server.py" if removed else "vision  claude-2  node  C:/x/mcp-vision.js"

    def fake_cmd(args, timeout=20):
        calls.append(list(args))
        if args[:3] == ["claude", "mcp", "list"]:
            return 0, "vision ... mcp-vision.js"
        if "remove" in args:
            removed.append(True)
            return 0, "Removed"
        if "add" in args:
            return 0, "Added"
        return 0, ""

    monkeypatch.setattr(install, "_mcp_list", fake_list)
    monkeypatch.setattr(mcp_hosts, "_cmd", fake_cmd)
    monkeypatch.setattr(mcp_hosts, "_python_cmd", lambda: "python")
    ok = install.ensure_mcp()
    assert ok is True
    assert any("remove" in c for c in calls)  # 覆盖 = remove→add
    assert any("add" in c and any("mcp_server.py" in str(a) for a in c) for c in calls)


def test_ensure_mcp_registers_when_absent(monkeypatch):
    import mcp_hosts
    calls = []

    def fake_list():
        return "vision ... mcp_server.py" if any("add" in c for c in calls) else ""

    def fake_cmd(args, timeout=20):
        calls.append(list(args))
        if args[:3] == ["claude", "mcp", "list"]:
            return 0, ""  # 未注册
        if "add" in args:
            return 0, "Added"
        return 0, ""

    monkeypatch.setattr(install, "_mcp_list", fake_list)
    monkeypatch.setattr(mcp_hosts, "_cmd", fake_cmd)
    monkeypatch.setattr(mcp_hosts, "_python_cmd", lambda: "python")
    assert install.ensure_mcp() is True
    assert any("add" in c for c in calls)


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
