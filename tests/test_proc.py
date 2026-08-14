import _proc


class _FakeP:
    returncode = 0

    def __init__(self, out, err=""):
        self.stdout = out
        self.stderr = err


def test_run_cmd_success(monkeypatch):
    def fake_run(args, **kw):
        return _FakeP("ok")
    monkeypatch.setattr(_proc.subprocess, "run", fake_run)
    code, out = _proc.run_cmd(["echo", "hi"])
    assert code == 0 and "ok" in out


def test_run_cmd_file_not_found_returns_127(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if len(calls) == 1:
            raise FileNotFoundError
        return _FakeP("fallback")
    monkeypatch.setattr(_proc.subprocess, "run", fake_run)
    monkeypatch.setattr(_proc.os, "name", "nt")
    code, out = _proc.run_cmd(["claude", "--version"])
    assert code == 0 and "fallback" in out
    assert calls[1][0:2] == ["cmd", "/c"]
