# text-llm-vision：MCP Tool Use 提为主路径（方案 A）

日期：2026-08-14
状态：设计已确认，待用户审阅
范围：本文档只定设计（组件图 + 数据流 + 注册矩阵），不含代码实现。

---

## 1. 背景与目标

当前项目给纯文本模型（DeepSeek/Qwen/Kimi/GLM）提供视觉能力的**主路径是代理拦截**：proxy.py 拦截 Anthropic/OpenAI 协议里的 image 块，转成文字再转发上游。识别引擎（vision_client.py 的 Scan/Zoom/Guess 流水线）整个藏在 FastAPI 代理后面，MCP server（mcp-vision.js）只是 `/identify` 的瘦 HTTP 客户端。

**问题**：
1. MCP server 依赖代理存活，代理挂了 MCP 就废，不「独立存活」；
2. 只注册了 Claude Code，Cline/Continue/Copilot/Codex/opencode/Cursor 无法直接用；
3. 工具面只有 1 个 describe_image，场景覆盖窄。

**目标**（用户定调）：把 **MCP Tool Use** 提为主路径——模型通过标准 Tool Use 主动调工具识图，而不是靠代理「拦截」。代理退为「对话内粘贴」场景的兜底。整体提高场景通用性：任何支持 MCP 的宿主都能挂载，不挑宿主环境、不依赖 VS Code 插件实现细节。

**已确认决策**：
- 引擎架构：**方案 A**——Python 写 MCP server，直接 `import vision_client.py` 复用全流水线；
- 工具面：**5 个工具**——describe_image / extract_text / locate_object / compare_images / vision_rules；
- 代理角色：保留，降级为「粘贴场景专用」兜底；
- 配置源：**env > config.json > 内置默认**三层；
- 多宿主注册：install.py 扩展 `--mcp <host>` / `--mcp all`，按宿主写各自的 MCP 配置 + 触发规则。

---

## 2. 架构总览（组件图）

```
【MCP 主路径】模型驱动，独立存活，代理可完全缺席
宿主(Claude Code / Cline / Continue / VS Code Copilot / Codex / opencode / Cursor)
   │  MCP stdio
   ▼
mcp_server.py（Python 手写 MCP JSON-RPC，零第三方依赖）
   │  5 个工具：
   │    describe_image —— Scan→Zoom→Guess 三遍流水线（可选 prompt）
   │    extract_text   —— 专攻 OCR，跳过判类直接文字提取
   │    locate_object  —— spatial prompt + grounding，返回 bbox JSON
   │    compare_images —— 双图各跑流水线后对比
   │    vision_rules   —— 返回触发规则文本（配置类工具）
   ▼
vision_client.py（既有引擎，直接 import，零 HTTP 中间层）
   ▼
Ollama（默认本地）/ 云端（OpenAI 兼容）

【代理兜底路径】仅服务「对话内粘贴」（无文件路径可传工具）
Claude Code ──/v1/messages(带 image 块)──▶ proxy.py ──image→text──▶ 上游
   │  贴图进对话 → base64 在消息里 → 工具无法传文件路径 → 只能拦截转换
   ▼
vision_client.py ──▶ Ollama / 云端

【操作层】保留但非必需
identify.py CLI / VS Code 面板 / batch_identify.py ──/identify──▶ proxy.py
```

**关键点**：mcp_server.py 与代理**完全解耦**——不调 `/identify`，直接 import vision_client.py。代理只服务于「对话内粘贴」这条必须拦截的路径。

---

## 3. 数据流

### 3.1 describe_image（主路径，模型驱动）

```
1. 模型判断需要看图 → 调用 describe_image(文件路径[, prompt])
2. mcp_server.py 读配置（env > config.json > 默认）确定后端（Ollama/云）
3. 读文件 → base64 → vision_client.analyze(b64, precision)
4. 流水线：scan 判大类+小类 → zoom_{大类} 按类提取 → guess 推测
5. 返回结构化文字描述 → 模型基于文字继续推理
```

### 3.2 extract_text（OCR 专用）

```
调用 extract_text(文件路径)
→ 跳过 scan/zoom 判类，直接 OCR 强化 prompt → 返回逐字文字
→ 覆盖：截图报错、终端红字、文档扫描
```

### 3.3 locate_object（grounding 定位）

```
调用 locate_object(文件路径, 元素名/查询)
→ spatial prompt + grounding 开关 → 返回 [{name, bbox:[x1,y1,x2,y2]}]
→ 覆盖：「分辨率稀释」——让模型知道元素在图中哪里
```

### 3.4 compare_images（双图对比）

```
调用 compare_images(图A路径, 图B路径)
→ 各自跑流水线 → 再对比 → 返回差异要点
→ 覆盖：UI 前后 diff、图找不同
```

### 3.5 vision_rules（配置类）

