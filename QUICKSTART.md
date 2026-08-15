# dsh-vision 快速开始

从零到"粘贴图片自动识别"的最短路径。

## 环境要求

- DeepSeek Harness（dsh）源码运行，且**已应用本体改动**（见 `docs/upstream-changes.md`）
- Python 3.9+
- Ollama（本地引擎）或 云端 API key（可选）

## 1. 一键安装

```bash
python scripts/install.py --all
```

`--all` = 依赖 → 本地引擎 → 云端通道（交互）→ 部署 → 测试。也可分步：

```bash
python scripts/install.py --check   # 环境检查（推荐先跑）
python scripts/install.py --deps    # Python 依赖
python scripts/install.py --local   # 拉取/检测 Ollama 视觉模型
python scripts/install.py --cloud   # 配置云端厂商（可选）
```

## 2. 挂载插件

```bash
python scripts/install.py --mount
# 等价于：
pnpm dsh plugin --profile web add D:/deepseek-harness/plugins/dsh-vision
```

## 3. 重启并验证

```bash
pnpm dsh web
```

- 设置 → 插件 → dsh-vision 卡片：检查档位/模型配置
- 粘贴一张截图 → 消息气泡应显示原图缩略图 + 你的文字（识别文本隐藏）
- 对话中让模型看本地图片：`describe_image("D:/a.png")`

## 4. 回归测试

```bash
python scripts/test_cli.py     # 16 用例，mock 引擎，不依赖网络/模型
python scripts/verify_mount.py # 静态挂载检查
```

## 常见问题

**Q: 粘贴图片提示"当前模型不支持图片"**
本体改动 1 未应用。检查 `adapter.ts` 的 `inputModalities`，重打补丁后重启 dsh。

**Q: 气泡里显示识别文本（旧消息）**
新消息才带 `dshVision` 标记；历史消息保持原样显示。属预期行为。

**Q: 想切云端大模型提升精度**
`python scripts/install.py --cloud` 或 GUI 卡片"云端厂商列表(JSON)"。
key 用环境变量 `<NAME>_API_KEY`（推荐）或写入 config（明文警告）。

**Q: 档位 off 后模型不释放显存**
off 档会执行 `ollama stop`；若仍占用，手动 `ollama stop qwen2.5vl`。

**Q: 识别超时/卡住**
默认 120s 看门狗会自动 kill；可在 GUI 卡片调"识别超时"。

## 升级 dsh 后

按 `docs/upstream-changes.md` 重新核对/应用 4 处本体改动，重建 GUI 产物，重启。
