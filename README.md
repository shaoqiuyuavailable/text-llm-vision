# visual-ds：给纯文本模型装上「本地视觉眼睛」

**让没有视觉的纯文本 LLM（DeepSeek、GLM 等）在 Claude Code 里真正"看得见"**——图片进去，文字描述出来，模型基于描述正常推理。全程本地、零 API 费用、数据不出机器。

> **一句话定位**：不是一个简单的"视觉代理"，而是一个 **本地视觉增强引擎**——专为"纯文本模型 + 终端/IDE"场景设计，用 **MCP + 代理 + 软规则三层互补架构** + **Scan/Zoom/Guess 三次判定流水线**，把 8B 小模型的视觉潜力榨到极致，同时做到开箱即用的深度集成。

参考了 [glm-vision](https://github.com/shiss3/glm-vision) 的「MCP + 代理 + 软规则」三层互补架构，视觉后端落在本地 Ollama + Qwen2.5-VL。

> **English / Keywords**: A **local vision proxy** for text-only LLMs (DeepSeek, GLM). Three-layer design: **MCP server** (`describe_image`), **reverse proxy** (paste-image fallback), and **CLAUDE.md soft rules**. Vision backend: **Ollama + Qwen2.5-VL** with a **Scan/Zoom/Guess** three-pass pipeline. **Zero API cost**, fully **local/offline**, deep **Claude Code** integration (vision level toggling, status bar, auto-start). Similar projects: glm-vision, ds-vision-skill-plus.

## 四大差异化卖点

| 卖点 | 说明 |
|------|------|
| 🏗️ **三层互补架构** | MCP（模型主动识图）+ 反向代理（兜底粘贴图）+ CLAUDE.md 软规则（引导用对工具）。覆盖**所有**视觉场景，纯文本模型永远只收 text、不报错 |
| 🎯 **三次判定流水线** | `Scan`（扫描判场景）→ `Zoom`（按场景聚焦细节）→ `Guess`（大胆推测），配合场景分层 + 温度分层，细节提取远胜竞品的"单次描述定终身" |
| 🔒 **100% 本地 & 零费用** | 本地 Ollama + Qwen2.5-VL，数据不出机器、不按次收费，适合敏感截图和内部文档 |
| ⚙️ **Claude Code 深度集成** | `/vision 0/1/2/3` 档位切换、状态栏实时显示、SessionStart 自动启动、超时 Masking + 历史图占位 + 隔离壳等一整套鲁棒性兜底 |

## 使用场景

**核心场景：纯文本模型需要「看图」**

| 场景 | 说明 |
|------|------|
| ① 用户无缝贴图 | 直接把图片粘贴进对话，模型基于图片内容回答（感知不到中间层）|
| ② 模型自主看图 | agentic 场景，模型自己查看本地图片文件 |
| ③ 批量图片处理 | 目录扫描、归类、批量识别 |
| ④ 隐私 / 离线 | 图片不出机器，不经过云 API（适合敏感截图、内部文档）|
| ⑤ 零成本 | 本地 Ollama 识别，不按次收费，高频使用不心疼 |

**不适用的场景**：
- 需极高精度 OCR（复杂图表 / 长文档精细识别）——8B 模型不够，应换大模型 API
- 认脸 / 认型号等需权威判断——8B 会猜但可能错，需主模型结合上下文复核

## 需求

| # | 需求 | 落地方式 |
|---|------|---------|
| 1 | 无缝粘贴，无感知 | 代理 image→text，验证通过 |
| 2 | 自动启动，开 Claude Code 即用 | SessionStart hook 拉起代理 |
| 3 | 可开关+可调档，随时切 | `/vision 0/1/2/3` + 状态栏显示档位 |
| 4 | 分类稳定，结果收敛 | 温度分层（document 0.2 ~ guess 0.5）|
| 5 | 敢猜，能提供推测 | guess 层（候选 + 置信度）|
| 6 | 不破坏 auto 分类器 | 代理只转含图请求，其余（含分类器）原样透传 |
| 7 | 跨模型切换不干扰 | 只有纯文本模型走代理，CC Switch 切走即绕过 |
| 8 | 模型不占系统盘 | `OLLAMA_MODELS` 重定向到 F 盘 |
| 9 | agentic 时序一致 | MCP `describe_image`（绕开 VS Code Read hook bug）|

## 架构

> **为什么需要这套机制（重要背景）**：本项目的核心目标是把图片识别能力「接进」纯文本模型的主流程。
> 但 VS Code 扩展存在一个**上游 bug**（[#37540](https://github.com/anthropics/claude-code/issues/37540)）：它的工具执行层**绕过 PreToolUse hook**，导致「Read 图片时用 hook 拦截识图」这条路在 VS Code 里根本走不通。
> 因此本架构**刻意避开 hook**，改用「MCP 工具（模型主动识图）+ 反向代理（兜底粘贴图）+ CLAUDE.md 软规则（引导模型）」三层组合。**这不是设计上的炫技，而是为了绕过 VS Code hook bug 的务实选择。** 如果 hook 在 VS Code 里正常，本可少做一层；但在 bug 修复前，这套机制是让纯文本模型可靠看图的唯一路径。

```
模型需要看图
  ├─ ① 主动识图 → MCP 工具 describe_image → 本地 Qwen2.5-VL 识别 → 文字描述
  ├─ ② 软规则   → CLAUDE.md 引导模型用 describe_image（而不是 Read 图片）
  └─ ③ 兜底     → 反向代理：用户粘贴的图片，请求体里 image 块 → 转文字 → 转发给纯文本模型
```

三层互补：**① 供模型主动看图；② 教模型用对工具；③ 兜底 hook 拦不住的粘贴图片**，保证纯文本模型永远只收 text、不报错。

### 三层详解

| 层 | 组件 | 文件 | 说明 |
|----|------|------|------|
| ① MCP | `describe_image` 工具 | `mcp-vision.js` | 模型主动调用，调本地 `/identify` 识图。用户级注册，所有会话可用 |
| ② 软规则 | CLAUDE.md | `~/.claude/CLAUDE.md` | 教模型「看图用 describe_image，别用 Read 图片」 |
| ③ 代理 | 反向代理 | `proxy.py` | 拦截请求体 image 块→转文字→转发上游；无图请求零开销透传 |

### 请求流转（粘贴图片）

```
Claude Code ──(ANTHROPIC_BASE_URL=localhost:8787)──▶ 本代理 ──▶ api.deepseek.com/anthropic
              ↳ 含 image 块 → 调本地 Ollama(Qwen2.5-VL) 转成文字
              ↳ 无 image 块 → 原样透传（含分类器等一切请求）
```

> **长会话历史图处理**：同一请求里的 messages 可能包含多轮旧图（长对话每次都带全量历史）。代理只对**最后一条含图的 user 消息**做真识别（当前轮新增），更早的旧图统一替换为 `[历史图片已省略]` 占位——**旧图不消耗每请求 3 张的识别配额**。这样既保证纯文本模型收不到 image 块（防 ReadError），又避免长会话被历史图反复重识别拖慢/挤占当前图。

### 请求流转（模型主动看图）

```
模型需要看图 → 调 describe_image(路径) → MCP → 本地 /identify → 返回文字描述
```

## 三次判定识别（核心能力）

本地识别不是「简单描述」，而是**三次判定 + 场景分层 + 温度分层**。通过视觉档位（`/vision 1/2/3`）接入主流程——代理贴图和 MCP `describe_image` 都读取档位：

```
第1次 scan：一句话描述 + 判断 大类+小类
   ↓（注入）
第2次 zoom：按大类选清单提取事实（保守）
   ↓（注入 scan+zoom）
第3次 guess：基于事实大胆推测（敢猜，列候选+置信度）
```

档位决定调用次数：`1=fast`（仅 scan 的描述部分）、`2=standard`（scan+zoom）、`3=deep`（scan+zoom+guess 完整三次 + **空间结构 grounding**）。

> **空间结构（deep 档专属）**：deep 档额外调用 Qwen2.5-VL 的 grounding 能力，输出**结构化 JSON**（元素名 + 边界框 bbox 坐标）+ 原图尺寸。解决纯文本模型读散文描述时的「空间迷失」——CSS 布局、UI 对齐、图表坐标等场景，主模型基于结构化坐标推理拓扑关系，而非脑补。档位设置见「视觉档位开关」章节。

**场景分层**（5 大类 × 小类）：

| 大类 | 小类 |
|------|------|
| person | anime / real / group |
| animal | — |
| document | chat / report / code / form / table |
| chart | — |
| generic | screenshot / landscape / object / meme |

**温度分层**（每个提示词独立温度）：

| 提示词 | 温度 | 理由 |
|--------|------|------|
| scan | 0.3 | 描述稳定 |
| zoom_document | 0.2 | 原文摘录要准 |
| zoom 其它 | 0.3 | 事实提取 |
| guess | 0.5 | 推测敢猜 |

**混合方案**：大类精调 + zoom 内「组合分支」兜底跨界（代码/表格/界面/地图/证件/表情包在任一 zoom 内都能被捕获）。

### OCR 自动路由（纯文字场景）

**场景**：`document.chat` / `document.code`（聊天记录、代码截图）这类**纯文字**图片，视觉模型"描述"不如"直接提取文字"准。

**路由逻辑**（在 `analyze` 的 zoom 层，`scan` 判场景后触发）：
1. `scan` 判定场景 → 若是 `document.chat/code`
2. 调 **RapidOCR**（本地 ONNX，离线免费，支持中英文）提取文字
3. **严格限定纯文字**：OCR 提取到 **≥20 字符**才算纯文字 → 用 OCR 结果替代视觉 zoom（标 `[OCR]`）
4. 提取不足（含图/空白）→ **回退视觉 zoom**（标 `[视觉]`）

**收益**：纯文字截图的文字提取比视觉模型**更准**（OCR 精确到字符），且**少跑一次视觉 zoom**（省 10-30s）。视觉模型仍做 scan（描述/场景）+ guess（推测）。需 `pip install rapidocr_onnxruntime`（首次加载模型约 1-2s，之后复用单例）。

### 视觉后端：默认本地，可选手动开云端

识别后端**默认纯本地 Ollama（零配置零费用）**；也可**手动配云端通道**（OpenAI 兼容 API）换取识别质量上限，两条路径自动切换：

| 后端 | 触发条件 | 特点 |
|------|---------|------|
| **本地 Ollama**（默认） | 未配云端 key | 零费用、数据不出机器、离线可用 |
| **云端通道** | 配置 `cloud.base_url` + 环境变量 `DASHSCOPE_API_KEY` | 识别质量更高（如 qwen-vl-plus）、更快，图出机器 |

**切换规则**：`_post_b64` 检测到**任一平台**配了 key（环境变量 `<NAME>_API_KEY` 或 config 的 `api_key`）就走云端，否则回退本地——**不配 key 即纯本地，配了自动用云端**。三次判定（Scan/Zoom/Guess）、场景分层、缓存、超时等全部复用，只换底层请求。`config.json` 不入库（key 走环境变量，防泄露）。

**多平台轮换**：`config.json` 的 `cloud` 块是数组：

```json
{
  "cloud": {
    "active": "dashscope",
    "clouds": [
      { "name": "dashscope", "base_url": "https://.../compatible-mode/v1", "model": "qwen-vl-plus", "api_key": "" },
      { "name": "siliconflow", "base_url": "", "model": "", "api_key": "" }
    ]
  }
}
```

- `active` 指定当前平台（按 name 匹配）；**留空则自动选第一个配了 key 的平台**
- 每个平台的 key 从环境变量 `<NAME大写>_API_KEY`（如 `DASHSCOPE_API_KEY`）或 `api_key` 读
- 手动轮换 = 改 `cloud.active` + 设对应环境变量，重启会话生效；`active` 指向不存在平台时安全回退本地

> 示例（阿里云百炼 DashScope）：`cloud.active="dashscope"`，启动时设 `DASHSCOPE_API_KEY=<你的key>`。识别流程不变，仅请求改走 `/chat/completions`。

实测 33 张跨类别语料：**大类准确率 91%，完全准确率（大类+小类）85%**。

## 环境与部署

### 前置环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| [Ollama](https://ollama.com) | ≥ 0.7（含 CUDA）| 本地视觉模型运行时 |
| `qwen2.5vl` 模型 | 8.3B / Q4_K_M（约 6GB）| 视觉识别模型（Ollama 拉取）|
| Python | ≥ 3.10 | 代理 + 识别脚本 |
| Node.js | ≥ 18 | MCP server |
| Claude Code | 最新 | 主运行环境 |

### 1. 安装 Ollama 并拉取视觉模型

**Windows（winget）**：
```bash
winget install Ollama.Ollama
```

**模型存储重定向到非系统盘（可选但推荐）**——设置用户环境变量 `OLLAMA_MODELS`，然后重启 Ollama（任务栏退出再开）：
```
OLLAMA_MODELS = F:\ollama\models
```

**拉取视觉模型**：
```bash
ollama pull qwen2.5vl
ollama list        # 确认就位
```

> 网络受限时：Ollama 模型走 `registry.ollama.ai`，一般直连可用；若失败，配好系统代理后重启 Ollama 重试。

### 2. 安装 Python 依赖

```bash
pip install fastapi uvicorn httpx
```

> 若 `ollama pull` 或代理访问外网受限，需确保能访问 `registry.ollama.ai` / `api.deepseek.com`（必要时走本地代理，如 Clash `127.0.0.1:7897`）。

### 3. 部署代码

将项目放到运行目录（如 `~/.claude/vision-eyes/`）：

```bash
mkdir -p ~/.claude/vision-eyes
# 拷贝本项目文件到该目录
```

复制 `config.json` 到 `~/.claude/vision-eyes/config.json`，按需调整：

```json
{
  "ollama": { "url": "http://localhost:11434/api/generate", "model": "qwen2.5vl",
              "temperature": 0.5, "top_p": 0.8 },
  "scenes": { "person": {"sub": ["anime","real","group"], "default_sub": "anime"}, ... },
  "prompts": { "scan": {"text": "...", "temperature": 0.3}, ... }
}
```

提示词缺失时回退到 `prompts.py` 内置默认（hybrid 模式）。

### 4. 启动代理

代理端口由 `config.json` 的 `port` 字段决定（默认 `8787`）。切换端口：`vision local <N>`（端口唯一入口，`vision port` 子命令已并入 `local`），会写 config.json 并提示同步 CC Switch / MCP / settings 三处（见「已知限制」第 14 条）。

**手动启动**：
```bash
cd ~/.claude/vision-eyes
python -m uvicorn proxy:app --port $(python read_port.py)
```

**自动启动（推荐）**：在 `~/.claude/settings.json` 配 SessionStart hook，Claude Code 启动时自动拉起代理：

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command",
        "command": "cmd /c \"C:\\Users\\<USERNAME>\\.claude\\vision-eyes\\start-proxy.bat\"" }] }
    ]
  }
}
```

> `start-proxy.bat` 是薄壳，实际逻辑在 `start_proxy.py`（纯 Python）：读 config 端口 → `/health` 验活（已在运行则跳过）→ 依赖预检 → 脱离启动 uvicorn → 等待验活。

### 5. 指向代理 ⚠️ **最后一步，做好回退准备**

设置 `ANTHROPIC_BASE_URL=http://localhost:<端口>`（默认 8787；可通过 CC Switch 或改 `~/.claude/settings.json` 的 env，端口以 `config.json` 的 `port` 为准）：

