# dsh 本体源码改动记录（升级必读）

本插件要完整工作，需要对 DeepSeek Harness 本体源码做少量改动。本文件是**权威记录**：
每次升级/重新安装 dsh 后，按此清单核对并重新应用改动。

> 状态：dsh 版本 **0.1.0-rc.5**（2026-08-16 验证）。改动位置以行号为参考，升级后行号可能漂移，按函数名/特征字符串定位。

---

## 改动总览

| # | 文件 | 性质 | 作用 |
|---|------|------|------|
| 1 | `packages/llm/llm-deepseek/src/adapter.ts` | 修改 | 模型能力声明加 `image`，网关放行图片进入消息流 |
| 2 | `packages/host/apiproxy/src/api-proxy.ts` | 修改 | Web 设置白名单加 `dsh-vision` 命名空间 |
| 3 | `packages/client/ui-conversation/src/client/chat/MessageItem.tsx` | 修改 | GUI 跳过 `dshVision` 识别文本块 + 提升 `dshAttachment` 为原图 |
| 4 | `packages/client/ui-settings-plugins/` | 新增+修改 | dsh-vision 配置卡片（GUI 集成） |

---

## 改动 1：llm-deepseek adapter 能力声明（**最关键**）

**文件**：`packages/llm/llm-deepseek/src/adapter.ts`

**背景**：dsh 网关（api-proxy 的 `prompt` 入口）在消息进入 agent 前检查模型能力：
`modelInfo.inputModalities.includes('image')`，不通过直接拒绝（客户端显示"当前模型不支持图片"）。
llm-deepseek adapter 原本把 `inputModalities` 硬编码为 `['text']`，导致图片**永远进不了消息流**，
插件挂在 `agent/pre-step` 的粘贴兜底钩子**没有机会执行**。

**改动**：两处 `['text']` → `['text', 'image']`：

1. `modelInfo()` 函数（约 L107-115）：
```ts
function modelInfo(provider: string, model: DeepSeekCatalogModel): LlmModelInfo {
  return {
    ...
    // The wire route is text-only, but the capability declaration admits image
    // so the host gateway lets pasted images into the agent flow: the
    // dsh-vision plugin rewrites image blocks to local-vision text at
    // agent/pre-step, and the serializer's UNSUPPORTED_CONTENT check below
    // remains the last line of defense when no such plugin is mounted.
    inputModalities: ['text', 'image'],
  }
}
```

2. `resolveModel()` 的 uncatalogued fallback（约 L189-191）：
```ts
...configured === undefined
  ? { provider, id: model, name: model, inputModalities: ['text' as const, 'image' as const] }
  : modelInfo(provider, configured),
```

**语义**：这是**声明层放行**，不是真的把图片发给 DeepSeek API——图片进入消息流后，
由插件在 `agent/pre-step` 转成识别文本。**安全网仍在**：`serialize.ts` 的
`assertTextOnly`/`UNSUPPORTED_CONTENT` 检查原样保留——若插件未挂载，图片会在
serializer 被拒（fail loud），不会真的发给 API。

**升级核对**：搜索 `inputModalities: ['text']`，确认两处都含 `'image'`。

---

## 改动 2：api-proxy Web 设置白名单

**文件**：`packages/host/apiproxy/src/api-proxy.ts`

**背景**：dsh 的 Web 设置 API 只对**白名单命名空间**开放读写（`WEB_SETTINGS_NAMESPACES`），
白名单外的命名空间返回 `settings-not-exposed`。要让 GUI 卡片读写插件的 `dsh-vision`
命名空间，必须加入白名单。

**改动**（约 L126-129）：
```ts
const WEB_SETTINGS_NAMESPACES = [
  'agent-loop', 'shell', 'locale', 'permission', 'ui-conversation', 'ui-theme', 'web-search-deepseek',
  'dsh-vision',   // ← 新增
] as const
```

**升级核对**：搜索 `WEB_SETTINGS_NAMESPACES`，确认含 `'dsh-vision'`。

---

## 改动 3：GUI 消息渲染（识别文本隐藏 + 原图展示）

