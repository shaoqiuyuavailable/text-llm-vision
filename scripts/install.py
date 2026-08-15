#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-vision 一键安装/配置脚本（自包含版）。

所有步骤均为可选项，按需组合；无参数运行进入交互模式逐项选择。

用法示例：
  python scripts/install.py                # 交互模式：逐项选择
  python scripts/install.py --check        # 只做环境检查（Python/Ollama/依赖/模型）
  python scripts/install.py --deps         # 只装 Python 依赖
  python scripts/install.py --local        # 只配本地引擎（检测/拉取 Ollama 视觉模型）
  python scripts/install.py --cloud        # 只配云端通道（厂商模板 + key）
  python scripts/install.py --deploy       # 部署识别库到 ~/.dsh/vision
  python scripts/install.py --mount        # 挂载插件到 dsh profile（pnpm dsh plugin）
  python scripts/install.py --test         # 跑 CLI 回归测试
  python scripts/install.py --all          # 全流程（依赖→本地→云端→部署→测试）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_DIR = ROOT / "python"
TARGET = Path(os.path.expanduser("~/.dsh/vision"))
CORE = ["vision_client.py", "config_loader.py", "prompts.py", "config.json", "requirements.txt"]

# 云端厂商模板（OpenAI 兼容）：name → (base_url, [model...])
CLOUD_TEMPLATES = {
    "dashscope（阿里云通义）": {
        "name": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-vl-plus", "qwen-vl-max"],
    },
    "gemini（Google，OpenAI 兼容端点）": {
        "name": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-2.5-flash", "gemini-2.0-flash"],
    },
    "zhipu（智谱 GLM）": {
        "name": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4v-plus", "glm-4v-flash"],
    },
    "自定义（任意 OpenAI 兼容）": {
        "name": "",
        "base_url": "",
        "models": [],
    },
}

ok = True


def check(cond, name, hint=""):
    global ok
    print(("✅" if cond else "⚠️ " if hint.startswith("可选") else "❌") + f"  {name}" + (f"  ({hint})" if hint and not cond else ""))
    if not cond:
        ok = False


def ask(question, default=""):
    """交互提问，返回用户输入（去空白）。"""
    suffix = f" [{default}]" if default else ""
    try:
        return input(f"{question}{suffix}: ").strip() or default
    except EOFError:
        return default


def yesno(question, default=True):
    """交互确认。"""
    suffix = "Y/n" if default else "y/N"
    try:
        return input(f"{question} ({suffix}) ").strip().lower() in ("", "y", "yes") if default \
            else input(f"{question} ({suffix}) ").strip().lower() in ("y", "yes")
    except EOFError:
        return default


# ---------- 步骤：环境检查 ----------

def step_check() -> int:
    print("\n== 环境检查 ==")
    check(sys.version_info >= (3, 9), f"Python {sys.version_info.major}.{sys.version_info.minor}", "需 3.9+")
    for mod in ("httpx", "PIL"):
        try:
            __import__(mod)
            check(True, f"依赖 {mod}")
        except ImportError:
            check(False, f"依赖 {mod}", f"缺少 {mod}，运行 --deps 安装")
    # Ollama 服务
    ollama = shutil.which("ollama")
    if ollama:
        check(True, "Ollama CLI 存在")
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            models = [line.split()[0] for line in r.stdout.strip().splitlines()[1:]]
            check(True, f"Ollama 服务运行中（{len(models)} 个模型）", "运行 ollama serve")
            vision = [m for m in models if any(k in m.lower() for k in ("vl", "vision", "llava", "minicpm"))]
            if vision:
                check(True, f"视觉模型: {', '.join(vision)}")
            else:
                check(False, "视觉模型（qwen2.5vl 等）", "运行 --local 拉取")
        else:
            check(False, "Ollama 服务", "启动 Ollama 桌面应用或 ollama serve")
    else:
        check(False, "Ollama CLI", "未安装 Ollama，https://ollama.com")
    # 云端通道
    cfg = _read_cfg()
    clouds = (cfg.get("cloud") or {}).get("clouds") or []
    if clouds:
        check(True, f"云端通道已配置（{len(clouds)} 个厂商: {', '.join(c.get('name','?') for c in clouds)}）")
    else:
        check(True, "云端通道未配置（可选）", "可选：运行 --cloud 添加云端厂商提升精度上限")
    print("\n环境检查完成。" + ("✅ 全部就绪" if ok else "⚠️ 有未通过项，按提示修复"))
    return 0 if ok else 1


