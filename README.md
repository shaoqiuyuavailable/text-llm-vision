# dsh-vision：DeepSeek Harness 外接视觉插件

> 参考 `F:\code of PY\visual-ds` 的“MCP 主路径 + 代理兜底 + 软规则三层互补”思想，为 DeepSeek Harness (`dsh`) 做适配。
> 核心目标：让 dsh 默认的纯文本 DeepSeek 模型也能“看见”图片——图片进、文字描述出，全程本地、零 API 费用。

## 🎉 开源与二次开发

**热烈欢迎各位个人开发者对本项目进行二次开发与个性化定制！**

- 本插件 MIT 协议开源，代码结构刻意保持"薄插件"设计：dsh 侧只做工具注册/事件改写/配置，重活全在 Python 引擎，**改引擎不碰 dsh，改 dsh 不碰引擎**
- 三大可定制面：
  1. **识别引擎**（`python/vision_client.py` + `prompts.py`）：换模型、改提示词、加场景、加引擎（如接入 rapid-table / OmniParser / UI-TARS 只需替换引擎函数体，路由表不用动）
  2. **工具面**（`src/index.ts`）：加工具、改描述、调参数，全部走 `defineTool` 标准接口
  3. **GUI 配置**（settings 命名空间）：新配置项 = schema 加字段 + 卡片加控件，模式现成
- 开发自检：`python scripts/test_all.py`（82 用例回归）+ `python scripts/verify_mount.py`（挂载检查）
- 想分享你的定制版？Fork 本仓库 → 改 → PR，或发布你自己的 npm 包均可
- 反馈/建议/问题：GitHub Issues（见仓库首页）——**本人心情好才看一眼修一下，心情不好请自行拉取二次开发，反正 MIT 都随你折腾**

## 架构

```
dsh (DeepSeek Harness, 纯文本模型)
  ├─ ① 模型主动看图 → dsh-vision 注册的 describe_image / extract_text / locate_object / compare_images
  │                     ↓
  │                     python/vision_cli.py ──▶ vision_client（Scan→Zoom→Guess，复用 visual-ds 引擎）
  ├─ ② 软规则       → AGENTS.md / 用户提示词，引导模型用 describe_image 而不是 read_image
  └─ ③ 粘贴兜底     → agent/pre-step 钩子：用户粘贴的 image block → 本地视觉识别 → 替换成 text block
                         ↓
                         python/vision_cli.py describe <临时图片>
```

三层互补：

| 层 | dsh 机制 | 对应 visual-ds |
|----|---------|----------------|
| MCP/工具主路径 | `ctx.tools.register(defineTool(...))` | `mcp_server.py` 的 5 个工具 |
| 软规则 | `AGENTS.md` / 系统提示词 | `CLAUDE.md` / `AGENTS.md` |
| 粘贴兜底 | `agent/pre-step` waterfall 改写 image block | `proxy.py` 的 image→text 反向代理 |

## 文件

| 文件 | 作用 |
|------|------|
| `cordis.patch.yml` | dsh 插件装载补丁（标准 bundle patch，`--patch` 或合并进 profile patch） |
| `cordis.yml` | 同上，兼容别名 |
| `src/index.ts` | dsh 原生插件：注册 5 个视觉工具 + 粘贴图自动转文字 + GUI 配置同步 |
| `python/vision_cli.py` | Python 侧 CLI：统一调 `vision_client` 的 analyze/ocr/locate/compare |
| `python/vision_client.py` | 识别引擎（自包含，visual-ds v2 基线：5 引擎路由/混合场景/缓存） |
| `python/config_loader.py` | 配置读取（默认 `~/.dsh/vision/config.json`，v2 路由基线） |
| `python/prompts.py` | v2 提示词体系 + 路由/模型基线表（随插件部署） |
| `python/config.json` | 视觉引擎配置（Ollama 地址/模型/温度等），可被 `~/.dsh/vision/config.json` 覆盖 |
| `python/requirements.txt` | Python 依赖 |
| `scripts/install.py` | 一键安装/配置（check/deps/local/cloud/deploy/mount/test/all 可选项） |
| `scripts/verify_mount.py` | 静态验证插件文件/自包含识别库/配置，并打印挂载命令 |
| `scripts/test_all.py` | 全量回归测试入口（82 用例：CLI/引擎/配置三套件） |
| `panel/` | 旧 8790 控制面板（**已退役**，源码保留参考，勿启动） |
| `AGENTS.md` | dsh 会话软规则模板 + GUI 配置指引 |

