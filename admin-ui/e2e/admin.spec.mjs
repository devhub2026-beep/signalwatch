import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8766';
const USER = 'admin';
const PASS = 'SW-BtBbQ7ZFiqnH0Qy8';

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'PASS' : 'FAIL'}: ${name}`);
  if (!cond) failures++;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().includes('Failed to load resource')) {
      errors.push('console: ' + m.text());
    }
  });
  page.on('response', (r) => {
    const u = r.url();
    // 登录前 /api/auth/me 返回 401 是预期的未认证检查，不计为错误
    if (r.status() === 401 && u.includes('/api/auth/me')) return;
    if (r.status() >= 404 && !u.includes('favicon')) errors.push(r.status() + ': ' + u);
  });
  page.on('requestfailed', (r) => {
    const u = r.url();
    if (!u.includes('favicon')) errors.push('requestfailed: ' + u + ' ' + (r.failure()?.errorText || ''));
  });

  // 1. /admin 加载
  await page.goto(BASE + '/admin', { waitUntil: 'networkidle', timeout: 30000 });
  const title = await page.title();
  check('标题含 SignalWatch', title.includes('SignalWatch'));

  // 2. 非白屏：#app 有内容
  const htmlBefore = await page.evaluate(() => document.getElementById('app')?.innerHTML || '');
  check('#app 有渲染内容（非白屏）', htmlBefore.length > 100);

  // 3. 登录表单存在
  const inputCount = await page.locator('input').count();
  check('有登录输入框', inputCount >= 2);

  // 4. 登录前截图
  await page.screenshot({ path: 'D:/signalwatch/admin-ui/e2e-admin-before-login.png', fullPage: true });

  // 5. 登录
  try {
    await page.locator('input').first().fill(USER);
    await page.locator('input[type="password"]').fill(PASS);
    const btn = page.getByRole('button', { name: /登录|登\s*录|Sign\s*in/i }).first();
    await btn.click();
    await page.waitForTimeout(2500);
  } catch (e) {
    check('登录操作成功', false);
    console.log('  登录异常:', e.message);
  }

  // 6. 登录后页面
  const htmlAfter = await page.evaluate(() => document.getElementById('app')?.innerHTML || '');
  check('登录后页面有内容', htmlAfter.length > 100);
  await page.screenshot({ path: 'D:/signalwatch/admin-ui/e2e-admin-after-login.png', fullPage: true });

  // 7. JS 加载错误（白屏根因的验证：不应再有 /admin/assets 404）
  const realErrors = errors.filter((e) => !e.includes('favicon'));
  check('无 JS/资源加载错误', realErrors.length === 0);
  if (realErrors.length) {
    console.log('  错误列表:');
    realErrors.slice(0, 12).forEach((e) => console.log('   -', e));
  }

  await browser.close();
  console.log(failures === 0 ? '\nE2E 全部通过' : `\nE2E 有 ${failures} 个失败`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error('E2E 异常:', e); process.exit(1); });