# ---------- 步骤：Python 依赖 ----------

def step_deps() -> int:
    print("\n== 安装 Python 依赖 ==")
    req = PY_DIR / "requirements.txt"
    if not req.exists():
        print("✗ 缺少 python/requirements.txt")
        return 1
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)])
    print("依赖安装" + ("完成 ✅" if r.returncode == 0 else "失败 ❌"))
    return r.returncode


# ---------- 步骤：本地引擎 ----------

def step_local() -> int:
    print("\n== 本地引擎（Ollama 视觉模型）==")
    ollama = shutil.which("ollama")
    if not ollama:
        print("✗ 未找到 Ollama CLI，请先安装 https://ollama.com")
        return 1
    r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print("✗ Ollama 服务未运行，请启动后重试")
        return 1
    existing = [line.split()[0] for line in r.stdout.strip().splitlines()[1:]]
    vision = [m for m in existing if any(k in m.lower() for k in ("vl", "vision", "llava", "minicpm"))]
    if vision:
        print(f"✓ 已有视觉模型: {', '.join(vision)}")
        return 0
    default_model = "qwen2.5vl"
    model = ask(f"要拉取的视觉模型（默认 {default_model}；13B 需 ~8GB 显存，7B 需 ~6GB）", default_model)
    if not model:
        model = default_model
    print(f"  拉取 {model}（首次约 1-6GB，取决于网络）...")
    r = subprocess.run(["ollama", "pull", model])
    if r.returncode != 0:
        print(f"✗ 拉取 {model} 失败")
        return 1
    # 写入插件自带配置
    cfg = _read_cfg()
    cfg.setdefault("ollama", {})["model"] = model
    _write_cfg(cfg)
    print(f"✓ 本地引擎就绪（{model}），已写入配置")
    return 0


# ---------- 步骤：云端通道 ----------

def step_cloud() -> int:
    print("\n== 云端通道（可选，提升精度上限）==")
    print("  云端用大模型（qwen-vl-max / gemini / glm-4v 等）识别，精度不受本地显卡限制。")
    print("  任意 OpenAI 兼容端点均可；key 优先读环境变量 <厂商大写>_API_KEY，其次写 config。")
    print()
    choices = list(CLOUD_TEMPLATES.keys())
    for i, name in enumerate(choices, 1):
        print(f"  {i}. {name}")
    try:
        sel = int(ask(f"选择厂商 (1-{len(choices)})", "1") or "1")
        if not 1 <= sel <= len(choices):
            raise ValueError
    except ValueError:
        print("✗ 无效选择")
        return 1
    tpl = CLOUD_TEMPLATES[choices[sel - 1]]
    name = tpl["name"]
    if not name:  # 自定义
        name = ask("厂商名（同时是 <NAME>_API_KEY 环境变量名，如 mycloud）", "")
        if not name:
            print("✗ 厂商名不能为空")
            return 1
        base = ask("OpenAI 兼容 base URL（如 https://api.example.com/v1）", "")
        if not base:
            print("✗ base URL 不能为空")
            return 1
        models = [ask("默认模型名", "")]
    else:
        base = tpl["base_url"]
        print(f"  base_url: {base}")
        models = tpl["models"]
        model = ask(f"模型（默认 {models[0]}）", models[0]) or models[0]
        models = [model]
    api_key = ask("API key（留空则后续用环境变量 <NAME>_API_KEY）", "")
    if api_key:
        print("  ⚠️  key 将写入 config.json（明文）。更安全的方式：不填 key，")
        print(f"     手动设置环境变量 {name.upper()}_API_KEY 后重启 dsh。")
        if yesno("  确认明文写入 config.json？", False):
            pass
        else:
            api_key = ""
    cfg = _read_cfg()
    clouds = cfg.setdefault("cloud", {}).setdefault("clouds", [])
    entry = {"name": name, "base_url": base, "model": models[0]}
    if api_key:
        entry["api_key"] = api_key
    clouds[:] = [c for c in clouds if c.get("name") != name] + [entry]
    cfg["cloud"]["active"] = name
    _write_cfg(cfg)
    print(f"✓ 云端厂商 {name} 已配置并激活（模型 {models[0]}）")
    if not api_key:
        print(f"  → 还需设置环境变量 {name.upper()}_API_KEY 才能走云端；未设置时自动回退本地 Ollama。")
    return 0


# ---------- 步骤：部署到 ~/.dsh/vision ----------