## 快速开始（源码运行 dsh）

### 一键安装（推荐）

```bash
python scripts/install.py            # 交互模式：逐项选择
python scripts/install.py --all      # 全流程（依赖→本地→云端→部署→测试）
```

所有步骤都是**可选项**，可单独执行：

| 选项 | 作用 |
|------|------|
| `--check` | 环境检查（Python/Ollama/依赖/视觉模型/云端通道） |
| `--deps` | 安装 Python 依赖 |
| `--local` | 配置本地引擎（检测/拉取 Ollama 视觉模型，如 qwen2.5vl） |
| `--cloud` | 配置云端通道（**可选**：dashscope/Gemini/GLM/自定义，提升精度上限） |
| `--deploy` | 部署识别库到 `~/.dsh/vision`（可选，插件目录已自带） |
| `--mount` | 挂载插件到 dsh profile（`pnpm dsh plugin --profile web add`） |
| `--test` | 跑 CLI 回归测试（82 用例） |

### 手动安装

```bash
pip install -r python/requirements.txt   # Python 依赖
ollama pull qwen2.5vl                    # 本地视觉模型
pnpm dsh plugin --profile web add D:/deepseek-harness/plugins/dsh-vision   # 挂载
```

### 精度上限：本地 vs 云端

- **本地（默认）**：Ollama + qwen2.5vl，零费用、数据不出机器；精度受显卡配置限制
- **云端（可选）**：`python scripts/install.py --cloud` 配置任一 OpenAI 兼容厂商
  （通义 qwen-vl-max / Gemini / GLM-4V），识别走云端大模型，**精度不受本地硬件限制**
- **自动回退**：云端 key 未设置时自动走本地 Ollama，配置了即切换，互不干扰
- 云端配置也可在 GUI 完成：设置 → 插件 → dsh-vision → 云端厂商列表(JSON)

### 使用

- 让模型看本地图片：`describe_image("D:/a.png")`
- 粘贴截图：dsh 自动转成文字描述，模型不会收到 image block
- 提取报错文字：`extract_text("D:/error.png")`
- 找 UI 元素：`locate_object("D:/ui.png", "提交按钮")`
- 对比两张图：`compare_images("D:/a.png", "D:/b.png")`

## 设计要点（从 visual-ds 继承的工程思想）

- **单一识别引擎**：所有入口（工具、粘贴兜底、CLI）都走 `python/vision_cli.py` → `vision_client`，不重复实现视觉逻辑。
- **三层容错**：工具失败不阻塞主流程；粘贴图识别失败时替换为占位文字，保证纯文本模型永远不收到 image block。
- **本地优先、零费用**：默认 Ollama，数据不出机器；可通过 GUI 或 `python/config.json` 切换到 OpenAI 兼容云端。
- **薄插件**：dsh 侧只做工具注册和事件改写，重活全在 Python 引擎；改引擎不碰 dsh，改 dsh 不碰引擎。
- **幂等/可回退**：插件通过 profile bundle 安装/卸载；本体改动仅 4 处（见 `docs/upstream-changes.md`），升级 dsh 后需重新核对。

## 验证挂载

```bash
python "F:\code of PY\dsh_vision\scripts\verify_mount.py"
```

它会静态检查 `cordis.yml`、`src/index.ts`、自包含识别库（vision_client/config_loader/prompts）、配置文件是否存在，并给出实际挂载命令。

## 自定义

编辑 `python/config.json`：

```json
{
  "ollama": {
    "url": "http://localhost:11434/api/generate",
    "model": "qwen2.5vl",
    "temperature": 0.5,
    "top_p": 0.8,
    "grounding": true,
    "precision": "standard"
  }
}
```

- 换模型：改 `model`，例如 `llava`、`qwen2.5vl:13b`
- 换精度：`src/index.ts` 的 `precision` 配置（`fast` / `standard` / `deep`）
- 云端：在 `config.json` 增加 `cloud` 与 key，`vision_client` 自动切换

## 已知限制

- 当前粘贴兜底只处理直接位于用户消息 `content` 中的 `image` 块；工具返回里的 image 块暂不转换（dsh 文本模型本就不应产生）。
- 识别耗时取决于本地模型；建议 `fast` 用于高频截图，`deep` 用于需要空间结构/推测的场景。
- 插件处于开发者预览阶段，dsh API 若变动需同步调整。
