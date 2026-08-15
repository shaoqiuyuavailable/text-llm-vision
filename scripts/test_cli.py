#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-vision CLI 回归测试（不调用真实视觉模型）。

mock vision_client 的 analyze/ocr/locate/compare，验证：
- 四个命令的参数解析与分发
- --precision/--prompt/--mode 透传
- 失败路径（缺参）返回非零
- 识别结果输出到 stdout

用法：
  python scripts/test_cli.py            # 全量
  python scripts/test_cli.py describe   # 单命令
"""
import io
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import vision_cli  # noqa: E402


class CaptureOutput(unittest.TestCase):
    def setUp(self):
        self._stdout = io.StringIO()
        self._stderr = io.StringIO()
        self._old_out, self._old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self._stdout, self._stderr

    def tearDown(self):
        sys.stdout, sys.stderr = self._old_out, self._old_err

    def out(self):
        return self._stdout.getvalue().strip()

    def err(self):
        return self._stderr.getvalue().strip()


class DescribeTest(CaptureOutput):
    def test_basic(self):
        with mock.patch.object(vision_cli.vision_client, "analyze", return_value="一张图片"):
            rc = vision_cli.cmd_describe(["a.png"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.out(), "一张图片")

    def test_precision_passthrough(self):
        with mock.patch.object(vision_cli.vision_client, "analyze", return_value="ok") as m:
            vision_cli.cmd_describe(["a.png", "--precision", "deep"])
        m.assert_called_once_with("a.png", "deep", mode="")

    def test_prompt_uses_describe(self):
        with mock.patch.object(vision_cli.vision_client, "describe", return_value="prompted") as m:
            vision_cli.cmd_describe(["a.png", "--prompt", "看文字"])
        m.assert_called_once_with("a.png", prompt="看文字")

    def test_mode_passthrough(self):
        with mock.patch.object(vision_cli.vision_client, "analyze", return_value="ok") as m:
            vision_cli.cmd_describe(["a.png", "--mode", "identity"])
        m.assert_called_once_with("a.png", "standard", mode="identity")

    def test_missing_path(self):
        rc = vision_cli.cmd_describe([])
        self.assertEqual(rc, 1)
        self.assertIn("missing image path", self.err())


class ExtractTest(CaptureOutput):
    def test_ocr_hit(self):
        with mock.patch.object(vision_cli.vision_client, "ocr", return_value="报错文字"):
            rc = vision_cli.cmd_extract(["e.png"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.out(), "报错文字")

    def test_ocr_fallback_to_describe(self):
        with mock.patch.object(vision_cli.vision_client, "ocr", return_value=""):
            with mock.patch.object(vision_cli.vision_client, "describe", return_value="视觉模型读出的字"):
                rc = vision_cli.cmd_extract(["e.png"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.out(), "视觉模型读出的字")

    def test_all_empty(self):
        with mock.patch.object(vision_cli.vision_client, "ocr", return_value=""):
            with mock.patch.object(vision_cli.vision_client, "describe", return_value=""):
                rc = vision_cli.cmd_extract(["e.png"])
        self.assertEqual(rc, 0)
        self.assertIn("未提取到文字", self.out())

    def test_missing_path(self):
        rc = vision_cli.cmd_extract([])
        self.assertEqual(rc, 1)


class LocateTest(CaptureOutput):
    def test_basic(self):
        with mock.patch.object(vision_cli.vision_client, "locate", return_value='{"x":1}') as m:
            rc = vision_cli.cmd_locate(["ui.png", "提交按钮"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.out(), '{"x":1}')
        m.assert_called_once_with("ui.png", "提交按钮")

    def test_missing_query(self):
        rc = vision_cli.cmd_locate(["ui.png"])
        self.assertEqual(rc, 1)


class CompareTest(CaptureOutput):
    def test_basic(self):
        with mock.patch.object(vision_cli.vision_client, "compare", return_value="差异说明") as m:
            rc = vision_cli.cmd_compare(["a.png", "b.png"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.out(), "差异说明")
        m.assert_called_once_with("a.png", "b.png", "standard")

    def test_precision_passthrough(self):
        with mock.patch.object(vision_cli.vision_client, "compare", return_value="ok") as m:
            vision_cli.cmd_compare(["a.png", "b.png", "--precision", "fast"])
        m.assert_called_once_with("a.png", "b.png", "fast")

    def test_missing_image_b(self):
        rc = vision_cli.cmd_compare(["a.png"])
        self.assertEqual(rc, 1)


class MainDispatchTest(CaptureOutput):
    def test_unknown_command(self):
        with mock.patch.object(vision_cli.sys, "argv", ["vision_cli.py", "nope"]):
            rc = vision_cli.main()
        self.assertEqual(rc, 1)

    def test_no_args_prints_doc(self):
        with mock.patch.object(vision_cli.sys, "argv", ["vision_cli.py"]):
            rc = vision_cli.main()
        self.assertEqual(rc, 1)
        self.assertIn("vision_cli.py", self.err())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + sys.argv[1:])