def step_deploy() -> int:
    print("\n== 部署识别库到 ~/.dsh/vision ==")
    TARGET.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in CORE:
        s = PY_DIR / name
        if s.exists():
            shutil.copy2(s, TARGET / name)
            copied += 1
    print(f"✓ 已复制 {copied} 个核心文件 -> {TARGET}")
    return 0


# ---------- 步骤：挂载到 dsh profile ----------

def step_mount() -> int:
    print("\n== 挂载插件到 dsh profile ==")
    plugin_dir = str(ROOT)
    r = subprocess.run(
        ["pnpm", "dsh", "plugin", "--profile", "web", "add", plugin_dir],
        cwd=str(ROOT.parent.parent) if (ROOT.parent.parent / "package.json").exists() else str(ROOT),
    )
    if r.returncode != 0:
        print("✗ 挂载失败（确认 dsh 已安装、pnpm 在 PATH）")
        print("  手动挂载：pnpm dsh plugin --profile web add <插件目录>")
        return 1
    print("✓ 已挂载，重启 dsh 后生效")
    return 0


# ---------- 步骤：回归测试 ----------

def step_test() -> int:
    print("\n== CLI 回归测试 ==")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "test_cli.py")])
    return r.returncode


# ---------- 配置读写 ----------

def _read_cfg() -> dict:
    p = TARGET / "config.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (ValueError, OSError):
            pass
    # 回退插件自带
    local = PY_DIR / "config.json"
    if local.exists():
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (ValueError, OSError):
            pass
    return {}


def _write_cfg(cfg: dict):
    """写入 ~/.dsh/vision/config.json（GUI 与 vision_cli 的共享配置）。"""
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- 主流程 ----------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description="dsh-vision 一键安装/配置（所有步骤可选）")
    ap.add_argument("--check", action="store_true", help="环境检查")
    ap.add_argument("--deps", action="store_true", help="安装 Python 依赖")
    ap.add_argument("--local", action="store_true", help="配置本地引擎（Ollama 视觉模型）")
    ap.add_argument("--cloud", action="store_true", help="配置云端通道（厂商 + key）")
    ap.add_argument("--deploy", action="store_true", help="部署识别库到 ~/.dsh/vision")
    ap.add_argument("--mount", action="store_true", help="挂载插件到 dsh profile")
    ap.add_argument("--test", action="store_true", help="跑 CLI 回归测试")
    ap.add_argument("--all", action="store_true", help="全流程：deps→local→cloud→deploy→test")
    args = ap.parse_args()

    print("== dsh-vision 安装脚本 ==")
    if args.all:
        args.deps = args.local = args.cloud = args.deploy = args.test = True

    steps = []
    if args.check:
        steps.append(("环境检查", step_check))
    if args.deps:
        steps.append(("Python 依赖", step_deps))
    if args.local:
        steps.append(("本地引擎", step_local))
    if args.cloud:
        steps.append(("云端通道", step_cloud))
    if args.deploy:
        steps.append(("部署到 ~/.dsh/vision", step_deploy))
    if args.test:
        steps.append(("回归测试", step_test))

    if not steps:
        # 交互模式：逐项询问
        print("  无参数进入交互模式（也可用 --check/--deps/--local/--cloud/--deploy/--mount/--test 直达）")
        if yesno("  1. 环境检查？", True):
            steps.append(("环境检查", step_check))
        if yesno("  2. 安装 Python 依赖？", True):
            steps.append(("Python 依赖", step_deps))
        if yesno("  3. 配置本地引擎（Ollama 视觉模型）？", True):
            steps.append(("本地引擎", step_local))
        if yesno("  4. 配置云端通道（可选，提升精度上限）？", False):
            steps.append(("云端通道", step_cloud))
        if yesno("  5. 部署识别库到 ~/.dsh/vision？", False):
            steps.append(("部署", step_deploy))
        if yesno("  6. 挂载插件到 dsh profile？", False):
            steps.append(("挂载", step_mount))
        if yesno("  7. 跑回归测试？", True):
            steps.append(("回归测试", step_test))
        if not steps:
            print("未选择任何步骤，退出。")
            return 0

    rc = 0
    for name, fn in steps:
        try:
            r = fn()
        except KeyboardInterrupt:
            print("\n已中断。")
            return 130
        if r != 0:
            rc = r
            print(f"⚠️ 步骤「{name}」失败（退出码 {r}），继续后续步骤...")
    print("\n完成。下一步：重启 dsh → 设置 → 插件 → dsh-vision 检查配置。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
