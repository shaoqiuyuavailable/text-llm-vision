# text-llm-vision 可视化插件

在 VS Code 侧边栏动态展示并修改 [text-llm-vision](../README.md) 的配置——**MCP 主路径状态**、档位、后端、端口、温度/top_p、上游、云端厂商。**复用后端控制 API（`/api/*`），MCP 状态直接读 `~/.claude.json`，不重写任何识别逻辑。**

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

侧边栏活动栏点 **Vision** 图标 → **树视图面板**（TreeView），节点实时显示状态，**点击节点弹选择器/输入框修改**，每 5s 自动刷新：

| 树节点 | 点击操作 | 底层调用 |
|------|------|---------|
| **MCP: mcp_server.py ✓ / 旧 node** | 旧 node/未注册时点击迁移 | 读 `~/.claude.json` + `python install.py --mcp claude` |
| **工具: describe_image · …** | 只读（5 工具列表） | 读 `~/.claude.json` |
| 档位: fast (1) | 选 off/fast/standard/deep（off 自动 `ollama stop`） | `POST /api/level` |
| 后端: local | 选本地/云端 + 厂商 | `POST /api/backend` |
| 端口: 8787 | 输入端口（改后提示需重启代理/会话） | `POST /api/backend` |
| 温度 / top_p | 输入数值 | `POST /api/config` |
| 上游: … | 输入地址 | `POST /api/config` |
| 云端厂商 | 点厂商切换 | `POST /api/backend` |
| 代理 / ollama | 只读状态 | `GET /api/status` |

代理未运行时显示「⚠ 代理未运行」+「▶ 启动代理」节点（spawn 启动 `start_proxy.py`，不弹 cmd 窗口）。

## 设置

`设置 → 搜索 vision`：

- `vision.port`：覆盖控制 API 端口（默认读 `~/.claude/vision-eyes/config.json`）
- `vision.proxyUrl`：覆盖代理地址（代理不在本机时用，如 `http://192.168.1.5:8787`）

## 架构

```
TreeView（registerTreeDataProvider）──▶ extension.js（主线程）
                                            │  ├─ fetch ──▶ 代理 /api/*（control_api.py → toggle/config_loader → config.json / state / ollama）
                                            │  └─ 读文件 ─▶ ~/.claude.json（MCP 注册：mcp_server.py vs mcp-vision.js）
```

## 为何用 TreeView 而非 WebviewView

本环境（Claude Code for VS Code）下 WebviewView 的 `resolveWebviewView` **不触发**——provider 注册成功、扩展正常激活，但视图内容不渲染（报「没有可提供视图数据的已注册数据提供程序」）。TreeView 走 `registerTreeDataProvider`（`createTreeView`），机制完全不同，稳定可靠。

## 已知限制

- 控制 API 走 localhost 信任模型（与 `/health` 一致），未加鉴权
- 改端口只写 config.json，**需重启代理/会话**才生效（后端启动时端口定死）
- `vision.port` 设置会覆盖 config.json 的端口，若二者不一致以设置值为准