```json
{ "env": { "ANTHROPIC_BASE_URL": "http://localhost:8787" } }
```

> **⚠️ 重要——这是整个部署的「最后一步」，务必先确认前面所有步骤（代理、Ollama、MCP）都正常，再改这一行。**
>
> **为什么是最后一步**：`ANTHROPIC_BASE_URL` 指向代理后，**所有请求（含 auto 模式分类器）都经过代理**。如果代理没启动、或代理有 bug（如连接池改动导致的转发故障），你的会话会「能发消息、收不到回复」——直接断连。
>
> **回退准备（务必先做好）**：
> 1. 记录原直连 URL：`https://api.deepseek.com/anthropic`（或 CC Switch 里当前的 provider 配置）
> 2. 出问题时，**手动改回直连 + 重启 Claude Code**（会话启动时加载 env，中途改不生效）
> 3. 或临时关掉代理（代理挂时请求走不通，直连是逃生通道）
>
> 只有 DeepSeek（纯文本模型）需要指向代理；其它有视觉的模型用各自真实端点，不经过代理。

### 6. 注册 MCP server

```bash
claude mcp add --scope user vision -e VISION_IDENTIFY_URL=http://127.0.0.1:8787 \
  -- node "C:\Users\<USERNAME>\.claude\vision-eyes\mcp-vision.js"
```

