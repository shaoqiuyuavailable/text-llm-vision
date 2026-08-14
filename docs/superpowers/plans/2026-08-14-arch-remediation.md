# 架构审查整改（A-F 重点）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 12 维架构审查确认的 A-F 根因组（后端路由单一事实来源、env 云安全、去硬编码模型、新旧 MCP 迁移断层、复用/清理），回归全绿。

**Architecture:** 「本地 vs 云端」决策收敛进 config_loader（`cloud_key_of/active_cloud/cloud_key/use_cloud`），vision_client 只向下委托；新建 stdlib-only `_proc.py` 归口子进程辅助（三处保留薄封装）；install/toggle 模型名改从 config_loader 读；ensure_mcp/doctor 区分新旧 MCP 形态。

**Tech Stack:** Python 3.10+，stdlib-only 新模块。测试 pytest。

**Spec:** `docs/superpowers/specs/2026-08-14-mcp-primary-path-design.md`（上游设计） + `C:\Users\shaoqiu yu\.claude\plans\qwen-sunny-meadow.md`（本轮整改计划，用户已批准）

## Global Constraints

- 分支 `feat/mcp-primary-path`（HEAD 9490a75），全部改动落在此分支。
- **循环依赖红线**：`config_loader` 永不 `import vision_client`（vision_client 顶部已 import config_loader）。
- `mcp_hosts.py` 保持零业务依赖：只 import stdlib + `_proc.py`（不 import config_loader/vision_client 等）。
- 新增 .py 必须同时进 `install.NEEDED_FILES` 与 `Dockerfile COPY`。
- `install.run` / `mcp_hosts._cmd` / `toggle._cmd` 保留**同名薄封装**委托 `_proc.run_cmd`（保住 test_install / test_mcp_hosts 的 monkeypatch 点）。
- 测试 `python -m pytest tests/ -v` 全绿（基线 33）；新测试遵循现有 monkeypatch 风格，不得真实请求 Ollama/云端/执行 claude mcp。
- B/A 同一次提交序列（B 单独落地会让 resolve_backend 与实际路由临时不一致）。
- 提交前缀 `refactor:`（重构）/ `fix:` / `docs:`。

---

### Task 1: _proc.py 子进程辅助归口

**Files:**
- Create: `_proc.py`
- Modify: `install.py`（run 薄封装 + NEEDED_FILES）、`toggle.py`（_cmd 薄封装）、`mcp_hosts.py`（_cmd 薄封装）、`Dockerfile`（COPY）
- Test: `tests/test_proc.py`

**Interfaces:**
- Produces: `_proc.run_cmd(cmd: list, timeout: int = 30) -> (returncode, output)`。内容 = 现 `install.run` 主体（CREATE_NO_WINDOW + `cmd /c` 回退 + 127/124），仅 `import os, subprocess`。
- Task 4/5/6 依赖三处薄封装已就位。

- [ ] **Step 1: 写失败测试**

`tests/test_proc.py`：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_proc.py -v`
Expected: FAIL（ModuleNotFoundError: No module named '_proc'）

- [ ] **Step 3: 实现**

Create `_proc.py`：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""子进程辅助：install/toggle/mcp_hosts 共用（stdlib-only，Windows 平台坑单点维护）。

Windows 上 npm 安装的 claude 是 .cmd 批处理，list 形式直接执行报 FileNotFoundError
（WinError 2），回退经 `cmd /c` 解析。Windows 加 CREATE_NO_WINDOW：父进程无控制台
时子进程不弹 cmd 黑窗（根治「每 5 秒弹黑窗」）。
"""
import os
import subprocess


def run_cmd(cmd, timeout=30):
    """执行命令返回 (code, out)。失败不抛。"""
    kwargs = dict(capture_output=True, text=True,
                  timeout=timeout, encoding="utf-8", errors="replace")
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        p = subprocess.run(cmd, **kwargs)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        if os.name == "nt":
            try:
                p = subprocess.run(["cmd", "/c", *cmd], **kwargs)
                return p.returncode, (p.stdout or "") + (p.stderr or "")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
```

