#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""config_loader 配置逻辑测试（不读取真实用户配置）。

覆盖：默认值、config 合并、损坏回退、端口解析、云端厂商选择、
环境变量覆盖、key 解析、后端归一化。

用法：
  python scripts/test_config_loader.py
"""
import json
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import config_loader  # noqa: E402


def write_cfg(data):
    """把给定 dict 写进临时 CONFIG_PATH 指向的文件。"""
    tmp = ROOT / ".test-config.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    patcher = mock.patch.object(config_loader, "CONFIG_PATH", str(tmp))
    patcher.start()
    return patcher


class DefaultsTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(config_loader, "CONFIG_PATH", str(ROOT / ".no-such-config.json"))
        self.p.start()
        self.addCleanup(mock.patch.stopall)

    def test_default_ollama(self):
        cfg = config_loader.get()
        self.assertEqual(cfg["ollama"]["model"], "qwen2.5vl")
        self.assertEqual(cfg["ollama"]["grounding"], True)
        self.assertIn("temperature", cfg["ollama"])

    def test_default_cloud_empty(self):
        cfg = config_loader.get()
        self.assertEqual(cfg["cloud"]["active"], "")
        self.assertEqual(cfg["cloud"]["clouds"], [])

    def test_default_router_from_prompts(self):
        cfg = config_loader.get()
        self.assertIn("_default", cfg["router"])
        self.assertIn("document.chat", cfg["router"])

    def test_default_port(self):
        self.assertEqual(config_loader.get_port(), 8787)


class ConfigMergeTest(unittest.TestCase):
    def setUp(self):
        self.p = write_cfg({"port": 9999, "ollama": {"model": "llava", "temperature": 0.7}})
        self.addCleanup(mock.patch.stopall)

    def test_port_overridden(self):
        self.assertEqual(config_loader.get_port(), 9999)

    def test_ollama_merged(self):
        cfg = config_loader.get()
        self.assertEqual(cfg["ollama"]["model"], "llava")
        self.assertEqual(cfg["ollama"]["temperature"], 0.7)
        self.assertTrue(cfg["ollama"]["grounding"])  # 未覆盖保留默认

    def test_cloud_merged(self):
        cfg = config_loader.get()
        self.assertEqual(cfg["cloud"]["active"], "")


class CorruptConfigTest(unittest.TestCase):
    def test_invalid_json_falls_back_defaults(self):
        tmp = ROOT / ".test-config.json"
        tmp.write_text("{ not valid json !!!", encoding="utf-8")
        p = mock.patch.object(config_loader, "CONFIG_PATH", str(tmp))
        p.start()
        try:
            cfg = config_loader.get()
            self.assertEqual(cfg["ollama"]["model"], "qwen2.5vl")  # 回退默认
        finally:
            p.stop()
            tmp.unlink(missing_ok=True)

    def test_non_dict_falls_back_defaults(self):
        tmp = ROOT / ".test-config.json"
        tmp.write_text("[1,2,3]", encoding="utf-8")
        p = mock.patch.object(config_loader, "CONFIG_PATH", str(tmp))
        p.start()
        try:
            cfg = config_loader.get()
            self.assertEqual(cfg["ollama"]["model"], "qwen2.5vl")
        finally:
            p.stop()
            tmp.unlink(missing_ok=True)


class PortTest(unittest.TestCase):
    def test_invalid_port_falls_back(self):
        tmp = ROOT / ".test-config.json"
        tmp.write_text(json.dumps({"port": "abc"}), encoding="utf-8")
        p = mock.patch.object(config_loader, "CONFIG_PATH", str(tmp))
        p.start()
        try:
            self.assertEqual(config_loader.get_port(), 8787)
        finally:
            p.stop()
            tmp.unlink(missing_ok=True)


class CloudKeyTest(unittest.TestCase):
    def test_env_key_preferred(self):
        c = {"name": "dashscope", "api_key": "cfg-key"}
        with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=False):
            self.assertEqual(config_loader.cloud_key_of(c), "env-key")

    def test_config_key_fallback(self):
        c = {"name": "dashscope", "api_key": "cfg-key"}
        with mock.patch.dict(os.environ, {}, clear=False):
            if "DASHSCOPE_API_KEY" in os.environ:
                del os.environ["DASHSCOPE_API_KEY"]
            self.assertEqual(config_loader.cloud_key_of(c), "cfg-key")

    def test_no_key(self):
        c = {"name": "dashscope"}
        with mock.patch.dict(os.environ, {}, clear=False):
            if "DASHSCOPE_API_KEY" in os.environ:
                del os.environ["DASHSCOPE_API_KEY"]
            self.assertEqual(config_loader.cloud_key_of(c), "")


class ActiveCloudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / ".test-config.json"
        self.addCleanup(self.tmp.unlink, missing_ok=True)
        self.addCleanup(mock.patch.stopall)

    def test_no_clouds_none(self):
        write_cfg({"cloud": {"active": "", "clouds": []}})
        self.assertIsNone(config_loader.active_cloud())

    def test_active_match(self):
        write_cfg({"cloud": {"active": "b", "clouds": [
            {"name": "a", "api_key": "k"}, {"name": "b", "api_key": "k2"}]}})
        c = config_loader.active_cloud()
        self.assertEqual(c["name"], "b")

    def test_active_missing_returns_none(self):
        write_cfg({"cloud": {"active": "zzz", "clouds": [
            {"name": "a", "api_key": "k"}]}})
        self.assertIsNone(config_loader.active_cloud())

    def test_no_active_first_with_key(self):
        write_cfg({"cloud": {"active": "", "clouds": [
            {"name": "a", "api_key": "k"}, {"name": "b"}]}})
        c = config_loader.active_cloud()
        self.assertEqual(c["name"], "a")


class UseCloudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / ".test-config.json"
        self.addCleanup(self.tmp.unlink, missing_ok=True)
        self.addCleanup(mock.patch.stopall)

    def test_provider_env_forces_local(self):
        write_cfg({"cloud": {"active": "a", "clouds": [{"name": "a", "api_key": "k"}]}})
        with mock.patch.dict(os.environ, {"VISION_PROVIDER": "local"}, clear=False):
            self.assertFalse(config_loader.use_cloud())

    def test_provider_env_forces_cloud(self):
        write_cfg({"cloud": {"active": "", "clouds": [{"name": "a"}]}})
        with mock.patch.dict(os.environ, {"VISION_PROVIDER": "cloud"}, clear=False):
            self.assertTrue(config_loader.use_cloud())

    def test_no_key_local(self):
        write_cfg({"cloud": {"active": "a", "clouds": [{"name": "a"}]}})
        with mock.patch.dict(os.environ, {}, clear=False):
            if "A_API_KEY" in os.environ:
                del os.environ["A_API_KEY"]
            self.assertFalse(config_loader.use_cloud())

    def test_key_cloud(self):
        write_cfg({"cloud": {"active": "a", "clouds": [{"name": "a", "api_key": "k"}]}})
        with mock.patch.dict(os.environ, {}, clear=False):
            self.assertTrue(config_loader.use_cloud())


class ResolveBackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / ".test-config.json"
        self.addCleanup(self.tmp.unlink, missing_ok=True)
        self.addCleanup(mock.patch.stopall)

    def test_local_backend(self):
        write_cfg({"ollama": {"model": "qwen2.5vl"}, "cloud": {"active": "", "clouds": []}})
        b = config_loader.resolve_backend()
        self.assertEqual(b["provider"], "local")
        self.assertEqual(b["model"], "qwen2.5vl")

    def test_cloud_backend(self):
        write_cfg({"ollama": {"model": "qwen2.5vl"}, "cloud": {"active": "d", "clouds": [
            {"name": "d", "model": "qwen-vl-max", "api_key": "k"}]}})
        b = config_loader.resolve_backend()
        self.assertEqual(b["provider"], "cloud")
        self.assertEqual(b["model"], "qwen-vl-max")
        self.assertEqual(b["active"], "d")


class EnvOverrideTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(config_loader, "CONFIG_PATH", str(ROOT / ".no-such-config.json"))
        self.p.start()
        self.addCleanup(mock.patch.stopall)

    def test_env_url_override(self):
        with mock.patch.dict(os.environ, {"OLLAMA_URL": "http://env:11434/api/generate"}, clear=False):
            cfg = config_loader.get()
            self.assertEqual(cfg["ollama"]["url"], "http://env:11434/api/generate")

    def test_env_model_override(self):
        with mock.patch.dict(os.environ, {"VISION_MODEL": "env-model"}, clear=False):
            cfg = config_loader.get()
            self.assertEqual(cfg["ollama"]["model"], "env-model")

    def test_env_key_without_base_no_cloud(self):
        with mock.patch.dict(os.environ, {"VISION_API_KEY": "k"}, clear=False):
            if "VISION_API_BASE_URL" in os.environ:
                del os.environ["VISION_API_BASE_URL"]
            cfg = config_loader.get()
            self.assertEqual(cfg["cloud"]["clouds"], [])

    def test_env_key_with_base_injects_cloud(self):
        with mock.patch.dict(os.environ, {
            "VISION_API_KEY": "k", "VISION_API_BASE_URL": "https://x/v1", "VISION_MODEL": "m",
        }, clear=False):
            cfg = config_loader.get()
            self.assertEqual(cfg["cloud"]["clouds"][0]["name"], "env")
            self.assertEqual(cfg["cloud"]["active"], "env")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + sys.argv[1:])
