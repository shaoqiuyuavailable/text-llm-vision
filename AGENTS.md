# 视觉能力使用规范（dsh-vision）

你的模型没有视觉能力。出现以下情况必须调用相应工具：

- 用户引用本地图片路径 / 粘贴截图 / 你看到图片占位符 → `describe_image(图片路径)`
- 终端红字、报错栈、文档扫描 → `extract_text(图片路径)`
- 图中某元素在哪里 → `locate_object(图片路径, 元素名)`
- 前后两张图对比 → `compare_images(图A路径, 图B路径)`
- 用户问"当前屏幕/界面是什么样"或需要看实时画面 → `take_screenshot(identify: true)`

## 强制规则

1. **需要查看图片内容时，调用 `describe_image`**，不要用 `read_image`。
   `read_image` 会把原生 image block 送入上下文，而当前 DeepSeek 文本模型不支持 image 输入。
2. **用户粘贴的图片已由插件自动转成文字**，无需额外处理。
3. 识别复杂对象（型号、角色、图表）时，`describe_image` 支持可选 `prompt` 参数。
4. 如果识别结果不确定，请结合上下文复核，不要仅凭视觉模型的一次输出下结论。

## 配置指引（面向用户的问题）

- 视觉档位/模型/云端/路由配置在 **dsh 设置 → 插件 → dsh-vision 卡片**（GUI），
  不是改插件源码。档位 `off` 关闭识别；`fast/standard/deep` 增强强度。
- 识别引擎已自包含（`python/` 目录），不依赖外部 visual-ds。
- 同图重复查看走进程内缓存，无需重复识别。
- 本体源码改动记录见 `docs/upstream-changes.md`（升级 dsh 后需重新核对）。