```
调用 vision_rules()
→ 返回静态规则文本（Markdown），模型写进自己的 CLAUDE.md/AGENTS.md
→ 覆盖：install.py 未预置规则的宿主，模型自助取规则
```

---

## 4. MCP server 规格（mcp_server.py）

- **语言/依赖**：Python 3，手写 MCP JSON-RPC（stdin/stdout newline-delimited），**零第三方依赖**（复用 mcp-vision.js 的手写协议模式，不引入 `mcp` SDK）。
- **入口**：`python mcp_server.py`，被宿主以 stdio transport spawn。
- **协议版本**：MCP `2024-11-05`，`capabilities: { tools: {} }`。
- **文件读取**：复用 vision_client 的图片加载（路径 → base64），支持绝对路径；沿用现有安全边界（identify_allowed_dirs 若配置则校验，未配置默认不限制本地路径——与现状一致）。
- **错误处理**：识别失败返回 `isError: true` + 错误摘要；超时沿用 RECOGNIZE_TIMEOUT 语义；在途请求计数（同 mcp-vision.js 的 `pending`，防止 async 识别未完时进程退出）。
- **后端选择**：见 §5 配置来源。

### 4.1 工具清单

| 工具 | 参数 | 复用引擎 | 场景 |
|---|---|---|---|
| `describe_image` | `image`(必), `prompt`(选) | analyze 全流水线 | 通用看图 |
| `extract_text` | `image`(必) | OCR 强化 | 报错/红字/扫描 |
| `locate_object` | `image`(必), `query`(必) | spatial + grounding | 元素定位 |
| `compare_images` | `image_a`(必), `image_b`(必) | 双 analyze 后对比 | UI diff/找不同 |
| `vision_rules` | 无 | 静态文本 | 触发规则自助 |

---

## 5. 配置来源（env > config.json > 内置默认）

**依据**（调研结论）：MCP 客户端通过 `mcpServers.<名>.env` 块注入环境变量是唯一标准通道；server 不应依赖自己去翻配置文件。变量命名无统一标准，最通用三件套是 `VISION_API_KEY` / `VISION_MODEL` / `VISION_API_BASE_URL`；`OLLAMA_URL` 非生态标准（`OLLAMA_BASE_URL` 更常见），需做 alias 兼容。

**优先级**：env 变量 > config.json > prompts.py 内置默认。

| env 变量 | 含义 | 默认（config.json） |
|---|---|---|
| `VISION_MODEL` | 视觉模型名 | `ollama.model`（qwen2.5vl） |
| `OLLAMA_BASE_URL` / `OLLAMA_URL`(alias) | Ollama 基础地址 | `ollama.url`（`http://localhost:11434/api/generate`） |
| `VISION_PROVIDER` | `local`(默认) / 云厂商名 | `cloud.active` |
| `VISION_API_KEY` | 云厂商 key | `cloud.clouds[].api_key` |
| `VISION_API_BASE_URL` | 云端 OpenAI 兼容 base | `cloud.clouds[].base_url` |
| `VISION_TIMEOUT_MS` | 识别超时（选） | RECOGNIZE_TIMEOUT |
| `VISION_MAX_TOKENS` | 输出上限（选） | — |

**实现**：扩展 config_loader.py——把现有 `OLLAMA_URL` override（env>config 单点先例）泛化成通用 `env > config > default` 三层取值函数 `resolve_backend()`，mcp_server.py 与 proxy.py 共用。Docker 的 `OLLAMA_URL` 兼容（alias 解析）。

---

## 6. 代理角色重定位

| 路径 | 现状 | 改造后 |
|---|---|---|
| MCP 识图 | 依赖代理 `/identify` | **独立**，import 引擎直连后端 |
| 对话内粘贴（Claude Code 贴图） | 代理拦截转换 | **保留**（无文件路径可传工具，只能拦截） |
| `/identify` / control_api / VS Code 面板 | 操作层 | **保留**，服务 identify.py / 面板，与 MCP 不冲突 |

**不删任何既有组件**。proxy.py、control_api.py、VS Code 扩展、toggle.py、identify.py 全部保留，只在 README 里重定位：MCP 为主路径，代理为粘贴兜底 + 操作层。

---

## 7. 多宿主注册矩阵

**实现载体**：install.py 新增子命令 `--mcp <host>` / `--mcp all`，按矩阵写入各宿主配置；写入时保留宿主原有 mcpServers 条目（merge，不覆盖）。

