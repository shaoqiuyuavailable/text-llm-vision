#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vision_client 引擎核心逻辑测试（不调用真实视觉模型/网络）。

覆盖：路由解析、引擎注册/回退、输出清洗、缓存开关、缩放、
云端厂商选择、模型级覆盖、OCR 回退、模式温度、zoom/guess 拼装。

用法：
  python scripts/test_vision_client.py
"""
import io
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import vision_client  # noqa: E402
import config_loader  # noqa: E402

# 测试用固定配置（路由/场景/提示词基线随 prompts.py 部署）
def base_cfg():
    return {
        "ollama": {"model": "qwen2.5vl", "url": "http://localhost:11434/api/generate",
                   "temperature": 0.5, "top_p": 0.8, "grounding": True, "precision": "standard"},
        "cloud": {"active": "", "clouds": []},
        "router": {"document.chat": "ocr", "document.code": "code", "chart": "vlm", "_default": "vlm"},
        "models": {"qwen2.5vl": {"type": "ollama", "purpose": "default"}},
        "scenes": {"document": {"sub": ["code", "chat"], "default_sub": "report"},
                   "screenshot": {"sub": ["software_ui"], "default_sub": ""}},
        "prompts": {"scan": {"text": "描述", "temperature": 0.3},
                    "zoom_document": {"text": "提取", "temperature": 0.2},
                    "guess": {"text": "推测", "temperature": 0.4}},
        "modes": {"identity": 0.1},
    }


def set_cfg(cfg):
    """注入测试配置：patch config_loader.get 返回给定 dict（不透真配置/基线合并）。"""
    patcher = mock.patch.object(config_loader, "get", return_value=cfg)
    patcher.start()
    unittest.TestCase.addCleanup  # noqa: B018 -- 只是引用，cleanup 由调用方管理
    return patcher


class RouteTest(unittest.TestCase):
    def setUp(self):
        set_cfg(base_cfg())
        self.addCleanup(mock.patch.stopall)

    def test_scene_sub_exact(self):
        self.assertEqual(vision_client._route_engine("document", "chat"), "ocr")

    def test_scene_sub_code(self):
        self.assertEqual(vision_client._route_engine("document", "code"), "code")

    def test_scene_only(self):
        self.assertEqual(vision_client._route_engine("chart", ""), "vlm")

    def test_unknown_falls_back_default(self):
        self.assertEqual(vision_client._route_engine("unknown", "x"), "vlm")

    def test_empty_table_default(self):
        # config 无 router 键 → 路由为空表（不注入基线时）→ 回退 vlm
        cfg = base_cfg()
        cfg["router"] = {}
        set_cfg(cfg)
        self.assertEqual(vision_client._route_engine("document", "chat"), "vlm")


class ParseRouteValueTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(vision_client._parse_route_value(""), ("vlm", ""))

    def test_engine_only(self):
        self.assertEqual(vision_client._parse_route_value("ocr"), ("ocr", ""))

    def test_engine_model(self):
        self.assertEqual(vision_client._parse_route_value("vlm:llava"), ("vlm", "llava"))

    def test_whitespace(self):
        self.assertEqual(vision_client._parse_route_value("  vlm : qwen  "), ("vlm", "qwen"))


class SanitizeTest(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(vision_client._sanitize("正常文本 abc"), "正常文本 abc")

    def test_empty(self):
        self.assertEqual(vision_client._sanitize(""), "")

    def test_none_passthrough(self):
        self.assertIsNone(vision_client._sanitize(None))

    def test_surrogate_replaced(self):
        s = "bad\udcffchar"
        out = vision_client._sanitize(s)
        self.assertNotIn("\udcff", out)
        self.assertIn("?", out)


class CacheSwitchTest(unittest.TestCase):
    def test_state_3_enabled(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="3")):
            self.assertTrue(vision_client._cache_on())

    def test_state_2_disabled(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="2")):
            self.assertFalse(vision_client._cache_on())

    def test_state_missing(self):
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertFalse(vision_client._cache_on())

    def test_clear_cache(self):
        vision_client._cache["k"] = "v"
        vision_client.clear_cache()
        self.assertEqual(vision_client._cache, {})


class DownscaleTest(unittest.TestCase):
    def _png_b64(self, w, h):
        import base64
        import io as _io
        from PIL import Image
        img = Image.new("RGB", (w, h), (255, 0, 0))
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def test_small_unchanged(self):
        b64 = self._png_b64(100, 50)
        self.assertEqual(vision_client._downscale_b64(b64), b64)

    def test_large_downscaled(self):
        b64 = self._png_b64(3000, 2000)
        out = vision_client._downscale_b64(b64)
        self.assertNotEqual(out, b64)
        import base64, io as _io
        from PIL import Image
        img = Image.open(_io.BytesIO(base64.b64decode(out)))
        self.assertLessEqual(max(img.size), vision_client.MAX_EDGE)

    def test_invalid_b64_passthrough(self):
        self.assertEqual(vision_client._downscale_b64("not-base64!"), "not-base64!")

    def test_empty(self):
        self.assertEqual(vision_client._downscale_b64(""), "")


class CloudSelectionTest(unittest.TestCase):
    def setUp(self):
        set_cfg(base_cfg())
        self.addCleanup(mock.patch.stopall)

    def test_no_cloud_local(self):
        self.assertFalse(vision_client._use_cloud())

    def test_active_with_key(self):
        cfg = base_cfg()
        cfg["cloud"] = {"active": "dashscope", "clouds": [
            {"name": "dashscope", "base_url": "https://x/v1", "model": "qwen-vl-plus", "api_key": "sk-1"}]}
        set_cfg(cfg)
        self.assertTrue(vision_client._use_cloud())
        self.assertEqual(vision_client._cloud_of("dashscope")["model"], "qwen-vl-plus")
        self.assertIsNone(vision_client._cloud_of("nonexistent"))

    def test_cloud_without_key_falls_back_local(self):
        cfg = base_cfg()
        cfg["cloud"] = {"active": "dashscope", "clouds": [
            {"name": "dashscope", "base_url": "https://x/v1", "model": "qwen-vl-plus"}]}
        set_cfg(cfg)
        self.assertFalse(vision_client._use_cloud())  # 无 key → 本地


class ModelOverrideTest(unittest.TestCase):
    """_post_b64 的模型级覆盖：models 表 type=cloud → 云端；否则本地。"""

    def setUp(self):
        set_cfg(base_cfg())
        self.addCleanup(mock.patch.stopall)

    def test_explicit_cloud_model_routes_to_cloud(self):
        cfg = base_cfg()
        cfg["models"] = {"qwen-vl-max": {"type": "cloud", "provider": "dashscope", "purpose": "chart"}}
        cfg["cloud"] = {"active": "dashscope", "clouds": [
            {"name": "dashscope", "base_url": "https://x/v1", "model": "qwen-vl-max", "api_key": "sk-1"}]}
        set_cfg(cfg)
        with mock.patch.object(vision_client, "_post_cloud", return_value="cloud-result") as pc:
            with mock.patch.object(vision_client, "_cache_on", return_value=False):
                out = vision_client._post_b64("b64", "prompt", 0.5, model="qwen-vl-max")
        pc.assert_called_once()
        self.assertEqual(out, "cloud-result")

    def test_explicit_ollama_model_routes_to_local(self):
        cfg = base_cfg()
        cfg["models"] = {"llava": {"type": "ollama", "purpose": "default"}}
        set_cfg(cfg)
        with mock.patch("httpx.post") as post:
            post.return_value.raise_for_status = mock.Mock()
            post.return_value.json = mock.Mock(return_value={"response": "local-result"})
            with mock.patch.object(vision_client, "_cache_on", return_value=False):
                out = vision_client._post_b64("b64", "prompt", 0.5, model="llava")
        args = post.call_args
        self.assertIn("llava", args.kwargs["json"]["model"])
        self.assertEqual(out, "local-result")


class ModeTemperatureTest(unittest.TestCase):
    def setUp(self):
        set_cfg(base_cfg())
        self.addCleanup(mock.patch.stopall)

    def test_known_mode(self):
        self.assertEqual(vision_client._mode_temperature("identity"), 0.1)

    def test_unknown_mode_none(self):
        self.assertIsNone(vision_client._mode_temperature("nope"))

    def test_empty_mode_none(self):
        self.assertIsNone(vision_client._mode_temperature(""))


class EngineRegistryTest(unittest.TestCase):
    def setUp(self):
        set_cfg(base_cfg())
        self.addCleanup(mock.patch.stopall)

    def test_all_engines_registered(self):
        for eng in ("ocr", "vlm", "table", "gui", "code"):
            self.assertIn(eng, vision_client._ENGINES)

    def test_run_engine_unknown_returns_empty(self):
        # _run_engine 未知引擎返回空串；vlm 回退由 analyze 调用方负责
        out = vision_client._run_engine("nonexistent", "p", "chart", "", "desc")
        self.assertEqual(out, "")

    def test_run_engine_exception_falls_back(self):
        def boom(*_a, **_k):
            raise RuntimeError("boom")
        with mock.patch.dict(vision_client._ENGINES, {"vlm": boom}):
            out = vision_client._run_engine("vlm", "p", "chart", "", "desc")
        self.assertEqual(out, "")


class OcrTest(unittest.TestCase):
    def test_ocr_text_extracted(self):
        fake_engine = mock.Mock(return_value=([(None, "第一行"), (None, "第二行")], None))
        with mock.patch.object(vision_client, "_get_ocr_engine", return_value=fake_engine):
            with mock.patch.object(vision_client.os.path, "exists", return_value=True):
                out = vision_client.ocr("fake.png")
        self.assertEqual(out, "第一行\n第二行")

    def test_ocr_empty_on_exception(self):
        with mock.patch.object(vision_client, "_get_ocr_engine", side_effect=Exception("no rapidocr")):
            self.assertEqual(vision_client.ocr("fake.png"), "")


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        set_cfg(base_cfg())
        self.addCleanup(mock.patch.stopall)

    def test_fast_single_scan(self):
        set_cfg(base_cfg())
        with mock.patch.object(vision_client, "scan", return_value=("一张图", "object", "", [])):
            out = vision_client.analyze("fake.png", "fast")
        self.assertIn("【初步判断】一张图", out)
        self.assertIn("【场景】object", out)

    def test_fast_with_question_hints_level(self):
        set_cfg(base_cfg())
        with mock.patch.object(vision_client, "scan", return_value=("一张图", "object", "", [])):
            out = vision_client.analyze("fake.png", "fast", question="这是什么")
        self.assertIn("fast 档", out)

    def test_standard_routes_ocr(self):
        set_cfg(base_cfg())
        ocr_mock = mock.Mock(return_value="[OCR 提取的文字]\n你好")
        vlm_mock = mock.Mock(return_value="")
        with mock.patch.object(vision_client, "scan", return_value=("聊天记录", "document", "chat", [])):
            with mock.patch.dict(vision_client._ENGINES, {"ocr": ocr_mock, "vlm": vlm_mock}, clear=False):
                out = vision_client.analyze("fake.png", "standard")
        ocr_mock.assert_called_once()
        vlm_mock.assert_not_called()
        self.assertIn("[OCR]", out)

    def test_engine_failure_falls_back_vlm(self):
        set_cfg(base_cfg())
        ocr_mock = mock.Mock(return_value="")
        vlm_mock = mock.Mock(return_value="vlm fallback")
        with mock.patch.object(vision_client, "scan", return_value=("图", "document", "chat", [])):
            with mock.patch.dict(vision_client._ENGINES, {"ocr": ocr_mock, "vlm": vlm_mock}, clear=False):
                out = vision_client.analyze("fake.png", "standard")
        ocr_mock.assert_called_once()
        vlm_mock.assert_called_once()
        self.assertIn("vlm fallback", out)

    def test_all_engines_fail_placeholder(self):
        set_cfg(base_cfg())
        ocr_mock = mock.Mock(return_value="")
        vlm_mock = mock.Mock(return_value="")
        with mock.patch.object(vision_client, "scan", return_value=("图", "document", "chat", [])):
            with mock.patch.dict(vision_client._ENGINES, {"ocr": ocr_mock, "vlm": vlm_mock}, clear=False):
                out = vision_client.analyze("fake.png", "standard")
        self.assertIn("识别失败（引擎异常）", out)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + sys.argv[1:])
