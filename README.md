# dsh-vision：DeepSeek Harness 外接视觉插件

> 让 dsh 默认的纯文本 DeepSeek 模型也能"看见"图片——图片进、文字描述出。
> 本地 Ollama 零费用，或可选云端大模型（通义/Gemini/GLM）提升精度上限。
> 识别引擎自包含（visual-ds v2 基线封存迁移），不依赖外部目录。

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

---

## ⚠️ 注意事项（必读）

### 对本地源码的更改细节（升级 dsh 后必须重新核对）

本插件需要对 dsh 本体源码做 **4 处改动**才能完整工作。**完整记录见 [`docs/upstream-changes.md`](docs/upstream-changes.md)**（含行号、改动前后代码、升级核对方法），摘要：

| # | 文件 | 改动 | 作用 |
|---|------|------|------|
| 1 | `packages/llm/llm-deepseek/src/adapter.ts` | `inputModalities` 两处 `['text']` → `['text','image']` | **最关键**：网关放行图片进入消息流，插件兜底才有机会执行 |
| 2 | `packages/host/apiproxy/src/api-proxy.ts` | `WEB_SETTINGS_NAMESPACES` 加 `'dsh-vision'` | GUI 卡片可读写插件配置 |
| 3 | `packages/client/ui-conversation/.../MessageItem.tsx` | `contentParts` 跳过 `dshVision` 块 + 提升 `dshAttachment` 为原图 | 气泡不显示识别文本、显示原图 |
| 4 | `packages/client/ui-settings-plugins/` | 新增 VisionCard + controller + locales | GUI 配置卡片 |

> **安全网**：改动 1 只是"声明层放行"——图片真正到达 API 前仍被 serializer 的 `UNSUPPORTED_CONTENT` 拒绝（插件未挂载时 fail loud，不静默）。改动 3/4 是 UI 层，不重建 bundle 只影响显示。
>
> **升级流程**：`grep` 核对 4 处 → 重建 GUI（`pnpm --filter ... run bundle` + `pnpm --filter @deepseek-ai/dsh-web-frontend run build`）→ 重启 dsh。详见 upstream-changes.md。

### 环境依赖

| 依赖 | 必需？ | 说明 |
|------|--------|------|
| Python 3.9+ | ✅ 必需 | 识别引擎运行环境 |
| pip 包（httpx / Pillow） | ✅ 必需 | `python scripts/install.py --deps` |
| Ollama + 视觉模型（qwen2.5vl） | 本地必需 | 云端配置后可省；`ollama pull qwen2.5vl` |
| 云端 API key | 可选 | 走 `<NAME>_API_KEY` 环境变量或 config，提升精度上限 |
| dsh 本体 4 处改动 | ✅ 必需 | 见上表，升级后需重打 |

> **自包含**：识别引擎（vision_client/config_loader/prompts）随插件 `python/` 部署，**不依赖**外部 visual-ds 目录或 `~/.claude/vision-eyes`。

---

## ✨ 核心卖点

### 1. 对 DeepSeek 官方模型"外挂"视觉处理机制

DeepSeek 官方模型是纯文本，官方视觉方案（`read_image`）需要换支持 image 的模型。本插件**不改模型、不改 API**——通过 `agent/pre-step` 钩子在消息进入模型前拦截图片：

```
粘贴/上传图片 → 网关放行（本体改动 1）→ 消息入流
  → 插件钩子拦截 → 本地/云端识别 → 替换为文本块
  → DeepSeek 模型只收到文字（图片字节永不进 API）
```

- **模型无关**：任何文本模型原地获得视觉能力
- **零 API 费用**（本地）/ 可选云端
- **图片不出机器**（本地默认）
- 识别失败自动降级占位，**永不把图片发给纯文本 API**

### 2. 聊天气泡支持图片

识别文本块携带附件引用（`dshAttachment`），GUI 渲染为**原图缩略图**：

- 用户消息气泡 = **你的文字 + 原图**，识别文本自动隐藏
- **历史回溯**：翻旧会话仍能看到当时贴的图（附件按 sha256 内容寻址存储）
- 社区插件大多"图片转文字就丢图"——本插件**图文双保留**（模型看文本、你看原图）

### 3. 精细化路由与针对性识别

**5 引擎场景路由**（识别前决策，非通用 VLM 直出）：

| 场景 | 引擎 | 针对性 |
|------|------|--------|
| 聊天记录 `document.chat` | ocr | RapidOCR 本地提取，纯文字零幻觉 |
| 代码 `document.code` | code | 逐字转写引擎（保真度高于通用 OCR） |
| 表格 `document.table` | table | Markdown 表格提取 |
| UI 截图 `screenshot.software_ui` | gui | 界面元素结构化枚举 |
| 图表/其他 `_default` | vlm | Scan→Zoom→Guess 三阶段视觉理解 |

- **混合场景识别**：一张图多人+飞机 → 多分支各自路由、各提各的
- **模型级覆盖**：路由值可写 `引擎:模型`（如 `vlm:qwen-vl-max`），按场景指定本地/云端模型
- 路由表 GUI 可改（JSON 字段），零代码定制

### 4. 其他细节

- **无缝粘贴**：零操作，粘贴即识别（对比：部分插件需切模型组/落盘中转）
- **GUI 全控**：设置 → 插件 → dsh-vision 卡片 12 项配置，保存即生效（无需重启）
- **云端灵活**：通义/Gemini/GLM/自定义，无 key 自动回退本地
- **防御性**：120s 看门狗 kill、引擎异常回退 vlm、OCR 失败升级视觉模型、输出清洗防 JSON 崩溃、并发信号量防单卡雪崩
- **同图去重**：进程内 LRU（sha256），重复查看零识别、零计费
- **一键安装**：`scripts/install.py` 7 个可选项（check/deps/local/cloud/deploy/mount/test）
- **82 用例回归**：CLI 16 + 引擎 39 + 配置 27，mock 引擎不依赖网络

