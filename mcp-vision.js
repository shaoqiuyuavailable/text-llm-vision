#!/usr/bin/env node
// MCP server：describe_image 工具，供模型主动识图（参考 glm-vision 的 MCP server 机制）。
// 模型需要看图时调用 describe_image(路径)，本工具调本地代理的 /identify 接口，
// 由本地 Qwen2.5-VL（Ollama）识别后返回文字描述。完全独立于 hook，VS Code 扩展可用。
//
// 无外部依赖（Node ≥ 18 内置 fetch），stdin/stdout 走 MCP 协议。
// 注册：claude mcp add vision -e VISION_IDENTIFY_URL=http://127.0.0.1:8787 -- node "绝对路径/mcp-vision.js"
// 注意：若用 `vision local <N>` 改了代理端口，需同步 VISION_IDENTIFY_URL 的端口（`vision port` 子命令已并入 local）。

const IDENTIFY_URL = process.env.VISION_IDENTIFY_URL || "http://127.0.0.1:8787/identify";

// 识别：调用本地 /identify，返回图片文字描述
async function describeImage(path, prompt) {
  const resp = await fetch(IDENTIFY_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, prompt: prompt || "" }),
    signal: AbortSignal.timeout(180000),
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`identify failed: ${resp.status} ${body.slice(0, 200)}`);
  }
  const data = await resp.json();
  return data.desc || data.error || "识别失败";
}

// ---- MCP JSON-RPC 处理 ----
let pending = 0; // 在途请求计数（async 识别未完成时不让进程退出）

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function maybeExit() {
  if (pending === 0) process.exit(0);
}

async function handle(msg) {
  const { id, method } = msg;
  switch (method) {
    case "initialize":
      send({
        jsonrpc: "2.0",
        id,
        result: {
          protocolVersion: "2024-11-05",
          capabilities: { tools: {} },
          serverInfo: { name: "vision-mcp", version: "1.0.0" },
        },
      });
      break;
    case "notifications/initialized":
      break;
    case "tools/list":
      send({
        jsonrpc: "2.0",
        id,
        result: {
          tools: [
            {
              name: "describe_image",
              description:
                "识别本地图片文件，用本地视觉模型返回图片内容的文字描述。当需要查看图片内容、分析截图、识别图片中的文字/物体/场景时使用。传入图片文件的本地绝对路径。",
              inputSchema: {
                type: "object",
                properties: {
                  image: {
                    type: "string",
                    description: "图片文件的本地绝对路径（如 D:/xxx/photo.jpg）",
                  },
                  prompt: {
                    type: "string",
                    description: "可选的识图指令，指定要关注的内容；缺省时描述图片整体",
                  },
                },
                required: ["image"],
              },
            },
          ],
        },
      });
      break;
    case "tools/call":
      pending++;
      (async () => {
        try {
          const args = msg.params?.arguments || {};
          const path = args.image || args.path || "";
          if (!path) throw new Error("缺少 image 参数（图片路径）");
          const desc = await describeImage(path, args.prompt);
          send({
            jsonrpc: "2.0",
            id,
            result: { content: [{ type: "text", text: desc }], isError: false },
          });
        } catch (e) {
          send({
            jsonrpc: "2.0",
            id,
            result: { content: [{ type: "text", text: "识别失败: " + e.message }], isError: true },
          });
        } finally {
          pending--;
          maybeExit();
        }
      })();
      break;
    case "ping":
      send({ jsonrpc: "2.0", id, result: {} });
      break;
    default:
      if (id !== undefined) send({ jsonrpc: "2.0", id, error: { code: -32601, message: "method not found: " + method } });
  }
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx);
    buf = buf.slice(idx + 1);
    if (!line.trim()) continue;
    try {
      handle(JSON.parse(line));
    } catch (e) {
      // 忽略解析错误
    }
  }
});
process.stdin.on("end", () => maybeExit());