> `VISION_IDENTIFY_URL` 的端口要与 `config.json` 的 `port` 一致；若用 `vision port <N>` 改了端口，需同步此处的 URL。

确认：`claude mcp list` 应显示 `vision ... ✔ Connected`。

### 7. CLAUDE.md 引导

将「看图规范」（见文末附录）写入 `~/.claude/CLAUDE.md`，引导模型用 `describe_image` 而非 Read 图片。

### 8. 视觉档位开关（可选）

`/vision <档位>` 斜杠命令切换，`0/1/2/3` 四个档位（`on`→1，`off`→0 向后兼容）：

| 档位 | 命令 | 识别次数 | 耗时 | 输出 |
|------|------|---------|------|------|
| 0 = off | `/vision off` / `/vision 0` | 0 | — | 图片→占位「视觉已关闭」 |
| 1 = fast | `/vision on` / `/vision 1` | 1 次 | ~15s | 单句描述（默认） |
| 2 = standard | `/vision 2` | 2 次 | ~30s | 描述 + 场景 + 按类细节 |
| 3 = deep | `/vision 3` | 3 次 | ~45s | 完整三次判定（含推测） |

代理和 MCP（`describe_image`）都读取该档位：0 不识别，1/2/3 对应 `fast/standard/deep` 精度。需在 `~/.claude/commands/vision.md` 放命令定义，并在 `permissions.allow` 加 `Bash(python *vision-eyes*toggle.py *)`。