| 宿主 | 配置文件 | 结构要点 | 触发规则文件 |
|---|---|---|---|
| Claude Code | `claude mcp add -s user` / `~/.claude.json` | 已有 ✓ | `CLAUDE.md`（已有 ✓） |
| Codex | `~/.codex/config.toml` | `[mcp_servers.vision]` + env | `AGENTS.md` |
| opencode | `~/.config/opencode/opencode.json` | **`mcp` 键 + 数组 command + `environment`**（非 mcpServers 格式） | `AGENTS.md` |
| Cline | VS Code: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`；CLI: `~/.cline/data/settings/cline_mcp_settings.json` | **两路径分离，分别探测**；`mcpServers` + env + autoApprove | `.clinerules` |
| Continue | `~/.continue/config.json` | `mcpServers` **数组** + env | config.json rules |
| VS Code Copilot | `.vscode/mcp.json` | `mcpServers` + env | `.github/copilot-instructions.md` |
| Cursor | `.cursor/mcp.json` | `${env:VAR}` 引用 | `AGENTS.md` |

**env 注入内容**（各宿主统一）：`command=python`、`args=[<部署目录>/mcp_server.py]`、`env` 里写 `${VAR}` 引用或直接注入当前配置值（OLLAMA 本地场景无 secret，可直接内联，符合「本地零 secret」定位）。

---

## 8. 触发规则

**母版**：以现有 CLAUDE.md「看图规范」为母版，缩成模型无关的通用规则，生成各宿主变体。

通用文本（vision_rules 工具返回 + install.py 写入共用同一常量）：

```markdown
你的模型没有视觉能力。出现以下情况必须调用相应工具：
- 用户引用本地图片路径 / 粘贴截图 / 你看到 [Unsupported Image] → describe_image(图片路径)
- 终端红字、报错栈、文档扫描 → extract_text(图片路径)
- 图中某元素在哪里 → locate_object(图片路径, 元素名)
- 前后两张图对比 → compare_images(图A路径, 图B路径)
```

**注入方式**：
- install.py 按 §7 矩阵把变体写进各宿主规则文件（AGENTS.md / .clinerules / Continue rules / copilot-instructions.md）；
- `vision_rules` 工具作为兜底：任何宿主环境，模型可自助取规则文本写进自己的规则文件；
- toggle.py `doctor()` 增加一项检查：各宿主规则文件是否存在 + mcpServers 是否含 vision。

---

## 9. 部署与诊断改动

| 组件 | 改动 |
|---|---|
| `mcp_server.py`（新） | Python MCP server，5 工具，import vision_client，env>config>default |
| `config_loader.py` | `OLLAMA_URL` override 泛化为 `resolve_backend()`（env>config>default 三层） |
| `install.py` | 新增 `--mcp <host>`/`--mcp all` 注册子命令（按 §7 矩阵写配置 + §8 规则）；NEEDED_FILES 加 mcp_server.py |
| `toggle.py` | `doctor()` 增加各宿主规则/MCP 配置检查项 |
| `mcp-vision.js` | 保留（向后兼容已注册用户），README 标注为旧形态，新部署用 mcp_server.py |
| `README.md` | 重定位：MCP Tool Use 为主路径、代理为粘贴兜底；新增注册矩阵表 + env 配置表 + 触发规则说明 |
| Docker | Dockerfile 加 mcp_server.py（可选：容器内跑 MCP server 由宿主 spawn，需挂 config.json/env） |

---

## 10. 测试计划

1. **mcp_server.py 单测**（5 工具）：
   - `describe_image`：传真实图片路径 → 返回 Scan/Zoom/Guess 三阶段文字；
   - `extract_text`：传含报错栈的截图 → 逐字返回文字；
   - `locate_object`：传带 UI 元素的截图 → 返回 bbox JSON；
   - `compare_images`：传两张相似/不同图 → 返回差异；
   - `vision_rules`：无参 → 返回 §8 规则文本。
2. **后端选择**：设 `OLLAMA_BASE_URL` 指向另一实例 → 直连生效；不设 env → config.json 生效；两者都无 → 默认 localhost。
3. **独立存活**：代理不启动，mcp_server.py 直连 Ollama 识别正常（证明解耦）。
4. **多宿主注册**：`install.py --mcp all` → 各宿主配置文件出现 vision 条目；opencode 用 `mcp` 键（验证格式坑）；Cline 探测 VS Code 与 CLI 两个路径。
5. **触发规则**：doctor() 检查全绿；新宿主无规则时调 vision_rules 能取到文本。
6. **回归**：代理路径（对话内粘贴 image→text）不受影响；Claude Code 既有 describe_image（mcp-vision.js）兼容。

---

## 11. 范围外（YAGNI）

- MCP server 进 Docker 镜像部署（本地 stdio spawn 为主；Docker 容器跑 MCP 需宿主侧 spawn，暂不做）。
- 远程/HTTP MCP transport（streamable-http/SSE）：一律 stdio。
- 鉴权加固（localhost 信任模型，与现状一致）。
- 超 5 个工具的工具面扩展（30 工具级的 vision-primitives 不做）。
- `mcp-vision.js` **不删除**：保留向后兼容已注册用户，仅 README 标注其为旧形态（新部署用 mcp_server.py）。