**文件**：`packages/client/ui-conversation/src/client/chat/MessageItem.tsx`

**背景**：插件把粘贴图片转成识别文本块（带 `dshVision: true` 标记 + `dshAttachment`
附件引用）。模型需要看到识别文本（已持久化），但用户消息气泡不应显示识别文本、
应显示原图缩略图。GUI 的 `contentParts()` 是消息内容 → 显示分区的唯一入口。

**改动**：
1. 新增 `VisionTextBlock` 接口（约 L22-29）：
```ts
interface VisionTextBlock {
  type?: 'text' | string
  text?: string
  dshVision?: unknown
  dshAttachment?: UserImage['attachment']
}
```

2. `contentParts()` 循环加过滤（约 L31-40）：
```ts
for (const block of content) {
  const b = block as VisionTextBlock & { attachment?: unknown }
  // dsh-vision 识别块：文本跳过（气泡不显示识别结果），附件引用提升为原图。
  if (b.type === 'text' && b.dshVision === true) {
    if (b.dshAttachment !== undefined) images.push({ attachment: b.dshAttachment })
    continue
  }
  if (b.type === 'text' && typeof b.text === 'string') texts.push(b.text)
  ...
}
```

**语义**：`dshVision` 标记块只影响 GUI 显示；模型侧 serializer 只读 `text` 字段，
多出的 `dshVision`/`dshAttachment` 字段被忽略，模型收到内容不变。

**升级核对**：搜索 `dshVision`，确认过滤存在。改后需重建：
```bash
pnpm --filter @deepseek-ai/dsh-client-ui-conversation run bundle
pnpm --filter @deepseek-ai/dsh-web-frontend run build
```

---

## 改动 4：插件配置卡片（GUI 集成）

**目录**：`packages/client/ui-settings-plugins/src/client/`

**新增文件**：
- `VisionCard.tsx` — dsh-vision 卡片 UI（12 个配置控件）
- `vision-card-controller.ts` — 表单逻辑（CardForm 模式 + JSON 字段）

**修改文件**：
- `index.ts` — 实例化 `VisionCardController`、注册 `settings.plugin.item` 槽（id `dsh-vision`, order 30）
- `locales.ts` — 新增 `vision*` 系列中英文案键

**升级核对**：搜索 `dsh-vision`，确认卡片注册存在。改后需重建：
```bash
pnpm --filter @deepseek-ai/dsh-client-ui-settings-plugins run bundle
pnpm --filter @deepseek-ai/dsh-web-frontend run build
```

---

## 重新应用改动的完整流程

```bash
# 1. 核对改动 1、2（源码行）
grep -n "inputModalities" packages/llm/llm-deepseek/src/adapter.ts
grep -n "WEB_SETTINGS_NAMESPACES" -A 4 packages/host/apiproxy/src/api-proxy.ts

# 2. 核对改动 3、4（GUI），如有缺失重新打补丁
grep -n "dshVision" packages/client/ui-conversation/src/client/chat/MessageItem.tsx
grep -n "dsh-vision" packages/client/ui-settings-plugins/src/client/index.ts

# 3. 重建 GUI 产物
pnpm --filter @deepseek-ai/dsh-client-ui-conversation run bundle
pnpm --filter @deepseek-ai/dsh-client-ui-settings-plugins run bundle
pnpm --filter @deepseek-ai/dsh-web-frontend run build

# 4. 重启 dsh，验证
pnpm dsh web
```

---

## 已知边界

- **改动 1 是全局声明**：`inputModalities` 含 `image` 会让**所有**走 deepseek-official 的
  会话接受图片入流（GUI 模型选择器也会显示"支持图片"）。插件未挂载时图片会在
  serializer 被拒（错误信息变为 "does not support image content"，而非网关的
  "当前模型不支持图片"）——这是有意保留的 fail-loud 安全网。
- **改动 3、4 是 UI 层**：不重建 bundle 不影响 host 功能，只影响显示。
- 插件自身（`plugins/dsh-vision/`）不在上述改动内；它通过 profile bundle 机制加载，
  卸载/重装不影响本体。
