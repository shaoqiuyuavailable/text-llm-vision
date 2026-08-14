# text-llm-vision 从零上手（干净机器）

> 给纯文本模型（DeepSeek / Qwen / Kimi / GLM 等）装「本地视觉眼睛」：MCP 工具识图（主路径）+ 反向代理兜底粘贴图。**目标读者：在一台新机器上从头跑起来。**

## 一、前置条件（一台干净的 Windows 机器）

| 依赖 | 版本 | 说明 |
|---|---|---|
| Windows | 10/11 | 主要适配 Windows（代理/CREATE_NO_WINDOW） |
| Python | ≥ 3.10 | 加入 PATH |
| Ollama | 任意 | 本地视觉模型运行时 |
| 代码 | git clone | `git clone https://github.com/shaoqiuyuavailable/text-llm-vision.git` |
| Claude Code | 可选 | 只用 Claude Code 主路径时安装（`npm i -g @anthropic-ai/claude-code`） |
| Node.js | 可选 | **仅旧 `mcp-vision.js` 需要**；新 python MCP server 协议层零依赖（识别链路复用 vision_client），不需要 |

> 云端可选手动开（DashScope/硅基流动等，配 `cloud.clouds` + key）；默认纯本地零费用。
>
> **📌 本地模型怎么选（重要）**：文档里默认 `qwen2.5vl`（8.3B / 约 6GB）**只是开箱即用的起点，不代表唯一选择**。请**根据自身需求与设备配置**挑：
>
> - **升级**：**设备硬件支持且对识别精度有高要求** → 更换**更大的模型**，如 `qwen2.5vl:13b`、`qwen2.5vl:32b`、`qwen3-vl` 等，精度更高
> - **降级**：**设备配置低**（显存小 / 跑不动，4-8GB）→ **降级到更小的模型**，如 `qwen2.5vl:3b`、`llava:7b`、`minicpm-v`，更省显存、更快
> - **换法**：`ollama pull <模型名>` 拉取 → 设 `VISION_MODEL=<模型名>`（env，临时）或改 `config.json` 的 `ollama.model`（持久）
>
> 模型名全部走配置读取，**不硬编码**——`install.py` / `/vision` / `doctor` 都会按你选中的模型工作（FAQ 见「换模型」）。

## 二、一分钟跑起来

```bash
# 1. 完整依赖（含 OCR：fastapi/uvicorn/httpx/Pillow/rapidocr_onnxruntime）
pip install -r requirements.txt

# 2. 拉本地视觉模型（默认 qwen2.5vl；可按上方「本地模型怎么选」换更适合你设备/需求的模型）
ollama pull qwen2.5vl

# 3. 一键部署：检测 → 拷贝到 ~/.claude/vision-eyes → 注册 MCP → hook → CLAUDE.md 规范 → /vision 命令 → 起代理
python install.py --auto
```

`--auto` 自动：装缺的 pip 依赖 + `ollama pull`（缺模型时）+ 全部配置。分步看 `python install.py`（缺什么打 ✓/✗ 并给命令）。

**之后重启一次 Claude Code**（新会话自动拉起 MCP server + 代理）。

## 三、迁移 / 升级

- **新机器迁移**：`git clone` + `pip install -r requirements.txt` + `ollama pull` + `python install.py --auto`。`config.json` 是用户配置，install.py **不覆盖已存在的**（拷贝时跳过）。
- **旧版本升级**：直接重跑 `python install.py`——会自动把旧 node MCP（`mcp-vision.js`）**覆盖成 python server**（remove→add，见 `claude_mcp_upsert`）。若只刷新代码不碰 MCP 注册，用手动拷贝 + `python restart_proxy.py`。
- **其他宿主**（Cline / Codex / opencode / Continue / Copilot / Cursor）：`python install.py --mcp <宿主>` 或 `--mcp all`，自动写各宿主配置 + 触发规则。

## 四、使用

| 场景 | 怎么做 |
|---|---|
| 模型主动看图 | 引用图片路径 → 模型调 `describe_image`（MCP 主路径，任何宿主可用） |
| 对话内粘贴图 | 粘贴 → 代理（:8787）自动转文字（兜底：Anthropic=Claude Code / OpenAI=Cline·OpenCode·Codex，前提 Base URL 指代理 + OpenAI 链路配 `upstream_openai`） |
| 换识别精度 | `/vision 0-3`（0=off 1=fast 2=standard 3=deep） |
| 按用途调推测温度 | `identify.py --mode rigorous\|identity\|military\|anime\|open`（覆盖 guess 温度） |
| 换后端 | `/vision local [端口]` / `/vision cloud [厂商]`；或设 `VISION_API_KEY`+`VISION_API_BASE_URL`（env 云） |
| 换模型 | `VISION_MODEL=llava` env 或 config `ollama.model` |
| 可视化面板 | VS Code 扩展（见 `vscode-ext/`，打包 `.vsix` 安装） |

## 五、诊断

```bash
# 1. 体检（7 项逐项给修复命令）——最快入口
python toggle.py doctor
# 或 python install.py --check

# 2. 专项
claude mcp list                        # MCP 注册（应为 mcp_server.py）
curl http://127.0.0.1:8787/api/status  # 代理/后端/厂商状态
type ~/.claude/vision-eyes/vision-proxy.log   # 代理日志

# 3. 修复/重启
python restart_proxy.py                # 自杀→重启代理→验证→确保 BASE_URL
restart_claude.bat                     # 重启 Claude Code + 挂 MCP（外部终端运行）
```

## 六、常见问题速查

| 症状 | 排查/修复 |
|---|---|
| 模型说「看不到图」 | ① `claude mcp list` 看注册是否 `mcp_server.py`；② CLAUDE.md 含 `describe_image` 规范（install.py 自动写）；③ 重启 Claude Code |
| `doctor` 显示 MCP 是旧 node | 跑 `python install.py --mcp claude`（自动 remove→add 迁移） |
| 代理没起 | `python start_proxy.py`；或重启 Claude（SessionStart hook 自动拉起） |
| 识别慢/超时 | `/vision 1`（fast）降精度；Ollama 单卡并发上限 2 是设计 |
| OCR 无文字 | 确认 `pip install -r requirements.txt`（rapidocr_onnxruntime）；纯文字截图走 `extract_text` |
| 只设 `VISION_API_KEY` 不生效 | env 云需 `VISION_API_KEY` + `VISION_API_BASE_URL` 成对（单独 key 保持本地并告警） |
| 换模型后 `install.py` 误报缺模型 | 模型名从 config/`VISION_MODEL` 读，非硬编码；`ollama pull <新模型>` 即可 |

## 七、文件速览

| 文件 | 角色 |
|---|---|
| `mcp_server.py` | MCP 主路径（5 工具，stdio，纯 Python） |
| `proxy.py` | 反向代理（粘贴兜底 + /identify + /api/* 控制） |
| `vision_client.py` | 识别引擎（Scan/Zoom/Guess + OCR + grounding） |
| `mcp_hosts.py` | 多宿主注册 + 触发规则 |
| `config_loader.py` | 配置（env > config.json > 内置默认） |
| `install.py` | 一键部署 / `--mcp <host>` 注册 |
| `toggle.py` | 操作 CLI（档位/后端/doctor） |
| `restart_proxy.py` / `restart_claude.bat` | 重启脚本 |
| `vscode-ext/` | VS Code 面板 |
