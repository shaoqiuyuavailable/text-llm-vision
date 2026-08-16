# dsh 本体源码改动记录（历史归档 · 已废弃）

> **状态：已废弃（2026-08-16）——本插件不再需要修改 dsh 本体源码，本文档仅作历史回溯，勿按旧流程操作。**

## 为什么废弃

1. **聊天气泡方案切换为 vision-router 式**：图片块原样保留在会话日志 → GUI 天然显示原图（零本体改动）；识别文本只在模型输入层改写（wrapper 适配器）。原先依赖本体改动 3（`MessageItem.tsx` 跳过 `dshVision` 文本 + 提升 `dshAttachment` 缩略图）的机制废弃。
2. **插件能力已并入 dsh-vision-router**（本地 Ollama 后端 / 即时翻译 / 结构化识别 / 桌面截屏），后续维护随 router 走；`dsh-vision` 设置命名空间（改动 2）无宿主。

## 回退记录（2026-08-16 晚）

- 回退时机：切换测试 vision-router 期间 dsh-vision 被禁用，改动 1 的图片放行声明失去改写兜底 → image 块落盘历史 → serializer 每轮 `UNSUPPORTED_CONTENT` → 会话锁死。
- 回退方式：`D:\deepseek-harness` 工作区 git 还原（当前 git 干净，仅剩 `plugins/`、`.dsh-browser/` 未跟踪）。

## 曾经的四处改动（供回溯，勿重新应用）

| # | 文件 | 性质 | 作用 |
|---|------|------|------|
| 1 | `packages/llm/llm-deepseek/src/adapter.ts` | 修改 | 模型能力声明加 `image`，网关放行图片进入消息流 |
| 2 | `packages/host/apiproxy/src/api-proxy.ts` | 修改 | Web 设置白名单加 `dsh-vision` 命名空间 |
| 3 | `packages/client/ui-conversation/src/client/chat/MessageItem.tsx` | 修改 | GUI 跳过 `dshVision` 识别文本块 + 提升 `dshAttachment` 为原图 |
| 4 | `packages/client/ui-settings-plugins/` | 新增+修改 | dsh-vision 配置卡片（GUI 集成） |

> 如需完整旧版记录（含行号、改动前后代码、升级核对方法），见 git 历史 `654ba91` 之前的 `docs/upstream-changes.md`。
