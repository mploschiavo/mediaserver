import { chromium } from 'playwright';

const BASE = 'http://docker.media-stack.local';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

const notFound = [];
const errors = [];
page.on('response', r => { if (r.status() === 404) notFound.push(r.url()); });
page.on('pageerror', e => errors.push(e.message));

console.log('1. Loading /app/bazarr/system/status ...');
await page.goto(`${BASE}/app/bazarr/system/status`, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(8000);
await page.screenshot({ path: '/tmp/bazarr-screenshots/01-direct-status.png' });

const body = await page.content();
const crashed = body.includes('TypeError') || body.includes('Failed to fetch dynamically imported module');
console.log(`   Crashed: ${crashed}`);
console.log(`   404s: ${notFound.length}`);
notFound.forEach(u => console.log(`     ${u}`));
console.log(`   JS errors: ${errors.length}`);
errors.forEach(e => console.log(`     ${e}`));

// Click-through flow
console.log('\n2. Click-through: /app/bazarr -> System -> Status');
const page2 = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const nf2 = []; const er2 = [];
page2.on('response', r => { if (r.status() === 404) nf2.push(r.url()); });
page2.on('pageerror', e => er2.push(e.message));

await page2.goto(`${BASE}/app/bazarr`, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page2.waitForTimeout(5000);
await page2.screenshot({ path: '/tmp/bazarr-screenshots/02-bazarr-home.png' });

const sysLink = page2.locator('a:has-text("System")').first();
if (await sysLink.isVisible({ timeout: 5000 }).catch(() => false)) {
  await sysLink.click();
  await page2.waitForTimeout(2000);
  await page2.screenshot({ path: '/tmp/bazarr-screenshots/03-system.png' });
}
const statusLink = page2.locator('a:has-text("Status")').first();
if (await statusLink.isVisible({ timeout: 5000 }).catch(() => false)) {
  await statusLink.click();
  await page2.waitForTimeout(5000);
  await page2.screenshot({ path: '/tmp/bazarr-screenshots/04-status.png' });
}

const body2 = await page2.content();
const crashed2 = body2.includes('TypeError') || body2.includes('Failed to fetch dynamically imported module');
console.log(`   Crashed: ${crashed2}`);
console.log(`   404s: ${nf2.length}`);
nf2.forEach(u => console.log(`     ${u}`));
console.log(`   JS errors: ${er2.length}`);
er2.forEach(e => console.log(`     ${e}`));

await browser.close();
console.log('\nDone.');
