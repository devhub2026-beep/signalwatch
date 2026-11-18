/**
 * 手机竖屏（390×844）验收：侧栏必须收成抽屉，内容区不能被挤成一条缝。
 *
 * 之前的 bug 是 el-aside 固定 200px，在 390px 宽的手机上只剩 190px 放内容，
 * 表格直接撑爆。这里把三件事钉死：侧栏不存在、汉堡按钮存在、内容区宽度够。
 */
import { chromium } from 'playwright'

const BASE = process.env.SW_BASE || 'http://127.0.0.1:8766'
const USER = process.env.SW_USER || 'admin'
const PASS = process.env.SW_PASS || 'Sw@2026#Signal'

const browser = await chromium.launch()
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
})
const page = await ctx.newPage()

const fails = []
const ok = (cond, msg) => { if (!cond) fails.push(msg) }

await page.goto(`${BASE}/admin#/login`, { waitUntil: 'networkidle' })

// 登录
await page.fill('input[autocomplete="username"]', USER)
await page.fill('input[autocomplete="current-password"]', PASS)
await page.click('button:has-text("登录")')
await page.waitForURL(/overview/, { timeout: 15000 })
await page.waitForTimeout(800)

// 1) 侧栏不该在窄屏出现
const aside = await page.locator('aside.el-aside').count()
ok(aside === 0, `窄屏不该有固定侧栏，实际 ${aside} 个`)

// 2) 汉堡按钮要在
const burger = page.locator('.burger')
ok(await burger.count() === 1, '窄屏缺少汉堡按钮')

// 3) 内容区宽度（不能只剩一条缝）
const mainBox = await page.locator('main.el-main').boundingBox()
ok(mainBox && mainBox.width > 340, `内容区宽度 ${mainBox && Math.round(mainBox.width)}px，应该接近整屏`)

// 4) 点开抽屉，菜单项可见
await burger.click()
await page.waitForTimeout(500)
const drawerItems = await page.locator('.el-drawer .el-menu-item').count()
ok(drawerItems >= 13, `抽屉里菜单项 ${drawerItems} 个，应该 13 个`)
await page.screenshot({ path: 'D:/signalwatch/docs/shot-mobile-drawer.png' })

// 5) 点一个菜单，抽屉自动关，页面切走
await page.locator('.el-drawer .el-menu-item', { hasText: '条目' }).click()
await page.waitForTimeout(800)
ok(/items/.test(page.url()), '点「条目」没跳转')
const drawerOpen = await page.locator('.el-drawer').isVisible().catch(() => false)
ok(!drawerOpen, '点完菜单抽屉应该自己关上')

// 窄屏该是卡片不是表格；卡片里标题必须看得见
await page.waitForFunction(
  () => document.querySelectorAll('.card').length > 0,
  null, { timeout: 15000 },
).catch(() => {})
const cards = await page.locator('.card').count()
ok(cards > 0, `手机条目页没渲染卡片（cards=${cards}）`)
ok(await page.locator('.el-table').count() === 0, '窄屏不该渲染表格')
const firstTitle = await page.locator('.card-title').first().innerText().catch(() => '')
ok(firstTitle.length > 6, `卡片标题为空或太短：${firstTitle}`)
const totalText = await page.locator('body').innerText()
const m = totalText.match(/共 (\d+) 条/)
ok(m && Number(m[1]) > 0, '条目总数为 0，接口没返回数据')

await page.screenshot({ path: 'D:/signalwatch/docs/shot-mobile-items.png' })

// 6) 横屏（844×390）也不该崩
await page.setViewportSize({ width: 844, height: 390 })
await page.waitForTimeout(500)
const asideWide = await page.locator('aside.el-aside').count()
ok(asideWide === 1, '横屏（844px）应该回到固定侧栏')

await browser.close()

if (fails.length) {
  console.error('FAIL')
  for (const f of fails) console.error(' -', f)
  process.exit(1)
}
console.log('PASS 手机竖屏：无固定侧栏 / 有汉堡 / 内容区满宽 / 抽屉 13 项 / 点选后自动关 / 横屏恢复侧栏')
