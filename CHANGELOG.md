# Changelog

插件版本演进记录。视觉引擎版本跟随 visual-ds 基线（封存于 commit `0a34ad6`）。

## [0.1.0] - 2026-08-16（当前）

### 打磨（日志/健壮性/UI）
- **日志分级**：runVision debug（完整参数）+ info（耗时/输出量）+ warn（失败 code/耗时）；syncVisionConfig 改用 logger
- **stdout 上限**：子进程输出 1MB 截断（防异常输出撑爆内存），截断标记 `+capped`
- **识别失败降档重试**：deep 失败 → standard 重试一次（快速档不重试防重复计费）
- **识别耗时提示**：>5s 长识别在文本头部标注耗时（进度反馈轻量形态）
- **模型名下拉**：host 注册 `/dsh-vision/ollama-models` 路由（Ollama /api/tags，15s 缓存），GUI 模型字段可下拉（失败回退文本框）
- **云端厂商/路由行编辑**：`JsonRowEditor` 组件——路由表（场景→引擎下拉）、云端厂商（name/model/base_url/api_key 行）可视化编辑，非法 JSON 回退文本编辑
- **Node 冒烟纳入测试**：`test_all.py` 新增 `node` 套件（smoke-apply.mjs：6 工具 + 命名空间断言）

### 新增
- **截图工具**：`take_screenshot`（6 号工具）——截全屏/区域保存 PNG，`identify: true`
  立即识别返回文本；vision_cli 新增 `screenshot` 子命令 + 7 个回归用例
- **自包含引擎**：vision_client/config_loader/prompts 随插件部署，脱离外部
  visual-ds 目录与 `~/.claude/vision-eyes` 依赖；visual-ds 封存为版本基线
- **GUI 配置集成**：设置 → 插件 → dsh-vision 卡片（12 项配置，档位/模型/
  Ollama/上游/云端厂商/场景路由），替代退役的 8790 控制面板
- **云端通道**：任意 OpenAI 兼容厂商（通义/Gemini/GLM/自定义），key 走环境变量
  或 config；无 key 自动回退本地；一键脚本 `--cloud` 配置
- **场景路由 GUI 化**：`router` JSON 字段（scene → 引擎，可带模型覆盖）
- **同图去重缓存**：进程内 LRU（sha256 → 识别文本），防重复识别/计费；不落盘
- **一键安装脚本**：`scripts/install.py` 模块化可选项
  （check/deps/local/cloud/deploy/mount/test/all）
- **回归测试**：`scripts/test_all.py` 全量 89 用例（CLI 23 + 引擎 39 + 配置 27，mock 引擎不依赖网络/模型）
- **文档**：`docs/upstream-changes.md`（本体改动记录）、`docs/architecture.md`、
  `QUICKSTART.md`、`CHANGELOG.md`

### 变更
- 档位 `level` 成为唯一识别强度控制（GUI 移除冗余的 `precision` 字段，
  schema 保留作组合配置兜底）
- `off` 档主动 `ollama stop` 释放显存
- 安装脚本/verify_mount 更新为自包含检查

## [0.0.x] - 2026-08-15（早期迭代）

### 已实现
- 插件 bundle 安装（`dsh plugin --profile web add`，自动加入 bundles 层）
- 5 个视觉工具（describe_image/extract_text/locate_object/compare_images/vision_rules）
- 粘贴图片兜底（agent/pre-step）：图片 → 本地识别 → 文本块
- 网关放行：llm-deepseek adapter 能力声明加 `image`（本体改动 1）
- 消息顺序修复：用户文本在前、识别文本在后
- 原图回溯：`dshAttachment` 引用 → GUI 缩略图；识别文本 `dshVision` 标记隐藏

### 早期里程碑
- 插件静态检查 + 加载冒烟测试通过
- 粘贴链路端到端验证（会话日志确认 image block 不进入持久化）
- 历史记录审计：日志纯净（无图片字节）、附件完整（4 张原图可追溯）
- 回溯设计决策：模型侧用持久化识别文本（不重识别），用户侧 GUI 显原图
