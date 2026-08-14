# 视觉路由器 v1.5：多路由模型管理（含日志兜底）

> 日期：2026-08-15 · 状态：已批准 · 前置：v1 路由器（scene→引擎）
> 用户决策：场景-模型**解耦由用户自行配置**；删除分**逻辑/物理**两档；本地+云端**都纳入**；落地层**兜底 + 接入日志**。

## 目标

让用户通过 **config + CLI + 可视化面板** 管理「每个场景用什么模型」（本地 Ollama / 云端厂商都支持），含**添加、下载、替换、逻辑删除、物理删除**。路由器 v1 的 `scene→引擎` 升级为 `scene→引擎:模型`（模型名由用户自行填写，系统不预设场景-模型绑定）。

## 架构

```
config router: scene → "引擎" | "引擎:模型"     （用户自行配置）
                     ↓
_run_engine 解析引擎 + 模型 → _ENGINES[engine](..., model=)
    ├─ vlm:模型 → _post_b64(..., model) → models[model].type 决定通道
    │       ├─ ollama → 本地该模型（覆盖全局 ollama.model）
    │       └─ cloud  → _post_cloud(provider=models[model].provider)
    └─ 引擎缺失/模型未拉取/失败 → 回退全局模型 + log.warning（兜底 + 日志）
```

## 组件

### ① 模型注册表（config `models`，本地+云端）

```json
"models": {
  "qwen2.5vl":    { "type": "ollama", "purpose": "default" },
  "qwen2.5vl:3b": { "type": "ollama", "purpose": "light" },
  "ui-r1-e-3b":   { "type": "ollama", "purpose": "gui" },
  "qwen-vl-plus": { "type": "cloud", "provider": "dashscope", "purpose": "precision" }
}
```
- `type`: `ollama` / `cloud`（provider 指向 cloud.clouds 厂商）/ `pip`（如 rapid-table）
- `prompts.py` 加 `MODELS` 基线（默认仅 qwen2.5vl）；`config_loader` 播种 + prompts_version 门内叠加

### ② 路由表（用户自行填写，场景与模型解耦）

```json
"router": {
  "document.chat":  "ocr",
  "document.report":"vlm:qwen2.5vl",
  "table":          "vlm:qwen-vl-plus",
  "screenshot":     "vlm:ui-r1-e-3b",
  "_default":       "vlm:qwen2.5vl"
}
```
值格式 `引擎` 或 `引擎:模型`。模型名不在 models 表 → 回退全局 + warning。**系统不预设场景-模型绑定**。

### ③ CLI（`toggle.py model`，删除分两档）

```
model list                          # 表：模型/type/purpose/状态(ollama list 检查)/被引用场景
model add <name> --type ollama|cloud|pip [--provider x] [--purpose y] [--download]
model download <name>               # ollama pull / pip install / 云端=标记已配
model rm <name>                     # 逻辑删除：仅移出 config（可恢复）
model rm <name> --physical          # 物理删除：config + ollama rm 释放磁盘（不可逆，双重确认）
model replace <旧> <新>             # 改 router 所有引用 + 可选删旧
```
- **日志**：所有 model 操作写 `~/.claude/vision-eyes/vision-model.log`（时间戳/命令/成败/详情）
- **兜底**：删被引用模型 → 列出引用场景 + 警告；replace 新模型未拉取 → 提示但仍改配置（路由时回退）

### ④ 面板（vscode-ext 新增「模型」区）

- 模型列表节点：名称/type/purpose/状态（✓已拉取/未下载），点击弹操作（下载/逻辑删/物理删/替换）
- 场景×模型映射节点：每个 router 场景显示当前 `引擎:模型`，点击 QuickPick 改绑（候选= models 表）
- 云端模型显示 provider + key 状态；底层 spawn `toggle.py model ...`

### ⑤ 引擎层（vision_client）——兜底 + 日志

- **加 logger**：`log = logging.getLogger("vision_client")`（proxy/MCP 进程 attach handler；CLI 走 Python lastResort 到 stderr）
- **`_post_b64(..., model="")`**：model 非空 → 查 models 表决定通道（ollama 本地该模型 / cloud 该厂商）；空 → 现状（use_cloud 全局判断）
- **`_run_engine` / `_engine_vlm`**：解析 `引擎:模型` 传 model；**失败/模型缺失 → 回退全局 + `log.warning`**（含场景/引擎/原因）
- **scan 用全局模型**（路由决策阶段），zoom/guess/spatial 透传场景模型

### ⑥ proxy / MCP 日志接入

- `proxy._setup_logging`：给 `logging.getLogger("vision_client")` 挂同 handler → 引擎回退进 `vision-proxy.log`
- `mcp_server`（stdlib-only）：加轻量 logging 配置（FileHandler 追加 vision-proxy.log），引擎日志可查

## 改动文件

`prompts.py`（MODELS 基线）/ `config_loader.py`（models 播种叠加）/ `vision_client.py`（logger、model 透传、回退日志）/ `proxy.py`（attach logger）/ `mcp_server.py`（logging 配置）/ `toggle.py`（model 子命令 + vision-model.log）/ `vscode-ext/extension.js`（模型区）/ `config.json`（models+router 示例）/ 测试 / README/QUICKSTART

## 测试

- `_parse_route_value`：`vlm:qwen2.5vl` → (vlm, qwen2.5vl)；`ocr` → (ocr, "")
- `_post_b64(model=ollama 模型)` → 本地 payload.model=该模型；`model=cloud 模型` → 走云端该厂商
- `_run_engine` 未注册/失败 → 回退 vlm + `log.warning` 被捕获
- `config_loader` models 播种 + 叠加；门内
- `toggle.py model`：list 输出、add 写 config、rm 逻辑删、rm --physical 调 ollama rm（monkeypatch）、replace 改 router 引用、操作日志写入 vision-model.log
- 现有 69 测试全绿

## 验证

1. pytest 全绿
2. `toggle.py model list` 显示 qwen2.5vl + 状态 + 引用场景
3. `toggle.py model add llava:7b --download` → config 增加 + ollama pull
4. router 配 `vlm:llava`（未拉取）→ 识别回退全局 + vision-proxy.log 有 warning
5. `model rm qwen2.5vl`（被 _default 引用）→ 警告列出引用；`--physical` 需确认
6. 面板显示模型区 + 场景映射，切换生效
