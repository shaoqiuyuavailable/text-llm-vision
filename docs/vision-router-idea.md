# 视觉路由器（Vision Router）思路记录

> 提出：用户，2026-08-14。架构演进方向：从「单 VLM + 提示词分类」升级为「本地模型路由器」。
> 状态：**思路记录**（尚未定稿 / 未实施），实现方案见 brainstorming 产出。

## 核心思路

**根据图片类型路由到最合适的本地专用引擎**，统一输出结构化文本给主模型（DeepSeek 等纯文本 LLM）：

| 图片类型 | 路由目标 | 现有基础（text-llm-vision） |
|---|---|---|
| 文档 | **OCR** | ✅ 已有雏形：[vision_client.py](vision_client.py) 的 `analyze` 中 `document.chat/code` → RapidOCR |
| UI | **GUI 模型** | 部分：`zoom_screenshot` + `spatial`（grounding bbox）已是 UI 理解 |
| 图片 | **VLM** | ✅ 已有：`qwen2.5vl`（scan/zoom/guess 流水线） |
| 表格 | **Table Parser** | ❌ 需评估本地引擎（如 RapidTable / PaddleOCR 表格识别） |

## 价值

> **可能比「单纯给 LLM 接一个眼睛」更接近下一代 Agent**：
> 不同类型视觉任务交给**最擅长的引擎**（OCR 精确到字符、表格解析保留行列结构、
> GUI 模型擅长交互元素定位、VLM 负责通用理解），主模型只收**结构化文本**，
> 推理质量与效率都可能优于「一个 VLM 通吃 + 提示词硬分类」。

## 与现有体系的关系

- 现有 `scan` 场景分类（v2：16 大类）天然是**路由决策的输入**——`scan` 判场景后，
  不是选 `zoom_{大类}` 提示词，而是**选引擎**（OCR / Table Parser / VLM / GUI）。
- 路由结果统一进现有 `analyze` 输出结构（【初步判断】【细节】【推测】…），
  代理/MCP 调用方**无感**（仍走 `analyze` / `describe_image`）。
- 需要评估：本地是否有可用的 Table Parser / GUI 专用模型；没有则回退 VLM（降级不报错）。

## 可行性评估（2026-08-14）

### 引擎可用性矩阵

| 路由目标 | 本地引擎 | 可用性 | 落地成本 |
|---|---|---|---|
| 文档 → OCR | **RapidOCR**（`rapidocr_onnxruntime`，已在依赖） | ✅ 已有 | 仅扩展路由（当前只 `document.chat/code`） |
| 表格 → Table Parser | **RapidTable**（`pip install rapid-table`，ONNX ~7MB，输出 **HTML 结构** + 单元格 box） | ✅ 轻量可行 | 新依赖 1 个，零重框架 |
| UI → GUI 模型 | **qwen2.5vl grounding**（现有 `spatial` bbox 已是 UI 元素定位）；可选 **UI-R1-E-3B**（3B，Ollama 可跑，ScreenSpotV2 89.5%） | ✅ 现有够用 | 零成本；升级可选 |
| 图片/其它 → VLM | **qwen2.5vl**（scan/zoom/guess 流水线） | ✅ 已有 | 零成本 |

### 关键结论

1. **现有代码已是 ~60% 路由器**：`analyze` 里 `document.chat/code → RapidOCR` 正是路由雏形（[vision_client.py](vision_client.py)），只需系统化。
2. **路由决策已就绪**：v2 的 16 大类 `scan` 分类天然是路由输入，**无需独立路由模型**。
3. **唯一新依赖 = `rapid-table`**（7MB ONNX，English 模型内置，同源 RapidAI，与现有 rapidocr 同生态）。
4. **UI 不需要专门 GUI 模型**：qwen2.5vl 自带 grounding（bbox），`zoom_screenshot` + `spatial` 已覆盖 UI 理解；GUI-Owl / ZonUI-3B / UI-R1-E-3B 等是可选增强，非必需。

### 落地点（若实施，属 architectural）

- `vision_client.analyze`：把 `scene == "document" and sub in ("chat","code")` 的单点判断升级为**路由表**——
  `document → OCR`、`table → RapidTable(HTML)`、`screenshot/UI → VLM grounding`、其它 → VLM。
- 引擎输出统一进现有 `【细节】` 段；引擎缺失/失败 → 回退 VLM（降级不报错，沿用现有容错模式）。
- config 可配路由开关（如 `router.table: true`）。
- 代理 / MCP 调用方无感（仍走 `analyze` / `describe_image`）。

### 建议分步（每步可独立验证）

1. **① document 全类走 OCR**——纯扩展现有路由（`document.*` → OCR，不只有 chat/code），**零新依赖**，立即收益。
2. **② table → RapidTable**——装 `rapid-table`，`table` 场景走 HTML 结构输出。
3. **③ UI 可选换模型**——需要更强 UI grounding 时，Ollama 拉 `UI-R1-E-3B` 等 3B 模型。

### 结论

**可行性高**：路由器架构在本地大部分已具备（OCR 已有、VLM 已有、路由决策已有），只需系统化 + 引入一个轻量 RapidTable。价值符合用户判断——「按类型交给最擅长引擎」比「单 VLM + 提示词硬分类」更接近 Agent。