### 9. 重启 Claude Code

所有配置就位后重启 Claude Code，验证：
- 状态栏显示视觉状态（如配置 statusLine）
- 粘贴图片 → 自动转文字
- 让模型看本地图片 → 调用 `describe_image`

## 命令行工具

```
python identify.py <图片路径>            # 三次判定全流程
python identify.py <路径> --precision fast|standard|deep  # 指定精度（默认 deep，含空间结构）
python identify.py <路径> --type person.anime  # 手动指定大类.小类
python identify.py <路径> --scan|--zoom|--guess
python identify.py <路径> --ask "自定义问题"

python batch_identify.py <目录> [输出]    # 批量识别目录
python scan_one.py <图片路径>             # JSON 输出（供脚本/代理用）
python collect_images.py <目录> [每类张数] # 从 Wikimedia 按类别采集语料
```

## 视觉档位

`/vision <档位>` 控制「识别不识别 + 精度」，状态存 `~/.claude/vision-eyes/state`（`0/1/2/3`）：

- `0`（off）：图片 → 占位文字「视觉已关闭，未识别」；**同时主动卸载视觉模型（`ollama stop`），立即释放显存**（不等 keep_alive 超时）
- `1`（fast，默认）：图片 → 单句描述（1 次识别调用）
- `2`（standard）：图片 → 描述 + 场景 + 按类细节（2 次调用）
- `3`（deep）：图片 → 完整三次判定含推测（3 次调用）

