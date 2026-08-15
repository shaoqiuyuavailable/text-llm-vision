import io
import json

import mcp_server


def _call(msg):
    out = io.StringIO()
    mcp_server._handle(msg, out)
    lines = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    return lines[0] if lines else None


def test_initialize():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "vision-mcp"


def test_tools_list_has_five():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in r["result"]["tools"]]
    assert names == ["describe_image", "extract_text", "locate_object", "compare_images", "vision_rules"]


def test_unknown_method():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "foo/bar"})
    assert r["error"]["code"] == -32601


def test_unknown_tool():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "nope", "arguments": {}}})
    assert r["error"]["code"] == -32602


def test_describe_image_calls_analyze(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    calls = {}

    def fake_analyze(p, precision="", mode="", question=""):
        calls["precision"] = precision
        calls["question"] = question
        return f"识别:{p}"

    monkeypatch.setattr(mcp_server.vision_client, "analyze", fake_analyze)
    monkeypatch.setattr(mcp_server, "_describe_precision", lambda: "deep")  # 档位对齐主路径（F1）
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "describe_image", "arguments": {"image": str(img)}}})
    assert r["result"]["content"][0]["text"] == f"识别:{img}"
    assert calls["precision"] == "deep"
    assert calls["question"] == ""  # 无提问 → question 空
    assert r["result"]["isError"] is False


def test_describe_image_mode_routes_to_analyze(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    calls = {}

    def fake_analyze(p, precision="", mode="", question=""):
        calls["mode"] = mode
        calls["question"] = question
        return f"MODE:{mode}|Q:{question}"

    monkeypatch.setattr(mcp_server.vision_client, "analyze", fake_analyze)
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "describe_image", "arguments": {"image": str(img), "mode": "anime"}}})
    assert calls["mode"] == "anime"
    assert calls["question"] == ""
    assert r["result"]["content"][0]["text"] == "MODE:anime|Q:"


def test_describe_image_prompt_routes_to_analyze_with_question(monkeypatch, tmp_path):
    # 用户具体需求（prompt）→ 驱动完整管线（question 传 analyze），不再裸问绕过分层
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    calls = {}

    def fake_analyze(p, precision="", mode="", question=""):
        calls["question"] = question
        calls["mode"] = mode
        return f"ANALYZE:{question}"

    monkeypatch.setattr(mcp_server.vision_client, "analyze", fake_analyze)
    monkeypatch.setattr(mcp_server.vision_client, "describe", lambda p, prompt="": "不应走 describe")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "describe_image", "arguments": {"image": str(img), "prompt": "数数几个人"}}})
    assert r["result"]["content"][0]["text"] == "ANALYZE:数数几个人"
    assert r["result"]["isError"] is False
    assert calls["question"] == "数数几个人"
    assert calls["mode"] == ""


def test_describe_image_missing_or_bad_path(tmp_path):
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "describe_image", "arguments": {}}})
    assert r["result"]["isError"] is True
    r2 = _call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "describe_image", "arguments": {"image": str(tmp_path / "nope.png")}}})
    assert r2["result"]["isError"] is True


def test_extract_text_ocr_empty_falls_back(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "ocr", lambda p: "")
    monkeypatch.setattr(mcp_server.vision_client, "describe", lambda p, prompt="": "OCR 回退文字")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "extract_text", "arguments": {"image": str(img)}}})
    assert "OCR 回退文字" in r["result"]["content"][0]["text"]


def test_extract_text_ocr_hit(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "ocr", lambda p: "报错栈文字")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "extract_text", "arguments": {"image": str(img)}}})
    assert r["result"]["content"][0]["text"] == "报错栈文字"


def test_locate_object_calls_locate(monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "locate",
                        lambda p, q: f'[{{"name":"{q}","bbox":[0,0,1,1]}}]')
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "locate_object", "arguments": {"image": str(img), "query": "按钮"}}})
    assert "按钮" in r["result"]["content"][0]["text"]


def test_compare_images_calls_compare(monkeypatch, tmp_path):
    a = tmp_path / "a.png"; a.write_bytes(b"x")
    b = tmp_path / "b.png"; b.write_bytes(b"x")
    monkeypatch.setattr(mcp_server.vision_client, "compare", lambda x, y: f"diff:{x}|{y}")
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "compare_images", "arguments": {"image_a": str(a), "image_b": str(b)}}})
    assert f"diff:{a}|{b}" in r["result"]["content"][0]["text"]


def test_vision_rules_returns_text():
    r = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "vision_rules", "arguments": {}}})
    assert "describe_image" in r["result"]["content"][0]["text"]
