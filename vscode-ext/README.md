# text-llm-vision 可视化插件

在 VS Code 侧边栏动态展示并修改 [text-llm-vision](../README.md) 代理的配置——档位、后端、端口、温度/top_p、上游、云端厂商。**复用后端控制 API（`/api/*`），不重写任何识别逻辑。**

## 前置

- 后端已部署（`python install.py`，见主 README「一键部署」）
- VS Code ≥ 1.80（配 Claude Code for VS Code 使用）

## 安装

**dev 模式（调试开发）**：
```bash
cd vscode-ext
code .                    # 用 VS Code 打开本目录
# 按 F5 启动「扩展开发宿主」，侧边栏出现 Vision 图标
```

**打包安装**：
```bash
cd vscode-ext && bash scripts/package.sh   # 产出 .vsix
code --install-extension text-llm-vision-*.vsix
```

## 使用

侧边栏活动栏点 **Vision** 图标 → 控制台面板：

| 区域 | 操作 | 底层调用 |
|------|------|---------|
| 健康横幅 | 显示代理 pid/版本/uptime；代理未运行时「启动代理」 | `GET /api/status`、`start_proxy.py` |
| 档位 | 0/1/2/3 一键切换（off 自动 `ollama stop` 释放显存） | `POST /api/level` |
| 后端 | 本地 / 云端 + 厂商下拉 | `POST /api/backend` |
| 端口 | 输入应用（改后提示需重启代理/会话） | `POST /api/backend` |
| 识别参数 | 温度 / top_p / 上游（白名单键） | `POST /api/config` |
| 状态 | ollama 服务 + 各云端厂商 key 状态 | `GET /api/status` |

面板每 5s 自动轮询刷新。

## 设置

`设置 → 搜索 vision`：

- `vision.port`：覆盖控制 API 端口（默认读 `~/.claude/vision-eyes/config.json`）
- `vision.proxyUrl`：覆盖代理地址（代理不在本机时用，如 `http://192.168.1.5:8787`）

## 架构

```
Webview View（webview/）──postMessage──▶ extension.js（主线程）
                                              │  fetch
                                              ▼
                              代理 /api/*（control_api.py）
                                              │  import toggle + config_loader
                                              ▼
                              config.json / state / ollama
```

webview 因 CSP 不能直连 localhost，统一由主线程 fetch 后 postMessage 回传。

## 已知限制

- 控制 API 走 localhost 信任模型（与 `/health` 一致），未加鉴权
- 改端口只写 config.json，**需重启代理/会话**才生效（后端启动时端口定死）
- `vision.port` 设置会覆盖 config.json 的端口，若二者不一致以设置值为准
