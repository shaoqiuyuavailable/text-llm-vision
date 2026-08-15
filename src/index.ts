/**
 * dsh-vision: DeepSeek Harness external vision plugin.
 *
 * Two responsibilities:
 *  1. Register model-facing tools that delegate to python/vision_cli.py,
 *     which runs the self-contained Scan/Zoom/Guess engine (python/vision_client.py,
 *     migrated from the sealed visual-ds v2 baseline).
 *  2. Listen to agent/pre-step and replace pasted image blocks with local
 *     vision text before a text-only DeepSeek adapter can reject them.
 *
 * The plugin is intentionally thin: all image understanding stays in Python.
 */
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir, homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync } from 'node:fs'
import type { Context } from '@deepseek-ai/cordis'
import type { PreStepDecision } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { ContentBlock, UserMessage } from '@deepseek-ai/dsh-llm'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { installSettingsSection, settingsNamespace } from '@deepseek-ai/dsh-settings'
import z from '@deepseek-ai/schemastery'

export const name = 'dsh-vision'
export const inject = ['tools', 'attachments']

export interface Config {
  python?: string
  visionDir?: string
  precision?: 'fast' | 'standard' | 'deep'
  timeoutMs?: number
  /** 视觉档位：off 关闭（粘贴图片不识别），fast/standard/deep 映射识别精度。 */
  level?: 'off' | 'fast' | 'standard' | 'deep'
  /** Ollama 服务地址（含 /api/generate）。 */
  ollamaUrl?: string
  /** Ollama 采样温度。 */
  temperature?: number
  /** Ollama 核采样 top_p。 */
  topP?: number
  /** grounding bbox 空间识别开关（deep 档）。 */
  grounding?: boolean
  /** Anthropic 兼容上游端点。 */
  upstream?: string
  /** OpenAI 兼容上游端点。 */
  upstreamOpenai?: string
  /** 云端视觉通道：当前激活厂商名（空 = 纯本地）。 */
  cloudActive?: string
  /** 云端厂商列表（name/model/baseUrl/apiKey；apiKey 走 secrets 脱敏）。 */
  clouds?: CloudProvider[]
  /** 场景路由表：scene[.sub] → 引擎（ocr/vlm/table/gui/code）。 */
  router?: Record<string, string>
}

/** 一个云端视觉厂商（OpenAI 兼容）。 */
export interface CloudProvider {
  /** 厂商名（同时是 <NAME>_API_KEY 环境变量名）。 */
  name: string
  /** 云端视觉模型名。 */
  model?: string
  /** OpenAI 兼容端点 base URL。 */
  baseUrl?: string
  /** API key；走 secrets 脱敏，GUI 不回显。 */
  apiKey?: string
}

/** GUI 可编辑的视觉设置命名空间（Settings → 插件 → dsh-vision 卡片）。 */
export const DSH_VISION_NS = settingsNamespace('dsh-vision')
const CloudProviderSchema = z.object({
  name: z.string().required(),
  model: z.string(),
  baseUrl: z.string(),
  apiKey: z.string().role('secret'),
})
export const DshVisionSettings = z.object({
  level: z.union(['off', 'fast', 'standard', 'deep']).default('standard'),
  precision: z.union(['fast', 'standard', 'deep']).default('standard'),
  timeoutMs: z.number().step(1).min(1000).default(120000),
  model: z.string().default('qwen2.5vl'),
  ollamaUrl: z.string().default('http://localhost:11434/api/generate'),
  temperature: z.number().min(0).max(2).default(0.5),
  topP: z.number().min(0).max(1).default(0.8),
  grounding: z.boolean().default(true),
  upstream: z.string().default('https://api.deepseek.com/anthropic'),
  upstreamOpenai: z.string().default(''),
  cloudActive: z.string().default(''),
  clouds: z.array(CloudProviderSchema).default([]),
  router: z.dict(z.string()).default({}),
})

/** 从组合配置解析档位 → 是否启用识别。 */
function levelEnabled(level: string | undefined): boolean {
  return level !== undefined && level !== 'off'
}

