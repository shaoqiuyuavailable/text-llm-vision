# visual-ds：给纯文本模型装上「本地视觉眼睛」

给没有视觉能力的纯文本 LLM（如 DeepSeek-V4、GLM）外挂**本地视觉识别**能力。图片进去，文字描述出来——模型基于描述正常推理，全程本地、零 API 费用、数据不出机器。

参考了 [glm-vision](https://github.com/shiss3/glm-vision) 的「MCP + 代理 + 软规则」三层互补架构，并将视觉后端落在本地 Ollama + Qwen2.5-VL。

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

**手动启动**：
```bash
cd ~/.claude/vision-eyes
python -m uvicorn proxy:app --port 8787
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

> 本仓库 `start-proxy.bat` 做了幂等检查（端口已监听则不重复启动）。

### 5. 指向代理 ⚠️ **最后一步，做好回退准备**

设置 `ANTHROPIC_BASE_URL=http://localhost:8787`（可通过 CC Switch 或改 `~/.claude/settings.json` 的 env）：

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

**可视化**：配置 `statusLine` 指向 `status.bat`，状态栏实时显示当前档位（如 `[vision] fast (1)`）。改档位后状态栏自动更新。

## 已知限制

1. **VS Code 扩展的 Read hook 绕过**（[upstream bug #37540](https://github.com/anthropics/claude-code/issues/37540)）：扩展的工具执行层绕过 PreToolUse hook，Read 图片 hook 不生效。因此「模型自主看图」走 **MCP describe_image**（不依赖 hook），而非 hook。**不要用 Read 读图片**（返回 `[Unsupported Image]`）。
2. **auto mode 分类器与第三方模型不兼容**（[upstream #68387](https://github.com/anthropics/claude-code/issues/68387)）：DeepSeek/GLM 等第三方模型驱动不了官方分类器，报「temporarily unavailable」是误导性错误。建议用 `acceptEdits` / `bypassPermissions` 权限模式，或把常用命令加进 `permissions.allow`。
3. **8B 模型边界**：Qwen2.5-VL(8B) 对复杂图表/长文档精细 OCR 弱于大模型；对「无鲸鱼/文字硬线索的角色」无法自行联想到品牌（需要主模型结合上下文复核）。
4. **缓存轻量化**：同图 sha256 **内存缓存**（不落盘、不占用磁盘）；上限 100 条 FIFO 清理（防内存无限膨胀）；`/vision off` 时主动清空缓存 + `ollama stop` 释放显存。代理进程重启后缓存自然清空。
5. **软路由失效风险（已知）**：`CLAUDE.md` 引导模型用 `describe_image` 属「软约束」，第三方模型（如 GLM）可能无视指令固执调用原生 Read 工具，触发 upstream bug（见第 1 条）。当前无代理层强制手段，属已知限制；若遇模型不听话，需手动提示改用 `describe_image`。
6. **SSE 流式**：代理用 `aiter_bytes()` **流式透传不缓冲**，保留打字机效果（不受拦截影响）。

## 文件清单

| 文件 | 作用 |
|------|------|
| `proxy.py` | 反向代理：image→text 转换 + 透传 |
| `vision_client.py` | 视觉识别客户端：scan/zoom/guess 三次判定 |
| `config_loader.py` | 读 config.json，缺失回退 prompts.py |
| `config.json` | 场景/提示词/温度配置（唯一来源） |
| `prompts.py` | 内置默认提示词（回退） |
| `identify.py` | 单图三次判定 CLI |
| `batch_identify.py` | 批量识别目录 |
| `scan_one.py` | 单图 scan JSON 输出 |
| `collect_images.py` | Wikimedia 类别语料采集 |
| `mcp-vision.js` | MCP server：describe_image 工具 |
| `toggle.py` | 视觉档位开关（写 state 0/1/2/3） |
| `start-proxy.bat` / `status.bat` | 启动代理 / 状态栏（显示档位）|
| `test_proxy.py` | 代理端到端测试 |

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

## 平台兼容（mac / Linux 分支）

项目主体（`proxy.py` / `vision_client.py` / `identify.py` 等）是纯 Python，**跨平台可用**。Windows 专属部分（`start-proxy.bat`、settings 里的 `cmd /c` hook、`C:\` 路径）在 `mac` 与 `linux` 分支中替换为 bash 版（`start-proxy.sh`）：

```bash
bash start-proxy.sh   # 幂等启动代理（lsof 检测 8787，不重复拉起）
```

settings.json 的 SessionStart hook 对应改为：

```json
{ "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/vision-eyes/start-proxy.sh\"" } ] }
```

路径示例：mac `/Users/<USER>/.claude/vision-eyes/`，Linux `/home/<USER>/.claude/vision-eyes/`。

> **免责声明**：`mac` 与 `linux` 分支是作者「心情好顺手做」的兼容适配，**未经完整测试，不保证正确性**。仅替换了启动脚本与路径习惯，未做系统级验证。请使用者自行谨慎验证后再用于生产环境。
>
> **维护邀请**：如果你在 macOS / Linux 上验证并修复了问题，欢迎提交 PR 或开 issue，共同完善跨平台支持。作者会尽力跟进。
