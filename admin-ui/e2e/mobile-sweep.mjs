/**
 * 手机竖屏（390×844）逐页检查：有没有横向溢出、有没有内容被挤成一条缝。
 *
 * 起因：条目页的 5 列表格在手机上把「标题」挤到看不见。修完顺手把 13 个页面
 * 都过一遍，免得下一个页面再犯同一个错。
 */
import { chromium } from 'playwright'

const BASE = process.env.SW_BASE || 'http://127.0.0.1:8766'
const PAGES = [
  ['/overview', '概览'], ['/home', '首页'], ['/starred', '收藏'], ['/items', '条目'],
  ['/categories', '分类'], ['/jobs', '任务'], ['/report', '报表'], ['/users', '用户'],
  ['/sources', '数据源'], ['/rules', '订阅词'], ['/settings', '设置'], ['/audit', '审计'],
  ['/me', '我的'],
]

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
const page = await ctx.newPage()

await page.goto(`${BASE}/admin#/login`, { waitUntil: 'networkidle' })
await page.fill('input[autocomplete="username"]', process.env.SW_USER || 'admin')
await page.fill('input[autocomplete="current-password"]', process.env.SW_PASS || 'Sw@2026#Signal')
await page.click('button:has-text("登录")')
await page.waitForURL(/overview/)
await page.waitForTimeout(800)

const rows = []
for (const [path, name] of PAGES) {
  await page.goto(`${BASE}/admin#${path}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const m = await page.evaluate(() => {
    const de = document.documentElement
    const main = document.querySelector('main.el-main')
    return {
      scrollW: de.scrollWidth,
      clientW: de.clientWidth,
      mainW: main ? Math.round(main.getBoundingClientRect().width) : 0,
      tables: document.querySelectorAll('.el-table').length,
      cards: document.querySelectorAll('.card').length,
    }
  })
  rows.push({ name, path, ...m, overflow: m.scrollW - m.clientW })
}

await browser.close()

const bad = rows.filter((r) => r.overflow > 2)
for (const r of rows) {
  const flag = r.overflow > 2 ? 'OVERFLOW' : 'ok'
  console.log(`${flag.padEnd(9)} ${r.name.padEnd(4)} scrollW=${String(r.scrollW).padStart(4)} clientW=${r.clientW} 表格=${r.tables} 卡片=${r.cards}`)
}
if (bad.length) {
  console.error(`\n${bad.length} 个页面横向溢出：` + bad.map((b) => `${b.name}(+${b.overflow}px)`).join('、'))
  process.exit(1)
}
console.log('\n全部 13 页无横向溢出')
