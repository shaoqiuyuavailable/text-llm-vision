// dsh-vision apply() 冒烟测试：在真实 cordis Context 上执行插件 apply，
// mock tools/attachments 服务，验证 5 个工具注册 + agent/pre-step 钩子不抛错。
// 用法: cd D:\deepseek-harness && node scripts/smoke-apply.mjs
import { Context } from '@deepseek-ai/cordis'

const registered = []
const ctx = new Context()
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

const mod = await import('file:///F:/code of PY/dsh_vision/src/index.ts')
mod.apply(ctx, { visionDir: 'F:/code of PY/dsh_vision', precision: 'fast', timeoutMs: 5000 })

console.log('apply() OK, no throw')
console.log('tools registered:', registered.join(', '))

const expected = ['describe_image', 'extract_text', 'locate_object', 'compare_images', 'vision_rules']
const missing = expected.filter((n) => !registered.includes(n))
if (missing.length > 0) {
  console.error('MISSING tools:', missing.join(', '))
  process.exit(1)
}
console.log('all 5 tools registered OK')
console.log('SMOKE TEST PASSED')
