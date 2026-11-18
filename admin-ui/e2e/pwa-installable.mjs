/**
 * 安装条件自查：在真 Chrome 里打开 https 管理台，验证 PWA 能被安装。
 *
 * 判据（Chromium 就是这么判的）：
 *   1) 页面是安全源（证书受信任的 https）
 *   2) 有 <link rel="manifest">，manifest 里 name/start_url/display/icons(192/512) 齐
 *   3) 注册了 Service Worker 且已 activated
 *   4) 浏览器抛出 beforeinstallprompt —— 这一条为真，菜单里才有「安装」
 *
 * 用法：node e2e/pwa-installable.mjs   （先 certutil 把 certs/ca.crt 加进信任库）
 */
import { chromium } from 'playwright'

const BASE = process.env.SW_BASE || 'https://localhost:8443'
const USER = process.env.SW_USER || 'admin'
const PASS = process.env.SW_PASS || 'Sw@2026#Signal'

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
// 在页面脚本之前挂监听，否则 beforeinstallprompt 可能已经抛完了
await ctx.addInitScript(() => {
  window.__bip = null
  window.addEventListener('beforeinstallprompt', (e) => {
    window.__bip = { platforms: e.platforms || [], at: Date.now() }
  })
})
const page = await ctx.newPage()
const fails = []
const ok = (cond, msg) => { if (!cond) fails.push(msg) }

await page.goto(`${BASE}/admin#/login`, { waitUntil: 'networkidle' })
await page.fill('input[autocomplete="username"]', USER)
await page.fill('input[autocomplete="current-password"]', PASS)
await page.click('button:has-text("登录")')
await page.waitForURL(/overview/, { timeout: 15000 })

// 安全源
const secure = await page.evaluate(() => window.isSecureContext)
ok(secure, '不是安全源（证书没被信任，Chrome 会拒绝注册 SW）')

// manifest
const href = await page.getAttribute('link[rel="manifest"]', 'href').catch(() => null)
ok(!!href, '页面没有 <link rel="manifest">')
const mf = await page.evaluate(async (h) => {
  const r = await fetch(h)
  return { status: r.status, body: await r.json() }
}, href)
ok(mf.status === 200, `manifest 取不到：${mf.status}`)
ok(!!mf.body.name && !!mf.body.start_url, 'manifest 缺 name 或 start_url')
ok(mf.body.display === 'standalone', 'display 不是 standalone')
const sizes = (mf.body.icons || []).map((i) => i.sizes)
ok(sizes.includes('192x192') && sizes.includes('512x512'), `图标尺寸不全：${sizes.join(',')}`)

// Service Worker
await page.waitForTimeout(1500)
const sw = await page.evaluate(async () => {
  const regs = await navigator.serviceWorker.getRegistrations()
  return regs.map((r) => ({ scope: r.scope, active: !!r.active }))
})
ok(sw.length > 0, '没有注册任何 Service Worker')
ok(sw.some((r) => r.active), 'Service Worker 注册了但没有 active')

// Chromium 自己的可安装性判定：这个列表为空才叫「能装」。
// 不用 beforeinstallprompt 当判据——headless Chrome 不会抛它，抛不抛还受「访问次数」影响。
const cdp = await ctx.newCDPSession(page)
const { installabilityErrors } = await cdp.send('Page.getInstallabilityErrors')
ok(installabilityErrors.length === 0,
   'Chromium 判定不可安装：' + installabilityErrors.map((e) => e.errorId + (e.errorArguments ? ':' + e.errorArguments : '')).join(', '))
const bip = await page.evaluate(() => window.__bip)

await browser.close()

console.log(`manifest: ${href} start_url=${mf.body.start_url} icons=${sizes.join('/')}`)
console.log(`service worker: ${JSON.stringify(sw)}`)
console.log(`installabilityErrors: ${JSON.stringify(installabilityErrors)}`)
console.log(`beforeinstallprompt: ${bip ? 'FIRE' : 'MISS（headless 不抛，不影响）'}`)
if (fails.length) {
  console.error('FAIL')
  for (const f of fails) console.error(' -', f)
  process.exit(1)
}
console.log('PASS /admin 可安装：安全源 + manifest 齐全 + SW active + Chromium 无 installabilityErrors')
