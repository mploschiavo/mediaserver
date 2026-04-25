import { chromium } from '@playwright/test';

const gateway = 'http://apps.media-dev.local:18080';
const adminUser = process.env.STACK_ADMIN_USERNAME || 'admin';
const adminPass = process.env.STACK_ADMIN_PASSWORD || 'media-dev';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const seen4xx = [];
const navs = [];
page.on('response', (resp) => {
  const url = resp.url();
  const status = resp.status();
  if (status >= 400 && url.includes('apps.media-dev.local')) {
    seen4xx.push({ status, url });
  }
});
page.on('framenavigated', frame => {
  if (frame === page.mainFrame()) navs.push(frame.url());
});

async function clickFirst(selectors, label) {
  for (const sel of selectors) {
    const loc = page.locator(sel).first();
    if (await loc.isVisible().catch(() => false)) {
      await loc.click();
      await page.waitForLoadState('domcontentloaded').catch(() => {});
      await page.waitForTimeout(1500);
      console.log(`CLICK ${label}: ${sel} -> ${page.url()}`);
      return true;
    }
  }
  console.log(`MISS ${label}`);
  return false;
}

await page.goto(`${gateway}/app/jellyseerr/login`, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

const emailInput = page.locator('input[type="text"][name="email"]').or(page.locator('input[type="email"]')).or(page.locator('input[id="email"]')).first();
if (await emailInput.isVisible().catch(() => false)) {
  await emailInput.fill(adminUser);
  await page.locator('input[type="password"]').first().fill(adminPass);
  await page.locator('button[type="submit"]').or(page.locator('button:has-text("Sign In")')).first().click();
  await page.waitForTimeout(5000);
}
console.log('AFTER LOGIN', page.url());

await clickFirst(['a[href="/discover"]', 'a:has-text("Discover")'], 'discover');
await clickFirst(['a[href="/discover/movies"]', 'a:has-text("Movies")', 'a[href*="/discover/"][href*="movie"]'], 'discover movies');
await clickFirst(['a[href*="/movie/"]', 'a[href*="/tv/"]', 'a[href*="/details/"]'], 'open media details');
await clickFirst(['a[href*="/requests"]', 'button:has-text("Request")', 'a:has-text("Requests")'], 'requests or request action');
await clickFirst(['a[href="/settings"]', 'a:has-text("Settings")'], 'settings');
await clickFirst(['a[href="/settings/users"]', 'a:has-text("Users")'], 'settings users');

console.log('FINAL URL', page.url());
console.log('NAVS');
for (const n of navs) console.log(n);
console.log('4XX');
for (const item of seen4xx) console.log(`${item.status} ${item.url}`);

await browser.close();
