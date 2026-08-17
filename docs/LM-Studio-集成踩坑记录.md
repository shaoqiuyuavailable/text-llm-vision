# LM Studio 集成踩坑记录（dsh-vision-router merge 版）

> 记录时间：2026-08-16 ｜ 环境：Windows 11 + RTX 4060 Laptop 8GB + Node 22
> 结论先行：**LM Studio 全链路已打通**（OpenAI + Anthropic 双格式识别测试通过）。

## 1. 安装

| 坑 | 说明 | 解法 |
|---|---|---|
| winget 安装无输出卡死 | `winget install ElementLabs.LMStudio --silent` 后台跑了 5 分钟无输出 | 改用 `winget download` 下载安装包（605MB，nullsoft 安装器）→ 手动 `/S` 静默安装 |
| lms CLI 位置 | 不在安装根目录，在 `resources\app\.webpack\lms.exe` | 用完整路径调用 |
| lms 找不到 daemon | CLI 报 "daemon is not running" | 先启动 GUI（`LM Studio.exe`）拉起 daemon，再 `lms server start` |

## 2. 模型下载

| 坑 | 说明 | 解法 |
|---|---|---|
| `lms get qwen2.5-vl-7b-instruct` 找不到 | staff picks 里没有 | 改用完整仓库名 `lmstudio-community/Qwen2.5-VL-7B-Instruct-GGUF` |
| `lms get` 报 artifact 不存在 | HF 解析失败 | 用完整 URL 也不行（fetch failed）→ 放弃 lms，用 Node fetch 直下 |
| HF 直连断流 | huggingface.co 大文件下载中断 | **hf-mirror.com 镜像** |
| Node fetch 不走系统代理 | 本机 Clash 127.0.0.1:7897，fetch 直连失败 | undici `ProxyAgent` + `setGlobalDispatcher` |
| 断点续传 | 下载中断后重下 | `Range: bytes=N-` + append 模式写入 |

## 3. Ollama 模型迁移尝试（失败，放弃）—— 关键认知

Ollama 的模型是**融合版 GGUF**（语言 + vision 塔合并单文件，858 tensor），
llama.cpp / LM Studio 标准是**分离版**（语言 GGUF 339 tensor + 独立 mmproj 519 tensor）。
直接迁移 Ollama 模型给 LM Studio 不可行（除非重写融合逻辑）。

迁移过程踩的坑（如果以后要写 Ollama→标准 GGUF 转换工具）：

1. **架构名**：Ollama 用 `qwen25vl`，llama.cpp 认 `qwen2vl`（GGUF metadata `general.architecture` + 全部 `qwen25vl.*` key 前缀要改）
2. **字符串长度前缀**：GGUF v3 的字符串长度是 **u64**（8 字节），不是 u32（解析错位）
3. **mrope_section**：Ollama 叫 `qwen2vl.rope.mrope_section`（i32 数组 3 段），llama.cpp 要 `qwen2vl.rope.dimension_sections` **4 段**（[16,24,24] → [16,24,24,24]）
4. **tensor 命名**：Ollama `v.blk.N.attn_out.*`，llama.cpp 要 `v.blk.N.attn_output.*`
5. **tensor offset**：相对数据段偏移（第一个 tensor = 0），llama.cpp 校验 `token_embd.weight offset == 0`
6. 改了以上仍失败（llama.cpp 加载 vision 时 339/858 错误）→ 根因是融合/分离结构差异，**放弃**

## 4. mmproj 配对（最大坑）

- **Qwen2.5-VL 的 GGUF 是「语言 + 独立 mmproj」结构**：`Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf`（339 tensor，纯语言）+ `mmproj-*.gguf`（519 tensor，vision 塔）
- lmstudio-community / unsloth / ggml-org 都是这个结构；Ollama 才是融合版
- **LM Studio 自动配对规则**：mmproj 文件名必须 = `mmproj-` + 语言模型文件名（如 `mmproj-Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf`），放同一目录
- 配对成功标志：`lms ls` 显示模型 SIZE = 语言 + mmproj（4.68GB → 6.04GB），`lms ps` 加载后同样 6.04GB
- 配对失败表现：模型能加载（4.68GB）但推理图片报 500 `image input is not supported - hint: you may need to provide the mmproj`

## 5. lms CLI 行为

| 现象 | 说明 |
|---|---|
| `lms load` 超时 | 加载完成但命令不返回（进程实际已加载，`lms ps` 看状态） |
| 加载不带 mmproj | mmproj 命名不对时 lms load 只加载语言部分；命名正确后自动带上 |
| `lms ps` 是权威状态 | STATUS / SIZE 看是否真的带上了 mmproj |

## 6. 最终测试结果（真实 LM Studio 0.4.21 + Qwen2.5-VL-7B）

| 格式 | 端点 | 耗时 | 结果 |
|---|---|---|---|
| openai | /v1/chat/completions | 3.4s | 正确识别测试图文字 + 元素 ✅ |
| anthropic | /v1/messages | 3.4s | 同 ✅ |

## 7. 相关文件与命令速查

```powershell
$lms = "$env:LOCALAPPDATA\Programs\LM Studio\resources\app\.webpack\lms.exe"
& $lms server start        # 启动 headless server (1234)
& $lms ls                  # 模型列表（SIZE 含 mmproj = 配对成功）
& $lms load <key> -y       # 加载（命名正确自动带 mmproj）
& $lms ps                  # 加载状态
```

模型目录：`%USERPROFILE%\.lmstudio\models\lmstudio-community\Qwen2.5-VL-7B-Instruct-GGUF\`
（语言 4.36GB + mmproj 1.26GB，共 5.62GB）