---

## ⚖️ 优劣对比

### vs 官方视觉方案（`read_image` + 支持 image 的模型）

| 维度 | 官方 | dsh-vision |
|------|------|-----------|
| 机制 | 图片字节原生进模型上下文 | 图片 → 识别 → 文本进上下文 |
| 信息保真 | 无损（模型看像素） | 有损（识别转述），云端大模型可缩小差距 |
| 模型要求 | 必须视觉模型（付费） | **任何文本模型**（DeepSeek 原生可用） |
| 成本 | 视觉 API 计费 | 本地零 / 云端可选 |
| 隐私 | 图片必然出网 | 本地零出网 |
| 工具面 | 1 个（read_image） | 5 个（描述/OCR/定位/对比/规则） |
| 场景特化 | 无 | 5 引擎路由 + 混合场景 |
| 粘贴体验 | 需模型支持，DeepSeek 下直接失败 | 无缝拦截，气泡显原图 |

**结论**：官方赢在"信息无损"（架构性差距，补不齐）；dsh-vision 赢在模型无关/成本/隐私/场景深度/体验。**配好云端 API 后，90%+ 场景识别质量已接近官方**。

### vs 第三方第一梯队（dsh-vision-router，社区评分最高）

| 维度 | router | dsh-vision |
|------|--------|-----------|
| 工具面 | **13 个像素工具**（crop/pixel_diff/trace/...） | 5 个理解工具 |
| 后端 | 免 key OVH 匿名回退（限速）+ 用户模型 | 本地 Ollama + 任意云端 |
| 路由 | 工具级编排（vision chain 多步循环） | **引擎级场景路由**（识别前决策） |
| 粘贴 | 需切 "+ Auto Vision" 模型组 | **无缝拦截，零操作** |
| 聊天气泡 | 无原图（改写即丢图） | **图文双保留** |
| GUI | 设置卡片（模型组编排） | 卡片 12 项（引擎参数全控） |
| 依赖 | 无 Python（sharp/potrace/tesseract/Chrome） | Python + Ollama（自包含） |
| 测试 | 149 用例 + doctor CLI | 82 用例 + 文档链 |
| 隐私 | 默认走匿名云端 | 本地零出网 |

**结论**：router 赢在"像素工具广度 + 零依赖 + 免 key 开箱"；dsh-vision 赢在"场景路由深度 + 无缝体验 + 图文保留 + 隐私 + GUI 全控"。**定位差异化**：router 是"图像处理工具箱"，dsh-vision 是"最懂对话场景的视觉助手"。

---

## 📁 文件

| 文件 | 作用 |
|------|------|
| `cordis.patch.yml` / `cordis.yml` | 插件装载补丁（bundle 标准命名，`name: dsh-vision`） |
| `src/index.ts` | dsh 原生插件：5 工具 + 粘贴兜底 + GUI 配置同步 |
| `python/vision_cli.py` | Python CLI：统一调 analyze/ocr/locate/compare |
| `python/vision_client.py` | 识别引擎（自包含：5 引擎路由/混合场景/缓存） |
| `python/config_loader.py` | 配置读取（`~/.dsh/vision/config.json`，v2 路由基线） |
| `python/prompts.py` | v2 提示词体系 + 路由/模型基线表 |
| `python/config.json` | 引擎默认配置（可被 `~/.dsh/vision/config.json` 覆盖） |
| `scripts/install.py` | 一键安装（check/deps/local/cloud/deploy/mount/test/all） |
| `scripts/verify_mount.py` | 静态挂载检查 |
| `scripts/test_all.py` | 全量回归（82 用例） |
| `scripts/smoke-apply.mjs` | Node 侧冒烟（插件 apply 不抛错） |
| `docs/upstream-changes.md` | **本体源码改动记录（升级必读）** |
| `docs/architecture.md` | 架构设计 |
| `panel/` | 旧 8790 面板（**已退役**，勿启动） |
| `AGENTS.md` | 会话软规则模板 + GUI 配置指引 |

---

## 🚀 快速开始

```bash
python scripts/install.py --all   # 全流程（依赖→本地→云端(交互)→部署→测试）
python scripts/install.py --mount # 或单独挂载
pnpm dsh web                      # 重启生效
```

- 更细步骤见 [QUICKSTART.md](QUICKSTART.md)
- 升级 dsh 后：按 [docs/upstream-changes.md](docs/upstream-changes.md) 重新核对本体改动
- 版本演进见 [CHANGELOG.md](CHANGELOG.md)

## 🛠️ 自定义

编辑 `python/config.json` 或 GUI 卡片（设置 → 插件 → dsh-vision）：

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

- 换模型：改 `model`（如 `llava`、`qwen2.5vl:13b`）
- 换档位：GUI 卡片 `level`（off/fast/standard/deep）
- 云端：GUI 卡片"云端厂商列表(JSON)"或 `--cloud` 脚本
- 场景路由：GUI 卡片"场景路由表(JSON)"或 config `router`

## 📋 已知限制

- 粘贴兜底只处理用户消息 `content` 中的 image 块；工具返回里的 image 块不转换（文本模型本就不应产生）
- 识别耗时取决于模型：`fast` 适合高频截图，`deep` 用于空间结构/推测
- 本体 4 处改动是升级 dsh 时的重打点（`upstream-changes.md` 是权威记录）
- dsh API 若变动需同步调整（插件处于活跃开发期）
