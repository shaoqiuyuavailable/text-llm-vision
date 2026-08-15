// dsh-vision apply() 冒烟测试：在真实 cordis Context 上执行插件 apply，
// mock tools/attachments/webServer/settings 服务，验证工具注册 + 钩子不抛错。
// 用法: cd D:\deepseek-harness && node --import tsx/esm scripts/smoke-apply.mjs
import { Context } from '@deepseek-ai/cordis'
import { existsSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

// 双副本兼容：harness 拷贝版优先，回退 F 盘源码。
const CANDIDATES = [
  'D:/deepseek-harness/plugins/dsh-vision/src/index.ts',
  'F:/code of PY/dsh_vision/src/index.ts',
]
const entry = CANDIDATES.find((p) => existsSync(p))
if (entry === undefined) {
  console.error('plugin src/index.ts not found in', CANDIDATES)
  process.exit(1)
}

const registered = []
const ctx = new Context()
ctx.logger = { debug: () => {}, info: () => {}, warn: () => {} }
ctx.provide('tools', {
  register(def) {
    registered.push(def.name)
    return () => {}
  },
})
ctx.provide('attachments', {
  async readImage(ref) {
    return { ref, data: new Uint8Array([1, 2, 3]) }
  },
})
ctx.provide('webServer', {
  register() {
    return () => {}
  },
})
let ns = null
ctx.provide('settings', {
  register(name, _schema, opts) {
    ns = String(name)
    return { get: () => opts?.base ?? {}, watch: () => () => {} }
  },
})

const mod = await import(pathToFileURL(entry).href)
mod.apply(ctx, { level: 'fast', timeoutMs: 5000 })

// installSettingsSection 通过 ctx.inject 异步注册命名空间：等一帧再断言。
await new Promise((resolve) => setTimeout(resolve, 100))

console.log('apply() OK, no throw')
console.log('tools registered:', registered.length, '|', registered.join(', '))
console.log('settings ns:', ns)

const expected = ['describe_image', 'extract_text', 'locate_object', 'compare_images', 'take_screenshot', 'vision_rules']
const missing = expected.filter((n) => !registered.includes(n))
if (missing.length > 0) {
  console.error('MISSING tools:', missing.join(', '))
  process.exit(1)
}
if (ns !== 'dsh-vision') {
  console.error('MISSING settings namespace:', ns)
  process.exit(1)
}
console.log('all 6 tools + settings ns registered OK')
console.log('SMOKE TEST PASSED')
