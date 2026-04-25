import { chromium } from '@playwright/test';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://apps.media-dev.local:18080/app/jellyseerr/login', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2000);
const before = await page.evaluate(() => ({
  patchScriptCount: document.querySelectorAll('script[data-media-stack-prefix-patch]').length,
  pushStateSource: String(history.pushState).slice(0, 200),
  firstSettingsHref: document.querySelector('a[href="/settings"]')?.getAttribute('href') || null,
}));
console.log('BEFORE', JSON.stringify(before));
const email = document => document.querySelector('input[name="email"], input[type="email"], input#email');
await page.locator('input[name="email"], input[type="email"], input#email').first().fill(process.env.STACK_ADMIN_USERNAME || 'admin');
await page.locator('input[type="password"]').first().fill(process.env.STACK_ADMIN_PASSWORD || 'media-dev');
await page.locator('button[type="submit"], button:has-text("Sign In")').first().click();
await page.waitForTimeout(5000);
const after = await page.evaluate(() => ({
  url: location.href,
  patchScriptCount: document.querySelectorAll('script[data-media-stack-prefix-patch]').length,
  pushStateSource: String(history.pushState).slice(0, 200),
  settingsHref: document.querySelector('a[href*="settings"]')?.getAttribute('href') || null,
  requestsHref: document.querySelector('a[href*="requests"]')?.getAttribute('href') || null,
}));
console.log('AFTER', JSON.stringify(after));
await browser.close();
