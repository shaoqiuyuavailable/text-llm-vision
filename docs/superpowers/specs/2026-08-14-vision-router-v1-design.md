# 视觉路由器 v1（Vision Router）设计

> 日期：2026-08-14 · 状态：已批准 · 来源：docs/vision-router-idea.md（思路 + 可行性评估）
> 决策：**先建立路由器架构，所有引擎当前统一用 qwen2.5vl（OCR 用已有 RapidOCR）**，后续替换专业模型。

## 目标

把 `analyze` 里单点 OCR 判断（`document.chat/code → RapidOCR`）升级为**可配置路由层**：
按 `scan` 场景查路由表 → 选引擎 → 统一输出进 `【细节】` 段。路由层先建好，**模型不换**
（VLM 类路由仍走 qwen2.5vl），后续换专业模型只改路由表。

## 架构

```
scan 判场景(16类) → _route_engine 查路由表 → _ENGINES[engine] 调引擎 → 统一进【细节】
                                                                    ↓
                                          deep 时 guess/spatial 仍用 VLM（qwen2.5vl）
```

### 路由表（config `router` 段，prompts.py `ROUTER` 为基线）

```json
"router": {
  "document.chat": "ocr",
  "document.code": "ocr",
  "_default": "vlm"
}
```

匹配规则：`scene.sub` 精确优先 → `scene` 大类 → `_default`。

### 引擎接口（统一签名 + 注册表）

```python
# 引擎注册表：name → callable(path_or_b64, scene, sub, scan_desc) → 事实文本
_ENGINES = {
    "ocr": _engine_ocr,   # RapidOCR（已有）；提取不足返回 "" → 调用方回退 vlm
    "vlm": _engine_vlm,   # qwen2.5vl：走现有 zoom（按类提示词）
}
```

后续换专业模型：加 `_engine_rapidtable` / `_engine_gui` 进注册表 + 改路由表指向即可，`analyze` 不动。

### 回退语义（沿用现有容错，不报错）

- 引擎未注册（路由表指向不存在的名字）→ 回退 `vlm`
- 引擎异常 / 输出为空（OCR 提取不足）→ 回退 `vlm`
- `vlm` 自身异常 → `[识别失败（引擎异常），已回退]` 占位

## 改动文件

| 文件 | 改动 |
|---|---|
| `prompts.py` | 加 `ROUTER` 基线 |
| `config_loader.py` | `_defaults()` 播种 `router`；`get()` 在 prompts_version 门内叠加 `router` |
| `vision_client.py` | `_route_engine` / `_ENGINES` / `_engine_ocr` / `_engine_vlm` / `_run_engine`；`analyze` 用路由层替换单点判断 |
| `config.json` | 加 `router` 段（本地覆盖层，不入库） |
| `tests/test_vision_client.py` | 路由测试 |

## 测试

- `_route_engine`：`document.chat→ocr`、`document.report→vlm`（_default）、`table→vlm`、未知→`vlm`
- `analyze` `document.chat` → 走 `ocr` 引擎（OCR 文本进【细节】）
- `analyze` `table` / 其它 → 走 `vlm`（zoom 输出）
- 路由表指向未注册引擎 → 回退 `vlm` 不报错
- OCR 提取不足 / 引擎异常 → 回退 `vlm`

## 验收

- 现有 63 测试全绿 + 新增路由测试
- 真实图：文字截图走 OCR、表格/界面走 qwen2.5vl
- 调用方无感：`analyze(path, precision, mode)` 签名不变