`install.py`：顶部 `import _proc`；`run()`（67-89 行主体）替换为：
```python
def run(cmd, timeout=30):
    """执行命令，返回 (returncode, stdout)。失败不抛。委托 _proc.run_cmd。"""
    return _proc.run_cmd(cmd, timeout)
```
`install.NEEDED_FILES` 加 `"_proc.py"`（紧跟 `install.py` 后）。

`toggle.py`：顶部 `import _proc`；`_cmd()`（73-94 行主体）替换为：
```python
def _cmd(args, timeout=20):
    """执行命令返回 (code, out)；Windows .cmd 回退 + CREATE_NO_WINDOW（见 _proc.run_cmd）。"""
    return _proc.run_cmd(args, timeout)
```

`mcp_hosts.py`：顶部 `import _proc`（保留只 import stdlib + _proc 的纯叶）；`_cmd()`（39-56 行主体）替换为一行委托 `_proc.run_cmd(args, timeout)`。

`Dockerfile` COPY 行加 `_proc.py`：
```dockerfile
COPY proxy.py config_loader.py control_api.py prompts.py vision_client.py toggle.py \
     mcp_server.py mcp_hosts.py _proc.py ./
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部通过（33 + 新 2），现有 install/mcp_hosts 测试零改动仍绿（薄封装保住 monkeypatch 点）。

- [ ] **Step 5: 提交**

```bash
git add _proc.py install.py toggle.py mcp_hosts.py Dockerfile tests/test_proc.py
git commit -m "refactor: _proc.py 归口子进程辅助（install/toggle/mcp_hosts 薄封装委托）"
```

---

### Task 2: env 云安全 — 无 BASE_URL 不注入合成云

**Files:**
- Modify: `config_loader.py`（`get()` env 段）、`README.md`（env 表注记）
- Test: `tests/test_config_loader.py`

**Interfaces:**
- Consumes: Task 1（无，独立文件）。
- Produces: `get()` 在 `VISION_API_KEY` 且 `VISION_API_BASE_URL` 均非空才注入合成 `env` 平台；只有 key → `log.warning` 保持纯本地。Task 3 依赖此行为。

- [ ] **Step 1: 写失败测试**

`tests/test_config_loader.py` 追加：
```python
def test_env_key_without_base_url_stays_local(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    # 不设 VISION_API_BASE_URL
    cfg = config_loader.get()
    assert cfg["cloud"].get("active") != "env"
    urls = [c.get("base_url", "") for c in cfg["cloud"].get("clouds", [])]
    assert "https://api.example.com/v1" not in urls
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_config_loader.py::test_env_key_without_base_url_stays_local -v`
Expected: FAIL（现注入合成云 active=env）

- [ ] **Step 3: 实现**

`config_loader.py` get() env 段（116-125 行）改为：
```python
    env_key = os.environ.get("VISION_API_KEY", "").strip()
    env_base = os.environ.get("VISION_API_BASE_URL", "").strip()
    if env_key and env_base:
        # 注入合成云平台（env 驱动，需 key+base_url 成对），vision_client 经 cloud 通道走云端
        cfg["cloud"]["clouds"] = [{
            "name": "env",
            "base_url": env_base,
            "model": env_model or "qwen-vl-plus",
            "api_key": env_key,
        }]
        cfg["cloud"]["active"] = "env"
    elif env_key:
        # 只有 key 无 base_url：不注入（避免 key 被 POST 到占位主机），保持纯本地
        log.warning("VISION_API_KEY set but VISION_API_BASE_URL missing; staying local (env cloud requires both)")
```

`README.md` 环境变量表 `VISION_API_KEY` 行注明「需与 VISION_API_BASE_URL 成对配置；仅设 key 不生效」。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_config_loader.py -v`
Expected: 8 passed（原 7 + 新 1；`test_cloud_env_injects_synthetic_platform` 仍绿——它同时设了 key+base_url）

- [ ] **Step 5: 提交**

```bash
git add config_loader.py README.md tests/test_config_loader.py
git commit -m "fix(config_loader): VISION_API_KEY 无 BASE_URL 时不注入合成云（防泄漏占位主机）"
```

---

### Task 3: 后端路由决策单一事实来源

**Files:**
- Modify: `config_loader.py`（新增 cloud_key_of/active_cloud/cloud_key/use_cloud + 重写 resolve_backend）、`vision_client.py`（_clouds/_active_cloud/_cloud_key_of/_cloud_key/_use_cloud 薄委托）、`control_api.py`（get_status）
- Test: `tests/test_config_loader.py`、`tests/test_vision_client.py`、可选 `tests/test_control_api.py`

**Interfaces:**
- Consumes: Task 2（get() 无 BASE_URL 不注入）。
- Produces: `config_loader.active_cloud() -> dict|None`（active 匹配，空则取第一个有 key 的平台）、`cloud_key_of(c) -> str`（`<NAME>_API_KEY` env 优先回退 c["api_key"]）、`cloud_key() -> str`、`use_cloud() -> bool`（VISION_PROVIDER 强制，否则 bool(cloud_key())）、`resolve_backend()` 重写（provider=use_cloud；cloud 时 model 取 active 云 model 空回退 ollama.model）。
- Task 6 依赖 vision_client 薄委托后的 locate/spatial（本任务不改它们）。

- [ ] **Step 1: 写失败测试**

`tests/test_config_loader.py` 追加：
```python
def test_active_cloud_falls_back_to_first_with_key(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    import json
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"cloud": {"active": "",
        "clouds": [{"name": "a", "base_url": "https://a", "model": "m", "api_key": ""},
                   {"name": "b", "base_url": "https://b", "model": "m", "api_key": "bkey"}]}}),
        encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(conf))
    monkeypatch.delenv("A_API_KEY", raising=False)
    monkeypatch.delenv("B_API_KEY", raising=False)
    c = config_loader.active_cloud()
    assert c is not None and c.get("name") == "b"


def test_cloud_key_env_name_support(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env")
    assert config_loader.cloud_key_of({"name": "dashscope", "api_key": ""}) == "sk-env"
    assert config_loader.cloud_key_of({"name": "dashscope", "api_key": "cfg"}) == "sk-env"
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    assert config_loader.cloud_key_of({"name": "dashscope", "api_key": "cfg"}) == "cfg"


def test_use_cloud_provider_force(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "k")
    monkeypatch.setenv("VISION_API_BASE_URL", "https://b")
    assert config_loader.use_cloud() is True
    monkeypatch.setenv("VISION_PROVIDER", "local")
    assert config_loader.use_cloud() is False
    monkeypatch.setenv("VISION_PROVIDER", "cloud")
    assert config_loader.use_cloud() is True


def test_resolve_backend_model_cloud(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_API_KEY", "k")
    monkeypatch.setenv("VISION_API_BASE_URL", "https://b")
    monkeypatch.setenv("VISION_MODEL", "qwen-vl-plus")
    b = config_loader.resolve_backend()
    assert b["provider"] == "cloud"
    assert b["model"] == "qwen-vl-plus"  # 云时取云模型，非 ollama.model
    monkeypatch.delenv("VISION_API_KEY")
    monkeypatch.delenv("VISION_API_BASE_URL")
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    b2 = config_loader.resolve_backend()
    assert b2["provider"] == "local"
    assert b2["model"] == "qwen2.5vl"
```

`tests/test_vision_client.py` 改 `test_use_cloud_provider_override`（monkeypatch 目标改为 config_loader）：
```python
def test_use_cloud_provider_override(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "cloud_key", lambda: "sk")
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    assert vision_client._use_cloud() is True
    monkeypatch.setenv("VISION_PROVIDER", "local")
    assert vision_client._use_cloud() is False
    monkeypatch.setenv("VISION_PROVIDER", "cloud")
    assert vision_client._use_cloud() is True
    monkeypatch.setattr(config_loader, "cloud_key", lambda: "")
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    assert vision_client._use_cloud() is False
```

`tests/test_config_loader.py` 改 `test_resolve_backend_cloud_env`：补设 `VISION_API_BASE_URL`（B 后仅 key 判 local）。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_config_loader.py tests/test_vision_client.py -v`
Expected: FAIL（active_cloud/cloud_key_of/use_cloud 不存在；_use_cloud 仍用 _cloud_key）

- [ ] **Step 3: 实现**

`config_loader.py` 新增（`get()` 之后、`resolve_backend()` 之前）：
```python
def cloud_key_of(c: dict) -> str:
    """单个平台的 key：优先 <NAME>_API_KEY 环境变量，回退 config api_key。"""
    name = (c.get("name") or "").strip().upper()
    env = os.environ.get(f"{name}_API_KEY") if name else ""
    if env:
        return env
    return c.get("api_key", "") or ""


def active_cloud() -> dict | None:
    """当前激活云平台：cloud.active 匹配；未指定 active 时取第一个有 key 的平台。"""
    cloud_cfg = get().get("cloud", {})
    active = cloud_cfg.get("active", "")
    clouds = cloud_cfg.get("clouds", []) or []
    if not clouds:
        return None
    if active:
        for c in clouds:
            if c.get("name") == active:
                return c
        return None
    for c in clouds:
        if cloud_key_of(c):
            return c
    return None


def cloud_key() -> str:
    """当前激活平台的 API key（供 use_cloud 判断）。"""
    c = active_cloud()
    return cloud_key_of(c) if c else ""


def use_cloud() -> bool:
    """是否走云端：VISION_PROVIDER 强制 local/cloud；否则任一平台有 key 即云端。"""
    provider = os.environ.get("VISION_PROVIDER", "").strip()
    if provider == "local":
        return False
    if provider == "cloud":
        return True
    return bool(cloud_key())
```

重写 `resolve_backend()`：
```python
def resolve_backend() -> dict:
    """归一化后端信息（env > config > 默认），供 mcp_server 调试/选后端。

    返回 {provider, active, model, url, api_key, base_url, precision}；
    provider 与 vision_client 实际路由同源（use_cloud）；model 云端时取云厂商模型。
    """
    cfg = get()
    o = cfg.get("ollama", {})
    ac = active_cloud()
    provider = "cloud" if use_cloud() else "local"
    model = o.get("model", "")
    if provider == "cloud" and ac:
        model = ac.get("model") or model
    return {
        "provider": provider,
        "active": (ac or {}).get("name", ""),
        "model": model,
        "url": o.get("url", ""),
        "api_key": cloud_key(),
        "base_url": (ac or {}).get("base_url", ""),
        "precision": o.get("precision", "fast"),
    }
```

`vision_client.py`：`_clouds()`/`_active_cloud()`/`_cloud_key_of()`/`_cloud_key()`/`_use_cloud()` 改为薄委托：
```python
def _clouds() -> list:
    """所有已配置的云端平台（config.cloud.clouds）。"""
    return config_loader.get().get("cloud", {}).get("clouds", []) or []


def _active_cloud() -> dict | None:
    return config_loader.active_cloud()


def _cloud_key_of(c: dict) -> str:
    return config_loader.cloud_key_of(c)


def _cloud_key() -> str:
    return config_loader.cloud_key()


def _use_cloud() -> bool:
    return config_loader.use_cloud()
```

`control_api.py` `get_status()`：`"backend"` 由 `"cloud" if active else "local"` 改为 `"cloud" if config_loader.use_cloud() else "local"`；`"active_provider"` 改为 `(config_loader.active_cloud() or {}).get("name", "")`（保留字段名，VS Code 插件兼容）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部通过（config_loader 11 + vision_client 3 + 其余不变）

- [ ] **Step 5: 提交**

```bash
git add config_loader.py vision_client.py control_api.py tests/
git commit -m "refactor: 后端路由决策收敛到 config_loader 单一事实来源（active_cloud/cloud_key/use_cloud）"
```

---

### Task 4: 去硬编码 qwen2.5vl

**Files:**
- Modify: `install.py`（删 VISION_MODEL 常量 + check_ollama 从 config 读模型）、`toggle.py`（删常量 + import config_loader + _unload_model/doctor 从 config 读模型）
- Test: `tests/test_install.py`、可选 `tests/test_toggle.py`

**Interfaces:**
- Consumes: Task 1（薄封装已就位）、config_loader.get()（Task 2/3 已增强）。
- Produces: `install.check_ollama` / `toggle._unload_model` / `toggle.doctor` 使用 `config_loader.get().get("ollama", {}).get("model", "qwen2.5vl")`。

- [ ] **Step 1: 写失败测试**

`tests/test_install.py` 追加：
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_install.py -v`
Expected: FAIL（check_ollama 用模块级 qwen2.5vl 判 llava 缺失）

- [ ] **Step 3: 实现**

`install.py`：删 `VISION_MODEL = "qwen2.5vl"`（37 行）。`check_ollama()` 内：
```python
def check_ollama(auto=False) -> bool:
    code, out = run(["ollama", "--version"])
    ok = code == 0
    mark(ok, f"Ollama ({out.strip() if ok else '未安装'})")
    if not ok:
        print("   → 安装: winget install Ollama.Ollama，然后重开终端")
        return False
    model = config_loader.get().get("ollama", {}).get("model") or "qwen2.5vl"
    code, out = run(["ollama", "list"])
    ok_run = code == 0
    mark(ok_run, "Ollama 服务运行中")
    if not ok_run:
        print("   → 启动 Ollama（桌面应用或 `ollama serve`），再重跑")
        return False
    has_model = model in out
    mark(has_model, f"视觉模型 {model}（ollama list）")
    if not has_model:
        if auto:
            print(f"   → 拉取中（约 6GB，耗时看网速）…")
            code, o = run(["ollama", "pull", model], timeout=600)
            mark(code == 0, f"ollama pull {model}")
            return code == 0
        print(f"   → 修复: ollama pull {model}（或 install.py --auto）")
        return False
    return True
```

`toggle.py`：删 `VISION_MODEL = "qwen2.5vl"`（9 行）；顶部 `import config_loader`。`_unload_model()`：
```python
def _unload_model():
    """off 时主动卸载视觉模型，立即释放显存（不等 keep_alive 超时）。"""
    try:
        model = config_loader.get().get("ollama", {}).get("model") or "qwen2.5vl"
        subprocess.run(["ollama", "stop", model], timeout=30, capture_output=True)
    except Exception:
        pass
```
`doctor()` 第 1 段（110-113 行）：`has_model = model in out` 且 `model` 从 config 取；`ollama pull {model}` 同步。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add install.py toggle.py tests/test_install.py
git commit -m "refactor: install/toggle 模型名从 config_loader 读，删硬编码 qwen2.5vl"
```

---

### Task 5: 新旧 MCP 形态迁移断层

**Files:**
- Modify: `install.py`（_mcp_list + ensure_mcp 四路径 + check_node 降级）、`toggle.py`（doctor 第 3 段新命令 + 区分新旧）、`mcp_hosts.py`（host_status claude 行收紧）、`README.md`（Node 行降级 + 533 端口注意事项）
- Test: `tests/test_install.py`（重写 ensure_mcp 测试为三路径 + check_node advisory）

**Interfaces:**
- Consumes: Task 1/4（install/toggle 已薄封装 + config 模型）。
- Produces: `install._mcp_list() -> str`；`ensure_mcp()` 四路径判定；`check_node()` 降级不阻断。

**⚠️ 实施前先真机验证（实现者执行）**：
① `claude mcp list` 输出是否含 `mcp_server.py` / `mcp-vision.js` 路径（`claude mcp list` 看实际列内容）；② `claude mcp add --scope user vision -- python <server>` 对同名已注册 `vision` 是否覆盖（若报已存在 → 覆盖路径先 `claude mcp remove vision` 再 add）。若 `claude mcp list` 不含路径，判定改读 `~/.claude.json` 的 mcpServers。

- [ ] **Step 1: 写失败测试**

`tests/test_install.py` 重写 `test_ensure_mcp_uses_python_server` 为三路径（monkeypatch `install._mcp_list` 与 `install.run`）：
```python
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
    monkeypatch.setattr(install, "_mcp_list", lambda: "")
    monkeypatch.setattr(install, "run", lambda cmd, timeout=30: calls.append(cmd) or (0, ""))
    assert install.ensure_mcp() is True
    assert any("mcp add" in " ".join(c) for c in calls)


def test_check_node_advisory(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "run", lambda cmd, timeout=30: calls.append(cmd) or (1, ""))
    ok = install.check_node()
    assert ok is True  # 无 node 不阻断
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_install.py -v`
Expected: FAIL（ensure_mcp 无 _mcp_list / 单路径；check_node 返回 False）

- [ ] **Step 3: 实现**

`install.py`：
```python
def _mcp_list() -> str:
    code, out = run(["claude", "mcp", "list"], timeout=20)
    return out if code == 0 else ""


def ensure_mcp() -> bool:
    out = _mcp_list()
    if "vision" in out:
        if "mcp_server.py" in out:
            mark(True, "MCP server `vision` 已注册（mcp_server.py）")
            return True
        if "mcp-vision.js" in out:
            print("⚠ 检测到旧 Node server（mcp-vision.js），覆盖为 mcp_server.py…")
        else:
            mark(True, "MCP server `vision` 已注册（非本脚本 command，保留）")
            return True
    server = os.path.join(TARGET, "mcp_server.py")
    cmd = ["claude", "mcp", "add", "--scope", "user", "vision", "--", sys.executable, server]
    code, _ = run(cmd, timeout=30)
    ok = code == 0 and "vision" in _mcp_list()
    mark(ok, "注册 MCP server `vision`（mcp_server.py）")
    if not ok:
        print(f"   → 修复: claude mcp add --scope user vision -- {sys.executable} \"{server}\"")
    return ok
```

`check_node()` 降级：有 node → `mark(True, f"Node.js {ver}（仅旧 mcp-vision.js 需要；新 Python MCP server 无需）")`；无 node → 打印 `提示: 未检测到 Node.js（仅旧 mcp-vision.js 路径需要，新 mcp_server.py 纯 Python 无需）`，恒 `return True`。

`toggle.py` doctor() 第 3 段（126-131 行）：改为按 out 区分：
```python
    code, out = _cmd(["claude", "mcp", "list"])
    if code == 0 and "vision" in out and "mcp_server.py" in out:
        mark(True, "MCP server `vision`（mcp_server.py）")
    elif code == 0 and "vision" in out:
        mark(False, "MCP server `vision` 是旧 Node 形态（mcp-vision.js）")
        print("   → 迁移: claude mcp add --scope user vision -- python "
              f"\"{os.path.join(home, 'vision-eyes', 'mcp_server.py')}\"")
    else:
        mark(False, "MCP server `vision` 未注册")
        print("   → 修复: claude mcp add --scope user vision -- python "
              f"\"{os.path.join(home, 'vision-eyes', 'mcp_server.py')}\"")
```

`mcp_hosts.py` `host_status()` claude 行：
```python
    code, out = _cmd(["claude", "mcp", "list"])
    claude_ok = code == 0 and "vision" in out
    if claude_ok and "mcp_server.py" in out:
        rows.append(("claude", True, "claude mcp list"))
    elif claude_ok:
        rows.append(("claude", False, "claude mcp list (旧 node mcp-vision.js)"))
    else:
        rows.append(("claude", False, "claude mcp list"))
```

`README.md`：275/280 依赖表 Node 行降级为「旧 mcp-vision.js（可选）」；533 端口注意事项删 `VISION_IDENTIFY_URL` 同步项，改为「MCP server 直连 vision_client 不依赖代理端口，端口改动只需同步 CC Switch Base URL + ANTHROPIC_BASE_URL」。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add install.py toggle.py mcp_hosts.py README.md tests/test_install.py
git commit -m "fix: 新旧 MCP 形态迁移——ensure_mcp 覆盖旧 node、doctor 第 3 段新命令、check_node 降级"
```

---

### Task 6: 复用/清理（F1-F3、F5）

**Files:**
- Modify: `install.py`（删 SERVER_PATH 冗余赋值）、`toggle.py`（删 SERVER_PATH 冗余赋值）、`vision_client.py`（抽 _grounding + _grounding_enabled + locate 门控 + 删 precision 孤儿参数）、`README.md`（本地 Ollama 注记）
- Test: `tests/test_vision_client.py`

**Interfaces:**
- Consumes: Task 3（vision_client 薄委托已就位）、Task 4（install/toggle config 模型）。
- Produces: `vision_client._grounding(path, prompt, temp)`、`_grounding_enabled() -> bool`、`locate(path_or_b64, query)`（无 precision 参数，尊重 grounding 门控）。

- [ ] **Step 1: 写失败测试**

`tests/test_vision_client.py`：
- 改 `test_locate_injects_query`：开头 `monkeypatch.setattr(vision_client, "_grounding_enabled", lambda: True)`。
- 追加：
```python
def test_locate_grounding_disabled_returns_hint(monkeypatch):
    monkeypatch.setattr(vision_client, "_grounding_enabled", lambda: False)
    called = []
    monkeypatch.setattr(vision_client, "_post_b64", lambda *a, **k: called.append(1) or "x")
    out = vision_client.locate("/tmp/x.png", "按钮")
    assert "grounding 已关闭" in out
    assert not called  # 未调识别


def test_spatial_uses_grounding(monkeypatch):
    captured = {}
    monkeypatch.setattr(vision_client, "_grounding_enabled", lambda: True)
    monkeypatch.setattr(vision_client, "_grounding", lambda p, prompt, temp: captured.update(prompt=prompt) or "OK")
    vision_client.spatial("/tmp/x.png")
    assert "spatial" in captured.get("prompt", "") or captured.get("prompt")  # _entry("spatial") 文本
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_vision_client.py -v`
Expected: FAIL（无 _grounding_enabled；locate 仍直接 _post_b64）

- [ ] **Step 3: 实现**

`vision_client.py`：
- 新增 `_grounding_enabled()`：
```python
def _grounding_enabled() -> bool:
    """模型无关：config ollama.grounding 控制是否启用 grounding（换不支持 bbox 的模型时设 false 跳过）。"""
    return config_loader.get().get("ollama", {}).get("grounding", True)
```
- 新增 `_grounding(path_or_b64, prompt, temperature)`（= 现 spatial 主体）：
```python
def _grounding(path_or_b64: str, prompt: str, temperature: float) -> str:
    """grounding 请求：计算图片尺寸 + _post_b64 + 追加【原图尺寸】。spatial()/locate() 共用。"""
    img_path = path_or_b64 if os.path.exists(path_or_b64) else ""
    size = _image_size(img_path) if img_path else ""
    text = _post_b64(_to_b64(path_or_b64), prompt, temperature)
    if size:
        text += f"\n【原图尺寸】{size}"
    return text
```
- `spatial()` 改为：
```python
def spatial(path_or_b64: str) -> str:
    """空间结构识别（grounding）：输出元素名 + bbox 坐标 + 图片尺寸。"""
    e = _entry("spatial")
    return _grounding(path_or_b64, e["text"], e["temperature"])
```
- `locate()` 改为（删 precision 参数 + 门控 + 复用 _grounding）：
```python
def locate(path_or_b64: str, query: str) -> str:
    """按 query 定位图中元素（grounding bbox）。复用 spatial 提示词注入查询。
    返回元素名 + bbox JSON + 原图尺寸。模型不支持 grounding 时返回明确提示。"""
    if not _grounding_enabled():
        return "视觉 grounding 已关闭（config ollama.grounding=false），无法输出边界框；如需定位请将 grounding 设为 true。"
    e = _entry("spatial")
    prompt = f"用户在图中查找：{query}\n\n{e['text']}"
    try:
        return _grounding(path_or_b64, prompt, e["temperature"])
    except Exception:
        return "定位失败（grounding 请求异常）"
```
- `mcp_server.py` `tool_locate` 调用改为 `vision_client.locate(path, query)`（已是 2 参，无需改）。

`install.py:260-261` 与 `toggle.py:180`：删 `mcp_hosts.SERVER_PATH = ...` 两行（默认值同源）。

`README.md`：识别逻辑处加注「本地后端 = Ollama（/api/generate 直连）；非 Ollama 本地（llama.cpp/vLLM 等 OpenAI 兼容）请走云端通道」。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部通过（mcp_server locate 测试不受 precision 删除影响——2 参调用）

- [ ] **Step 5: 提交**

```bash
git add vision_client.py install.py toggle.py README.md tests/test_vision_client.py
git commit -m "refactor: 抽 _grounding 共用 + locate 尊重 grounding 门控 + 删孤儿 precision 参数"
```

---

### Task 7: G 归档 + 全量回归 + README 终稿

**Files:**
- Modify: `README.md`（终稿复核）
- Test: 全量回归

**Interfaces:** 无新接口。

- [ ] **Step 1: README 终稿复核**

grep 复核 `README.md` 无残留：`VISION_IDENTIFY_URL`（旧 MCP env）、「Node…必需/必须」表述（Node 应为「旧 mcp-vision.js 可选」）、占位 `api.example.com`（除注明环境变量成对配置处）。修复残留。

- [ ] **Step 2: 全量回归**

Run: `python -m pytest tests/ -v`
Expected: 全部通过（基线 33 + Task1-6 新增）。

Run: `python install.py --check` → 多宿主段 claude 行区分新旧 server；无 ✗ 硬伤（BASE_URL 未指向属预期）。

Run: `python toggle.py doctor` → 第 3 段修复命令为新形态（指向 mcp_server.py，无 VISION_IDENTIFY_URL）。

- [ ] **Step 3: G 归档**

把审查通过项（部署覆盖清单齐全、物理相邻良好）写入 ledger 归档记录。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs(README): 终稿复核——清旧 MCP env 残留、Node 降级、env 云成对注记"
```

---

## Self-Review（对照审查 21 条）

| 审查组 | 覆盖任务 |
|---|---|
| A 后端路由单一事实来源（4 条） | Task 3 |
| B env 云安全（2 条） | Task 2 |
| C 去硬编码模型（1 条） | Task 4 |
| D 新旧 MCP 迁移断层（5 条） | Task 5 |
| F 复用/清理（6 条：_proc 归口/grounding/locate precision/SERVER_PATH 冗余/Ollama 注记） | Task 1、6 |
| G 通过项（2 条） | Task 7 |
| E（defer）配置层读写收敛 + mcp_hosts 拆分 | 范围外 |

**循环依赖检查**：Task 3 后 `config_loader.py` 无 `import vision_client`（红线）。
**测试影响**：test_install monkeypatch `install.run`、test_mcp_hosts monkeypatch `mcp_hosts._cmd` —— Task 1 薄封装保留，零改动。