`on`/`off` 向后兼容（on→1，off→0）。代理和 MCP 都读取该档位。on 档（1/2/3）不主动卸载——识别时模型自动加载进显存，空闲默认 5 分钟后由 Ollama 自动卸载；只有 off（0）才立即 `ollama stop` 释放。

**快速调节**：`/vision 0|1|2|3`（或 `/vision on|off`）；也可直接改 state 文件，下一请求生效，无需重启。

### 后端切换（本地 / 云端）

档位（0-3）控制**识别精度**，后端控制**识别引擎**——**两条独立轴，互不影响、可叠加**：

```bash
/vision local              # 切本地 Ollama（端口保持当前）
/vision local 9000         # 切本地 + 指定端口（端口唯一入口，`vision port` 子命令已并入）
/vision cloud              # 切云端（当前或第一个厂商）
/vision cloud siliconflow  # 切云端 + 指定厂商
/vision list               # 查看档位/后端/端口/各厂商 key
```

**逻辑隔离**：`local` 只写 `cloud.active=""`（+ 可选端口），**不碰**云端厂商列表；`cloud` 只写 `active=<厂商>`，**不碰**端口；档位命令不碰后端。`active` 指向不存在厂商时安全回退本地。云端 key 从环境变量 `<厂商大写>_API_KEY` 或 config `api_key` 读（见「视觉后端」章节）。

