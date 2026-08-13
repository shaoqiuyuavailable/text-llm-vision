# visual-ds：给纯文本模型装上「本地视觉眼睛」

给没有视觉能力的纯文本 LLM（如 DeepSeek-V4、GLM）外挂**本地视觉识别**能力。图片进去，文字描述出来——模型基于描述正常推理，全程本地、零 API 费用、数据不出机器。

参考了 [glm-vision](https://github.com/shiss3/glm-vision) 的「MCP + 代理 + 软规则」三层互补架构，并将视觉后端落在本地 Ollama + Qwen2.5-VL。

## 架构

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

本地识别不是「简单描述」，而是**三次判定 + 场景分层 + 温度分层**：

```
第1次 scan：一句话描述 + 判断 大类+小类
   ↓（注入）
第2次 zoom：按大类选清单提取事实（保守）
   ↓（注入 scan+zoom）
第3次 guess：基于事实大胆推测（敢猜，列候选+置信度）
```

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

### 5. 指向代理

设置 `ANTHROPIC_BASE_URL=http://localhost:8787`（可通过 CC Switch 或改 `~/.claude/settings.json` 的 env）：

```json
{ "env": { "ANTHROPIC_BASE_URL": "http://localhost:8787" } }
```

> 只有 DeepSeek（纯文本模型）需要指向代理；其它有视觉的模型用各自真实端点，不经过代理。

### 6. 注册 MCP server

```bash
claude mcp add --scope user vision -e VISION_IDENTIFY_URL=http://127.0.0.1:8787 \
  -- node "C:\Users\<USERNAME>\.claude\vision-eyes\mcp-vision.js"
```

确认：`claude mcp list` 应显示 `vision ... ✔ Connected`。

### 7. CLAUDE.md 引导

将「看图规范」（见文末附录）写入 `~/.claude/CLAUDE.md`，引导模型用 `describe_image` 而非 Read 图片。

### 8. 视觉开关（可选）

`/vision on|off` 斜杠命令切换。需在 `~/.claude/commands/vision.md` 放命令定义，并在 `permissions.allow` 加 `Bash(python *vision-eyes*toggle.py *)`。

### 9. 重启 Claude Code

所有配置就位后重启 Claude Code，验证：
- 状态栏显示视觉状态（如配置 statusLine）
- 粘贴图片 → 自动转文字
- 让模型看本地图片 → 调用 `describe_image`

## 命令行工具

```
python identify.py <图片路径>            # 三次判定全流程
python identify.py <路径> --type person.anime  # 手动指定大类.小类
python identify.py <路径> --scan|--zoom|--guess
python identify.py <路径> --ask "自定义问题"

python batch_identify.py <目录> [输出]    # 批量识别目录
python scan_one.py <图片路径>             # JSON 输出（供脚本/代理用）
python collect_images.py <目录> [每类张数] # 从 Wikimedia 按类别采集语料
```

## 视觉开关

`/vision on|off` 控制「图片是否识别」：
- `on`：图片 → 本地识别 → 文字描述
- `off`：图片 → 占位文字「视觉已关闭，未识别」

状态文件 `~/.claude/vision-eyes/state`（on/off）。

## 已知限制

1. **VS Code 扩展的 Read hook 绕过**（[upstream bug #37540](https://github.com/anthropics/claude-code/issues/37540)）：扩展的工具执行层绕过 PreToolUse hook，Read 图片 hook 不生效。因此「模型自主看图」走 **MCP describe_image**（不依赖 hook），而非 hook。**不要用 Read 读图片**（返回 `[Unsupported Image]`）。
2. **auto mode 分类器与第三方模型不兼容**（[upstream #68387](https://github.com/anthropics/claude-code/issues/68387)）：DeepSeek/GLM 等第三方模型驱动不了官方分类器，报「temporarily unavailable」是误导性错误。建议用 `acceptEdits` / `bypassPermissions` 权限模式，或把常用命令加进 `permissions.allow`。
3. **8B 模型边界**：Qwen2.5-VL(8B) 对复杂图表/长文档精细 OCR 弱于大模型；对「无鲸鱼/文字硬线索的角色」无法自行联想到品牌（需要主模型结合上下文复核）。
4. **缓存**：同图 sha256 内存缓存（代理进程重启后清空，首次会重新识别）。

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
| `toggle.py` | 视觉开关（写 state） |
| `start-proxy.bat` / `status.bat` | 启动代理 / 状态栏 |
| `test_proxy.py` | 代理端到端测试 |

## 附：CLAUDE.md 看图规范

```markdown
# 视觉能力使用规范

当前主力模型是纯文本模型，没有视觉能力。看图时必须走本地视觉工具：

1. 需要查看图片时，调用 MCP 工具 `describe_image`（传图片绝对路径），
   本地视觉模型识别后返回文字描述。
2. 不要用 Read 工具读图片文件（只会得到 [Unsupported Image]）。
3. 用户粘贴的图片已由代理自动转成文字，无需额外处理。
4. 复杂对象识别可用 describe_image 的 prompt 参数指定关注特征。

本地识别脚本：python "~/.claude/vision-eyes/identify.py" <图片路径>
```