/** 档位优先于 precision：GUI 切档位即可控制识别强度。 */
function effectivePrecision(level: string | undefined, precision: string | undefined): 'fast' | 'standard' | 'deep' {
  if (level === 'fast' || level === 'standard' || level === 'deep') return level
  if (precision === 'fast' || precision === 'standard' || precision === 'deep') return precision
  return 'standard'
}

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const DEFAULT_PYTHON = process.platform === 'win32' ? 'python' : 'python3'
const DEFAULT_CLI = join(ROOT, 'python', 'vision_cli.py')
const IMAGE_EXT: Record<string, string> = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/webp': '.webp',
  'image/gif': '.gif',
}

/** 模块级日志：apply 时绑定 ctx.logger，供 runVision 等模块级函数使用。 */
interface VisionLogger {
  debug(msg: string, ...args: unknown[]): void
  info(msg: string, ...args: unknown[]): void
  warn(msg: string, ...args: unknown[]): void
}
let logger: VisionLogger | undefined

/** 单次子进程 stdout/stderr 捕获上限：防异常输出撑爆内存（超限截断并标记）。 */
const VISION_OUTPUT_CAP = 1_000_000 // 1MB

function runVision(
  python: string,
  cli: string,
  args: string[],
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<string> {
  const started = Date.now()
  const log = logger
  log?.debug('[dsh-vision] spawn %s %s %o', python, cli, args)
  return new Promise((resolve, reject) => {
    const child = spawn(python, [cli, ...args], {
      cwd: ROOT,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    let stdoutCapped = false
    let stderrCapped = false
    let settled = false
    const timer = setTimeout(() => {
      child.kill()
      fail(new Error(`dsh-vision: timeout after ${timeoutMs}ms`))
    }, timeoutMs)
    const onAbort = () => child.kill()
    signal?.addEventListener('abort', onAbort, { once: true })

    child.stdout.on('data', (chunk: Buffer) => {
      if (stdout.length < VISION_OUTPUT_CAP) {
        stdout += chunk.toString('utf8')
        if (stdout.length > VISION_OUTPUT_CAP) {
          stdout = stdout.slice(0, VISION_OUTPUT_CAP)
          stdoutCapped = true
        }
      } else {
        stdoutCapped = true
      }
    })
    child.stderr.on('data', (chunk: Buffer) => {
      if (stderr.length < VISION_OUTPUT_CAP) {
        stderr += chunk.toString('utf8')
        if (stderr.length > VISION_OUTPUT_CAP) {
          stderr = stderr.slice(0, VISION_OUTPUT_CAP)
          stderrCapped = true
        }
      } else {
        stderrCapped = true
      }
    })
    child.on('error', (error) => fail(error))
    child.on('close', (code) => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      const ms = Date.now() - started
      if (code === 0) {
        log?.info('[dsh-vision] vision_cli %s took %dms (out=%d%s)', args[0], ms, stdout.length, stdoutCapped ? '+capped' : '')
        resolve(stdout.trim())
      } else {
        log?.warn('[dsh-vision] vision_cli %s failed code=%d after %dms (err=%d%s)', args[0], code, ms, stderr.length, stderrCapped ? '+capped' : '')
        fail(new Error(stderr.trim() || `dsh-vision: vision_cli exited with code ${code}`))
      }
    })

    function fail(error: Error): void {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      reject(error)
    }
  })
}

/** dsh-vision 专属配置路径（vision_cli.py 的 DSH_CONFIG 优先读取）。 */
const VISION_CONFIG = join(homedir(), '.dsh', 'vision', 'config.json')
/** 档位 state 文件（0-3，与 visual-ds toggle.STATE 同语义）。 */
const VISION_STATE = join(homedir(), '.dsh', 'vision', 'state')
const LEVEL_TO_NUM: Record<string, number> = { off: 0, fast: 1, standard: 2, deep: 3 }

/**
 * 把 GUI 设置同步到 ~/.dsh/vision/config.json + state：
 * vision_cli.py 每次调用都是新进程，读文件即生效，无需重启。
 * 仅覆盖面板可配置的键，保留 scenes/prompts 等引擎内部结构。
 * @param cfg - 当前生效配置（组合配置 + GUI settings 覆盖后的值）。
 */
async function syncVisionConfig(cfg: Config): Promise<void> {
  try {
    await mkdir(join(homedir(), '.dsh', 'vision'), { recursive: true })
    let data: Record<string, unknown> = {}
    if (existsSync(VISION_CONFIG)) {
      try {
        const parsed = JSON.parse(await readFile(VISION_CONFIG, 'utf8'))
        if (parsed !== null && typeof parsed === 'object') data = parsed
      } catch {
        // 损坏的 config.json 直接重建
      }
    }
    const ollama = { ...(typeof data.ollama === 'object' && data.ollama !== null ? data.ollama : {}) } as Record<string, unknown>
    if (cfg.model !== undefined) ollama.model = cfg.model
    if (cfg.ollamaUrl !== undefined) ollama.url = cfg.ollamaUrl
    if (cfg.temperature !== undefined) ollama.temperature = cfg.temperature
    if (cfg.topP !== undefined) ollama.top_p = cfg.topP
    if (cfg.grounding !== undefined) ollama.grounding = cfg.grounding
    if (cfg.precision !== undefined) ollama.precision = cfg.precision
    data.ollama = ollama
    if (cfg.upstream !== undefined) data.upstream = cfg.upstream
    if (cfg.upstreamOpenai !== undefined) data.upstream_openai = cfg.upstreamOpenai
    // 云端通道：clouds（厂商列表）与 cloud.active（当前激活）同步到 config.json。
    // 键名转换：GUI 的 baseUrl/apiKey → 引擎的 base_url/api_key。
    if (cfg.clouds !== undefined) {
      const clouds = cfg.clouds.map(c => ({
        name: c.name,
        ...c.model === undefined ? {} : { model: c.model },
        ...c.baseUrl === undefined ? {} : { base_url: c.baseUrl },
        ...c.apiKey === undefined ? {} : { api_key: c.apiKey },
      }))
      data.cloud = { ...(typeof data.cloud === 'object' && data.cloud !== null ? data.cloud : {}), clouds }
    }
    if (cfg.cloudActive !== undefined) {
      data.cloud = { ...(typeof data.cloud === 'object' && data.cloud !== null ? data.cloud : {}), active: cfg.cloudActive }
    }
    // 场景路由表：GUI 的 router 覆盖（scene[.sub] → 引擎）。
    if (cfg.router !== undefined && Object.keys(cfg.router).length > 0) {
      data.router = cfg.router
    }
    await writeFile(VISION_CONFIG, JSON.stringify(data, null, 2) + '\n', 'utf8')
    if (cfg.level !== undefined) {
      const num = LEVEL_TO_NUM[cfg.level] ?? 2
      await writeFile(VISION_STATE, String(num), 'utf8')
      // off 档：主动卸载 Ollama 视觉模型释放显存（与 visual-ds toggle._unload_model 同语义）。
      if (num === 0) {
        try {
          const model = (cfg.model || (ollama.model as string | undefined) || 'qwen2.5vl')
          const stopped = spawn('ollama', ['stop', model], { windowsHide: true, stdio: 'ignore' })
          stopped.unref()
        } catch {
          // 卸载失败不阻断：模型会随 keep_alive 超时自动释放。
        }
      }
    }
  } catch (error) {
    // 同步失败不阻断插件运行：识别时 vision_cli 会回退到插件自带 config.json。
    logger?.warn('[dsh-vision] sync vision config failed: %o', error)
  }
}

/**
 * 同图去重缓存（进程内 LRU，不落盘）。
 *
 * 每次识别都会 spawn 新 Python 进程，引擎自带的内存缓存随进程销毁——
 * 同一张图在当前 dsh 会话重复粘贴/重试会重复识别（云端即重复计费）。
 * 这里按图片字节 sha256 缓存识别文本，命中直接返回，零识别、零计费。
 * 缓存随 dsh 进程生命周期，重启即清空；上限防内存膨胀（FIFO 淘汰）。
 */
const VISION_CACHE_MAX = 64
const visionCache = new Map<string, { text: string; precision: string }>()

/** Ollama 模型列表缓存（15s TTL，供 GUI 模型下拉）。 */
let ollamaModelsCache: { ts: number; models: string[] } = { ts: 0, models: [] }
const OLLAMA_MODELS_TTL = 15_000

/** 探测 Ollama /api/tags 获取已安装模型名列表；失败返回空数组（GUI 回退文本框）。 */
async function fetchOllamaModels(url: string | undefined): Promise<string[]> {
  const base = (url ?? 'http://localhost:11434/api/generate').split('/api/')[0]
  if (Date.now() - ollamaModelsCache.ts < OLLAMA_MODELS_TTL) return ollamaModelsCache.models
  try {
    const res = await fetch(`${base}/api/tags`, { signal: AbortSignal.timeout(3000) })
    if (!res.ok) return []
    const data = await res.json() as { models?: Array<{ name?: string }> }
    const models = (data.models ?? []).map(m => m.name ?? '').filter(Boolean)
    ollamaModelsCache = { ts: Date.now(), models }
    return models
  } catch {
    return []
  }
}

/** 计算图片字节的 sha256（缓存 key，内容寻址天然安全）。 */
function imageDigest(data: Uint8Array): string {
  return createHash('sha256').update(data).digest('hex')
}

/** 查缓存：命中返回识别文本，未命中返回 undefined。精度变化视为不同结果。 */
function cacheGet(digest: string, precision: string): string | undefined {
  const hit = visionCache.get(digest)
  return hit !== undefined && hit.precision === precision ? hit.text : undefined
}

/** 写缓存：FIFO 淘汰最旧条目。 */
function cacheSet(digest: string, precision: string, text: string): void {
  visionCache.delete(digest)
  visionCache.set(digest, { text, precision })
  while (visionCache.size > VISION_CACHE_MAX) {
    const oldest = visionCache.keys().next().value
    if (oldest === undefined) break
    visionCache.delete(oldest)
  }
}

function rulesText(): string {
  return [
    '你的模型没有视觉能力。出现以下情况必须调用相应工具：',
    '- 用户引用本地图片路径 / 粘贴截图 / 你看到图片占位符 → describe_image(图片路径)',
    '- 终端红字、报错栈、文档扫描 → extract_text(图片路径)',
    '- 图中某元素在哪里 → locate_object(图片路径, 元素名)',
    '- 前后两张图对比 → compare_images(图A路径, 图B路径)',
    '- 用户问"当前屏幕/界面是什么样"或需要看实时画面 → take_screenshot(identify: true)',
  ].join('\n')
}

export function apply(ctx: Context, config: Config = {}): void {
  logger = ctx.logger
  const python = config.python || DEFAULT_PYTHON
  const cli = config.visionDir ? join(config.visionDir, 'python', 'vision_cli.py') : DEFAULT_CLI

  // 运行时配置：组合配置为基底，GUI（Settings → 插件 → dsh-vision）通过
  // settings 命名空间覆盖；setSource 后 current() 返回最新值。
  // 每次变更同步到 ~/.dsh/vision/config.json + state，vision_cli 读文件即生效。
  let current: () => Config = () => config
  installSettingsSection(ctx, DSH_VISION_NS, DshVisionSettings, config, {
    setSource: (source) => {
      current = source
      void syncVisionConfig(source())
    },
    onChange: () => {
      void syncVisionConfig(current())
    },
  })
  const cfg = (): Config => current()

  // Ollama 模型列表路由：GUI 模型下拉的数据源（webServer 可选注入，缺失时 GUI 回退文本框）。
  ctx.inject(['webServer'], (wctx) => {
    wctx.effect(() => wctx.webServer.register({
      kind: 'prefix',
      path: '/dsh-vision/ollama-models',
      handler: async (_req, res) => {
        const models = await fetchOllamaModels(cfg().ollamaUrl)
        res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-cache' })
        res.end(JSON.stringify({ models }))
      },
    }), 'dsh-vision: ollama models route')
  })

  // ---------- 1. 模型主动看图工具 ----------
  ctx.tools.register(defineTool({
    name: 'describe_image',
    description: '识别本地图片，用本地/云端视觉模型返回图片内容的文字描述（Scan→Zoom→Guess 三阶段）。当需要查看图片内容、分析截图、识别图片中的文字/物体/场景时使用。',
    parameters: {
      image: { type: 'string', required: true, description: '图片文件本地绝对路径' },
      prompt: { type: 'string', description: '可选的识图指令，指定要关注的内容' },
      mode: { type: 'string', description: '可选的识别模式：rigorous/identity/military/anime/open' },
      precision: { type: 'string', enum: ['fast', 'standard', 'deep'], description: '覆盖默认识别精度' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const precision = effectivePrecision(cfg().level, args.precision ?? cfg().precision)
      // 默认调用（无 prompt/mode）走同图去重缓存；定制化识别不缓存（结果随指令变化）。
      if (args.prompt === undefined && args.mode === undefined) {
        try {
          const data = await readFile(args.image)
          const digest = imageDigest(data)
          const cached = cacheGet(digest, precision)
          if (cached !== undefined) {
            ctx.logger.info('[dsh-vision] describe_image cache hit (%s)', digest.slice(0, 12))
            return cached
          }
          const text = await runVision(
            python,
            cli,
            ['describe', args.image, '--precision', precision],
            cfg().timeoutMs || 120000,
            exec.signal,
          )
          cacheSet(digest, precision, text)
          return text
        } catch {
          // 文件读取失败（不存在/不可读）：交给 vision_cli 报错（与无缓存行为一致）。
        }
      }
      const cliArgs = ['describe', args.image, '--precision', precision]
      if (args.prompt) cliArgs.push('--prompt', args.prompt)
      if (args.mode) cliArgs.push('--mode', args.mode)
      return runVision(python, cli, cliArgs, cfg().timeoutMs || 120000, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'extract_text',
    description: '提取图片中的全部文字（OCR 优先，回退视觉模型）。用于截图报错、终端红字、文档扫描等纯文字场景。',
    parameters: {
      image: { type: 'string', required: true, description: '图片文件本地绝对路径' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      return runVision(python, cli, ['extract', args.image], cfg().timeoutMs || 120000, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'locate_object',
    description: '在图中定位指定元素，返回元素名与边界框坐标（grounding bbox JSON）。用于“图中某元素在哪”“点击哪个按钮”等需要坐标的场景。',
    parameters: {
      image: { type: 'string', required: true, description: '图片文件本地绝对路径' },
      query: { type: 'string', required: true, description: '要定位的元素，如“提交按钮”“错误提示框”' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      return runVision(python, cli, ['locate', args.image, args.query], cfg().timeoutMs || 120000, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'compare_images',
    description: '对比两张图片（各自识别后逐点对比差异）。用于 UI 前后对比、图找不同。',
    parameters: {
      image_a: { type: 'string', required: true, description: '图A本地绝对路径' },
      image_b: { type: 'string', required: true, description: '图B本地绝对路径' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const precision = effectivePrecision(cfg().level, cfg().precision)
      return runVision(python, cli, ['compare', args.image_a, args.image_b, '--precision', precision], cfg().timeoutMs || 120000, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'take_screenshot',
    description: '截取当前屏幕（全屏或指定区域）保存为 PNG 并返回路径；identify=true 时立即用视觉引擎识别并返回识别文本。用于“看看当前屏幕/界面现在是什么样”。',
    parameters: {
      region: { type: 'string', description: '截取区域 x,y,w,h（像素）；省略 = 全屏' },
      identify: { type: 'boolean', description: 'true 时截屏后立即识别；默认 false 只返回路径' },
      precision: { type: 'string', enum: ['fast', 'standard', 'deep'], description: 'identify 时的识别精度（默认 standard）' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const cliArgs = ['screenshot']
      if (args.region) cliArgs.push('--region', args.region)
      if (args.identify) {
        cliArgs.push('--identify')
        if (args.precision) cliArgs.push('--precision', args.precision)
      }
      return runVision(python, cli, cliArgs, cfg().timeoutMs || 120000, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'vision_rules',
    description: '返回“何时该调用识图工具”的规则文本。当规则文件缺失、不确定何时该调用识图工具时调用本工具。',
    parameters: {},
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute() {
      return rulesText()
    },
  }))

  // ---------- 2. 粘贴图片兜底：agent/pre-step 改写 image block ----------
  async function replaceImages(
    blocks: ContentBlock[],
    signal?: AbortSignal,
  ): Promise<ContentBlock[] | null> {
    let changed = false
    const out: ContentBlock[] = []
    // 识别文本单独收集，最后追加到用户原有文本之后：保持"用户的话在前、
    // 图片描述在后"的顺序，避免长识别结果把用户输入挤到后面显得被吞。
    const converted: ContentBlock[] = []
    for (const block of blocks) {
      if (block.type !== 'image') {
        out.push(block)
        continue
      }
      changed = true
      const ext = IMAGE_EXT[block.attachment.mediaType] || '.png'
      // 附件引用随识别文本一起持久化：模型只读 text 字段（照常收到识别文本），
      // GUI 渲染时用 dshAttachment 显示原图缩略图（用户侧历史回溯）。
      const attachment = block.attachment
      // 识别起始时间：用于长识别耗时提示（进度反馈的轻量形态）。
      const startedAt = Date.now()
      let tmpDir: string | undefined
      try {
        // 档位 off：不调本地识别，直接占位（原图附件仍保留给 GUI）。
        if (!levelEnabled(cfg().level)) {
          converted.push({
            type: 'text',
            text: '[图片（视觉档位 off，未识别）]',
            dshVision: true,
            dshAttachment: attachment,
          } as ContentBlock)
          continue
        }
        const stored = await ctx.attachments.readImage(block.attachment, signal)
        ctx.logger.info('[dsh-vision] intercepting pasted image: %s (%d bytes)', block.attachment.mediaType, block.attachment.bytes)
        // 同图去重：同一字节内容在进程内只识别一次（防重复计费/耗时）。
        const precision = effectivePrecision(cfg().level, cfg().precision)
        const digest = imageDigest(stored.data)
        const cached = cacheGet(digest, precision)
        if (cached !== undefined) {
          ctx.logger.info('[dsh-vision] paste image cache hit (%s)', digest.slice(0, 12))
          converted.push({
            type: 'text',
            text: `[用户粘贴图片，已由本地视觉识别]\n${cached}`,
            dshVision: true,
            dshAttachment: attachment,
          } as ContentBlock)
          continue
        }
        tmpDir = await mkdtemp(join(tmpdir(), 'dsh-vision-'))
        const file = join(tmpDir, `pasted${ext}`)
        await writeFile(file, Buffer.from(stored.data))
        // 识别 + 失败降档重试：deep 失败 → standard 重试一次（快速失败不重试，
        // 避免重复计费/耗时；识别失败无计费风险，降档是白赚的鲁棒性）。
        let text: string
        try {
          text = await runVision(
            python,
            cli,
            ['describe', file, '--precision', precision],
            cfg().timeoutMs || 120000,
            signal,
          )
        } catch (error) {
          if (precision !== 'standard' && precision !== 'fast') {
            ctx.logger.warn('[dsh-vision] %s recognition failed, retrying standard: %o', precision, error)
            text = await runVision(
              python,
              cli,
              ['describe', file, '--precision', 'standard'],
              cfg().timeoutMs || 120000,
              signal,
            )
          } else {
            throw error
          }
        }
        cacheSet(digest, precision, text)
        const elapsedMs = Date.now() - startedAt
        // 识别进度反馈（轻量）：长识别（>5s）在文本头部标注耗时，让用户感知
        // 识别确实发生了且耗时来源（而非"卡住"）。
        const durationNote = elapsedMs > 5000 ? `（识别耗时 ${Math.round(elapsedMs / 1000)}s）` : ''
        converted.push({
          type: 'text',
          text: `[用户粘贴图片，已由本地视觉识别${durationNote}]\n${text}`,
          // 标记为视觉识别产物：模型照常收到，但 GUI 渲染跳过（用户消息块
          // 只显示用户自己的话，不显示识别文本）；dshAttachment 供 GUI 读回原图。
          dshVision: true,
          dshAttachment: attachment,
        } as ContentBlock)
        ctx.logger.info('[dsh-vision] pasted image converted, result chars=%d', text.length)
      } catch (error) {
        ctx.logger.warn('[dsh-vision] paste image conversion failed: %o', error)
        converted.push({
          type: 'text',
          text: '[图片（本地视觉识别失败，已省略）]',
          dshVision: true,
          dshAttachment: attachment,
        } as ContentBlock)
      } finally {
        if (tmpDir) await rm(tmpDir, { recursive: true, force: true })
      }
    }
    if (!changed) return null
    return [...out, ...converted]
  }

  ctx.on('agent/pre-step', async (
    { signal },
    next,
  ): Promise<PreStepDecision> => {
    const decision = await next()
    if (decision.kind === 'reject') return decision
    let changed = false
    const rewritten: UserMessage[] = []
    for (const message of decision.messages) {
      const content = await replaceImages(message.content, signal)
      if (content === null) {
        rewritten.push(message)
      } else {
        changed = true
        rewritten.push(createUserMessage({ content, source: message.source }))
      }
    }
    return changed ? { kind: 'enter', messages: rewritten } : decision
  })
}