**可视化**：配置 `statusLine` 指向 `status.bat`，状态栏实时显示当前档位（如 `[vision] fast (1)`）。改档位后状态栏自动更新。

## 已知限制

1. **VS Code 扩展的 Read hook 绕过**（[upstream bug #37540](https://github.com/anthropics/claude-code/issues/37540)）：扩展的工具执行层绕过 PreToolUse hook，Read 图片 hook 不生效。因此「模型自主看图」走 **MCP describe_image**（不依赖 hook），而非 hook。**不要用 Read 读图片**（返回 `[Unsupported Image]`）。
2. **auto mode 分类器与第三方模型不兼容**（[upstream #68387](https://github.com/anthropics/claude-code/issues/68387)）：DeepSeek/GLM 等第三方模型驱动不了官方分类器，报「temporarily unavailable」是误导性错误。建议用 `acceptEdits` / `bypassPermissions` 权限模式，或把常用命令加进 `permissions.allow`。
3. **8B 模型边界**：Qwen2.5-VL(8B) 对复杂图表/长文档精细 OCR 弱于大模型；对「无鲸鱼/文字硬线索的角色」无法自行联想到品牌（需要主模型结合上下文复核）。
4. **缓存轻量化（仅 deep 档）**：同图 sha256 **内存缓存**（不落盘、不占用磁盘），**只在 `deep` 档(3)启用**——fast/standard 各 1-2 次调用，缓存收益趋近于零；deep 是 3-4 次调用（scan+zoom+guess+spatial），「同图重试 / 重复粘贴」时缓存才省时。key 含 **model + 温度**，换模型/改温度后不命中旧缓存；上限 100 条 FIFO 清理；`/vision off` 时主动清空缓存 + `ollama stop` 释放显存；代理进程重启后缓存自然清空。
5. **软路由失效风险（已知）**：`CLAUDE.md` 引导模型用 `describe_image` 属「软约束」，第三方模型（如 GLM）可能无视指令固执调用原生 Read 工具，触发 upstream bug（见第 1 条）。当前无代理层强制手段，属已知限制；若遇模型不听话，需手动提示改用 `describe_image`。
6. **SSE 流式**：代理用 `aiter_bytes()` **流式透传不缓冲**，保留打字机效果（不受拦截影响）。
7. **历史图配额（已修复的坑）**：早期版本历史占位图也消耗 `MAX_IMAGES_PER_REQ=3` 配额，长会话里旧图堆满后，当前真正要识别的图会被误判「超上限」替换成占位符——表现为**代理日志一切正常（has_image=True、上游 200）但模型实际收不到识别结果**。现已在 `_convert_images` 区分「历史占位」与「当前识别」，历史图不再挤占配额（见「请求流转」）。**遇到「能发消息但模型像没看到图」时优先怀疑此环节**。
8. **日志系统（代理整体日志）**：写 `vision-proxy.log`，**成功路径（正常发出/接收）只记 debug 级不刷屏；兜底路径（识别失败/超限/历史占位/off/非 image 块）和异常（上游连接失败、非 2xx、解析错误）记 warning/error 落盘**，每条带请求 ID `rid` 可追踪整个生命周期。按天滚动，保留 3 天自动清理（`TimedRotatingFileHandler` + 启动时清理双保险）。`/health` 端点返回 pid / 档位 / 代码 mtime / uptime，供验活。
9. **图片大小限制 + 自动缩放**：单图超过 10MB 不识别，替换占位符 `[图片N（超过10MB，未识别）]` 并记 warning，防内存/耗时失控。**所有识别前等比例缩放**（最大边长 ≤1280px，`PIL` 处理，小图零开销）——实测 2600×3200 图识别耗时 **20.9s→5.0s（省 76%）**，防高分辨率图（4K/长截图）Token 暴增拖慢识别。非 image 块（`document` 等）原样透传但记 warning。
10. **识别超时兜底（#13）**：Ollama 僵死/极慢时，识别可能无限挂起（`asyncio.to_thread` 无超时）。现已用 `asyncio.wait_for` 加**总超时**（按档位 fast 45s / standard 60s / deep 120s），超时后**所有图片替换为占位符**（`[图片（识别超时，已省略）]`）并继续转发——请求不死、纯文本模型不收到 image 块，但该次识别结果丢失。
11. **依赖缺失兜底（#5）**：`start-proxy.bat` 启动前预检 `python` + `uvicorn/fastapi/httpx`，失败给出明确提示；启动后用 `/health` 验活（而非只看端口），端口占用但无响应时告警。state/config 回退（#15/#16）不再静默——损坏时记 warning 落日志。
12. **假死探测（#2）**：事件循环卡死时 HTTP 层不响应但端口仍监听（`/health` 测不出）。代理内置 **watchdog 守护线程**：每 30s 请求自身 `/health`，连续 3 次失败判假死 → 记 ERROR + `os._exit(1)` 自杀，下次 SessionStart 的 start-proxy.bat 自动拉起新进程。仅在 uvicorn 运行时启用（lifespan 启动），import/测试不触发。
13. **上游断流日志（#10）**：SSE 流式转发中上游中途断流（ReadError/ConnectError）时，`_iter_upstream` 包装生成器记 `WARNING upstream stream interrupted mid-way` + 请求 ID。正常完成 / 客户端主动断开不记录（不算异常）。
14. **端口可配置（#4）**：代理端口由 `config.json` 的 `port` 字段决定（默认 8787）。切换命令：`vision local <N>`（端口唯一入口，已并入 `local` 子命令），会写 config.json 并**提示同步三处**：CC Switch 里 DeepSeek 的 Base URL、MCP 注册的 `VISION_IDENTIFY_URL`、settings.json 的 `ANTHROPIC_BASE_URL`。改端口后需重启会话（SessionStart 会在新端口自动拉起代理）。
15. **上游重试策略（防重复扣费）**：代理**只重试连接断开类错误**（`ConnectError`/`ReadError`/`ReadTimeout` 等——这些保证请求未达服务端，重试不会重复扣费）。**5xx 一律不重试**（500/502/503/504）：服务端可能已生成内容并扣费，盲发会**双重扣费 + 幻觉**（曾因此额外扣费）。4xx 业务错误也不重试。所有非 2xx 直接透传给 Claude Code 处理。

## 文件清单

| 文件 | 作用 |
|------|------|
| `proxy.py` | 反向代理：image→text 转换 + 透传 + 整体日志 + /health 验活 |
| `vision_client.py` | 视觉识别客户端：scan/zoom/guess 三次判定 + 云端通道 + OCR 自动路由 |
| `config_loader.py` | 读 config.json，缺失回退 prompts.py |
| `config.json` | 场景/提示词/温度配置（唯一来源） |
| `prompts.py` | 内置默认提示词（回退） |
| `identify.py` | 单图三次判定 CLI |
| `batch_identify.py` | 批量识别目录 |
| `scan_one.py` | 单图 scan JSON 输出 |
| `collect_images.py` | Wikimedia 类别语料采集 |
| `mcp-vision.js` | MCP server：describe_image 工具 |
| `toggle.py` | 视觉控制：档位 0/1/2/3 + 后端 local[端口]/cloud[厂商]/list |
| `start-proxy.bat` / `start_proxy.py` | 启动代理（bat 薄壳，逻辑全在 Python：读端口/验活/拉起）|
| `read_port.py` | 输出配置端口（供 bat/脚本用） |
| `status.bat` | 状态栏（显示档位）|
| `test_proxy.py` | 代理端到端测试（读配置端口） |

## 附：CLAUDE.md 看图规范

> **为什么用软规则 + 为什么增强**：本项目无法在代理层强制拦截 Read 图片（VS Code 扩展的工具执行层绕过 hook，见「已知限制」第 1 条），路由控制权只能靠 CLAUDE.md 的 Prompt 引导。增强后的软规则把「绝对不要用 Read 读图片」从「建议」提升为「强制 + 失败路径识别」（Read 拿到 `[Unsupported Image]` 后必须改用 `describe_image`）。**这是软约束，第三方模型仍可能无视**——属已知限制（见「已知限制」第 5 条），但增强版能显著降低失效概率。

将以下内容写入 `~/.claude/CLAUDE.md`（所有会话生效）：

```markdown
# 视觉能力使用规范

看图时必须走本地视觉工具，**不能直接用 Read 读图片**（会返回 `[Unsupported Image]`）。

## 看图规范（强制）

1. **需要查看图片内容时，调用 MCP 工具 `describe_image`**（传图片绝对路径），
   它会用本地视觉模型（Qwen2.5-VL）识别后返回文字描述。
2. **绝对不要用 Read 工具读图片文件**——Read 读图片只会得到 `[Unsupported Image]`，
   是**已知的失败路径**。如果尝试 Read 图片后拿到 `[Unsupported Image]`，立刻改用 `describe_image`。
3. **判断图片路径的标准**：文件扩展名是 `.jpg/.jpeg/.png/.webp/.gif/.bmp` 的就是图片，
   必须走 `describe_image`；只有非图片文本文件才用 Read。
4. 如果用户粘贴了图片，代理已自动把图片转成文字描述进入上下文，无需额外处理。
5. 识别复杂对象（型号、角色、图表）时，`describe_image` 支持可选 `prompt` 参数。

> **为什么不能 Read 图片**：本环境的 VS Code 扩展存在上游 bug，Read 图片时无法通过 hook
> 拦截识别，且主力模型无视觉，Read 只会拿到 `[Unsupported Image]`。`describe_image` 是本环境下唯一可靠的看图方式。

## 命令工具

本地识别脚本：`python "~/.claude/vision-eyes/identify.py" <图片路径>`
- 默认三段式：scan（描述+场景）→ zoom（按类提取事实）→ guess（大胆推测）
- 可选 `--scan` / `--zoom` / `--guess` / `--ask "自定义问题"`
- 批量识别目录：`python "~/.claude/vision-eyes/batch_identify.py" <目录> [输出文件]`
```
