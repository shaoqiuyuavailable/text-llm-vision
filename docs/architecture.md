# dsh-vision 架构

让 DeepSeek Harness（纯文本模型）获得视觉能力的完整方案：**图片进、文字出，全程可控**。

## 三层链路

```
用户 / 模型
  │
  ├─ ① 模型主动看图（工具）─────────────────────────────────────┐
  │    describe_image / extract_text / locate_object            │
  │    / compare_images / vision_rules                          │
  │                    ↓                                        │
  ├─ ② 粘贴图片兜底（agent/pre-step 钩子）───────────────────────┤
  │    粘贴 → 网关放行（本体改动 1）→ 消息入流 → 钩子拦截          │
  │    → 本地/云端识别 → 文本块(dshVision + dshAttachment)       │
  │    → 模型收到识别文本 + GUI 显示原图                          │
  │                    ↓                                        │
  └─ ③ 识别引擎（python/vision_cli.py → vision_client）─────────┘
       Scan → Zoom → Guess 三阶段
       场景路由：ocr / vlm / table / gui / code
       本地 Ollama 或 云端 OpenAI 兼容
```

## 核心机制

### 1. 粘贴兜底（agent/pre-step）

dsh 的 `agent/pre-step` waterfall 在消息进入 step 前可改写消息。插件把 image block
替换为识别文本块：

```
原消息:  [image(sha256附件), text "看看效果"]
改写后:  [text "看看效果", text "[用户粘贴图片，已由本地视觉识别]..."(dshVision)]
```

- **模型侧**：识别文本进入上下文（历史恢复时直接可用，不重复识别）
- **GUI 侧**：`dshVision` 标记块被 `MessageItem.tsx` 跳过显示，`dshAttachment`
  引用被提升为原图缩略图（用户侧历史回溯）
- **顺序**：用户文本在前、识别文本在后（避免长识别结果淹没用户输入）

### 2. 配置流（GUI 集成）

```
GUI 卡片（设置 → 插件 → dsh-vision）
  → dsh settings 命名空间（$DSH_HOME/settings.yaml，本体改动 2 放行）
  → 插件 installSettingsSection onChange
  → 写入 ~/.dsh/vision/config.json + state
  → vision_cli.py 每次调用（独立进程）读文件即生效
```

12 个配置项：档位 / 识别超时 / 视觉模型 / Ollama 地址 / 温度 / Top P / Grounding /
上游(Anthropic/OpenAI) / 云端厂商(激活) / 云端厂商列表(JSON) / 场景路由表(JSON)。

### 3. 档位语义

| 档位 | 行为 |
|------|------|
| `off` | 不识别：粘贴图片 → 占位文本 + 原图保留；并 `ollama stop` 释放显存 |
| `fast` | 1 次调用（scan 快速描述）|
| `standard` | scan + zoom（描述 + 场景 + 细节）|
| `deep` | scan + zoom + guess + grounding（空间结构，引擎内缓存启用）|

### 4. 场景路由（继承 visual-ds v2）

```
router: {
  "document.chat": "ocr",        // 聊天记录 → RapidOCR
  "document.code": "code",       // 代码 → 逐字转写引擎
  "document.table": "table",     // 表格 → Markdown 提取
  "chart": "vlm",                // 图表 → 视觉模型
  "screenshot.software_ui": "gui", // UI 截图 → 界面元素枚举
  "_default": "vlm"
}
```

路由值可带模型覆盖：`"chart": "vlm:qwen-vl-max"`（配合 `models` 注册表，
`type=cloud` 走云端厂商）。

### 5. 后端选择与回退

```
本地（默认）：Ollama + qwen2.5vl，零费用、数据不出机器
云端（可选）：任一 OpenAI 兼容厂商（通义/Gemini/GLM/自定义）
回退：云端 key 未设置（或无 <NAME>_API_KEY 环境变量）→ 自动走本地
```

## 鲁棒性与兜底

| 机制 | 位置 | 说明 |
|------|------|------|
| 识别超时看门狗 | `src/index.ts` runVision | 120s 超时 kill 子进程 + 占位文本 |
| httpx 超时 | vision_client | 每请求 120s 双保险 |
| 引擎回退 | `_run_engine` | 未注册/异常引擎 → 回退 vlm |
| OCR 回退 | `ocr()` 空串 → 视觉模型 | 纯文字提取失败自动升级 |
| 并发保护 | `BoundedSemaphore(2)` | 防本地单卡雪崩 |
| 输出清洗 | `_sanitize` | 孤立代理字符 → `?`，防 JSON 崩溃 |
| 配置回退 | config_loader | 损坏 config → 内置默认 |
| 同图去重 | `src/index.ts` 进程内 LRU | sha256 → 识别文本，防重复识别/计费 |
| 失败兜底 | pre-step catch | 识别失败 → 占位文本 + 原图保留 |

## 自包含设计

识别引擎（vision_client/config_loader/prompts）随插件 `python/` 目录部署，
不依赖外部 visual-ds 目录或 `~/.claude/vision-eyes`。visual-ds 已封存为版本基线
（路由 v2 + 5 引擎 + 混合场景识别全部继承）。

## 与官方视觉方案的关系

- **官方**（`read_image` + 支持 image 的模型）：图片字节原生进模型上下文，信息无损；
  但必须路由到视觉模型（付费、图片出网），且 DeepSeek 文本模型不可用
- **本插件**：识别转文本（信息有损，但云端大模型可显著缩小差距）；模型无关、
  成本可控、隐私可保、场景特化深度更高
- 两者互补，非替代：官方解决"换眼睛"，本插件解决"配眼镜"
