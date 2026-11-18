import { chromium } from 'playwright'
const BASE = 'https://localhost:8443'
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 390, height: 844 } })
const p = await ctx.newPage()
await p.goto(`${BASE}/admin#/login`, { waitUntil: 'networkidle' })
await p.fill('input[autocomplete="username"]', 'admin')
await p.fill('input[autocomplete="current-password"]', 'Sw@2026#Signal')
await p.click('button:has-text("登录")')
await p.waitForURL(/overview/)
await p.waitForTimeout(2000)
const cdp = await ctx.newCDPSession(p)
try {
  const r = await cdp.send('Page.getInstallabilityErrors')
  console.log('installabilityErrors:', JSON.stringify(r))
} catch (e) {
  console.log('CDP 不支持:', e.message)
}
const man = await cdp.send('Page.getAppManifest').catch((e) => ({ errors: [{ message: e.message }] }))
console.log('manifest errors:', JSON.stringify(man.errors || []))
await b.close()
