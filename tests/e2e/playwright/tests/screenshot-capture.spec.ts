import { expect, test, type Locator, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const nodeIp = process.env.STACK_NODE_IP || '';
const hostCsv =
  process.env.STACK_HOSTS ||
  [
    'homepage.local',
    'jellyfin.local',
    'jellyseerr.local',
    'sonarr.local',
    'radarr.local',
    'lidarr.local',
    'readarr.local',
    'bazarr.local',
    'prowlarr.local',
    'qbittorrent.local',
    'sabnzbd.local',
    'maintainerr.local',
    'tautulli.local',
  ].join(',');
const screenshotDir =
  process.env.STACK_SCREENSHOT_DIR ||
  path.resolve(process.cwd(), '..', '..', '..', '..', 'docs', 'screenshots', 'apps');
const strictMode = (process.env.STACK_SCREENSHOT_STRICT || '0') === '1';
const stackAdminUsername = process.env.STACK_ADMIN_USERNAME || 'admin';
const stackAdminPassword = process.env.STACK_ADMIN_PASSWORD || 'media-stack-admin';
const qbUsername = process.env.QBITTORRENT_USERNAME || stackAdminUsername;
const qbPassword = process.env.QBITTORRENT_PASSWORD || stackAdminPassword;
const jellyseerrUsername = process.env.JELLYSEERR_USERNAME || stackAdminUsername;
const jellyseerrPassword = process.env.JELLYSEERR_PASSWORD || stackAdminPassword;
const sabUsername = process.env.SABNZBD_USERNAME || stackAdminUsername;
const sabPassword = process.env.SABNZBD_PASSWORD || stackAdminPassword;

const hosts = hostCsv
  .split(',')
  .map((v) => v.trim())
  .filter(Boolean);

const acceptableStatusCodes = new Set([200, 301, 302, 303, 307, 308, 401, 403]);
const resolverRules = hosts.map((host) => `MAP ${host} ${nodeIp}`).join(',');
const testSkipReason =
  nodeIp.length === 0
    ? 'STACK_NODE_IP is not set; export STACK_NODE_IP=<cluster node ip> before running screenshot capture.'
    : '';

const hostPathOverrides: Record<string, string> = {
  'jellyfin.local': '/web/',
  'jellyseerr.local': '/login',
  'maintainerr.local': '/rules',
  'tautulli.local': '/auth/login',
};

function safeName(host: string): string {
  return host.replace(/[^a-zA-Z0-9.-]/g, '_').replace(/\./g, '_').toLowerCase();
}

async function isVisible(locator: Locator): Promise<boolean> {
  try {
    return await locator.first().isVisible({ timeout: 1200 });
  } catch {
    return false;
  }
}

async function fillFirst(page: Page, selectors: string[], value: string): Promise<boolean> {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (!(await isVisible(locator))) {
      continue;
    }
    try {
      await locator.fill(value, { timeout: 2500 });
      return true;
    } catch {
      // Best-effort selector fallback.
    }
  }
  return false;
}

async function clickFirst(page: Page, selectors: string[]): Promise<boolean> {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (!(await isVisible(locator))) {
      continue;
    }
    try {
      await locator.click({ timeout: 2500 });
      return true;
    } catch {
      // Best-effort selector fallback.
    }
  }
  return false;
}

async function loginCommonForm(page: Page, username: string, password: string): Promise<boolean> {
  const hasPassword = await isVisible(page.locator('input[type="password"]'));
  if (!hasPassword) {
    return false;
  }

  const usernameFilled = await fillFirst(
    page,
    [
      'input[name="username"]',
      'input[name="user"]',
      'input[name="email"]',
      'input#username',
      'input[id*="user"]',
      'input[type="text"]',
    ],
    username,
  );
  const passwordFilled = await fillFirst(
    page,
    ['input[name="password"]', 'input#password', 'input[id*="pass"]', 'input[type="password"]'],
    password,
  );
  if (!usernameFilled || !passwordFilled) {
    return false;
  }

  await clickFirst(page, [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Sign in")',
    'button:has-text("Login")',
    'button:has-text("Log in")',
  ]);
  await page.waitForTimeout(1800);
  return true;
}

async function loginJellyfin(page: Page): Promise<void> {
  const hasPassword = await isVisible(page.locator('#txtManualPassword, input[type="password"]'));
  if (!hasPassword) {
    return;
  }
  await fillFirst(page, ['#txtManualName', 'input[name="username"]', 'input[type="text"]'], stackAdminUsername);
  await fillFirst(page, ['#txtManualPassword', 'input[name="pw"]', 'input[name="password"]', 'input[type="password"]'], stackAdminPassword);
  await clickFirst(page, ['#btnManualSubmit', 'button:has-text("Sign In")', 'button[type="submit"]']);
  await page.waitForTimeout(2200);
}

async function loginJellyseerr(page: Page): Promise<void> {
  const onLoginPage = /\/login/i.test(page.url()) || (await isVisible(page.locator('input[type="password"]')));
  if (!onLoginPage) {
    return;
  }
  await fillFirst(
    page,
    ['input[name="email"]', 'input[name="username"]', 'input#email', 'input#username', 'input[type="text"]'],
    jellyseerrUsername,
  );
  await fillFirst(page, ['input[name="password"]', 'input#password', 'input[type="password"]'], jellyseerrPassword);
  await clickFirst(page, ['button[type="submit"]', 'button:has-text("Sign In")', 'button:has-text("Login")']);
  await page.waitForTimeout(2200);
}

async function loginQbittorrent(page: Page): Promise<void> {
  const hasPassword = await isVisible(page.locator('#password, input[type="password"]'));
  if (!hasPassword) {
    return;
  }
  await fillFirst(page, ['#username', 'input[name="username"]', 'input[type="text"]'], qbUsername);
  await fillFirst(page, ['#password', 'input[name="password"]', 'input[type="password"]'], qbPassword);
  await clickFirst(page, ['#loginbutton', 'button:has-text("Log in")', 'button[type="submit"]']);
  await page.waitForTimeout(2200);
}

async function loginSabnzbd(page: Page): Promise<void> {
  const hasPassword = await isVisible(page.locator('input[type="password"]'));
  if (!hasPassword) {
    return;
  }
  const filled = await fillFirst(page, ['input[name="username"]', '#username', 'input[type="text"]'], sabUsername);
  const pw = await fillFirst(page, ['input[name="password"]', '#password', 'input[type="password"]'], sabPassword);
  if (filled && pw) {
    await clickFirst(page, ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Login")']);
    await page.waitForTimeout(2200);
  }
}

async function ensureLoggedInIfNeeded(host: string, page: Page): Promise<void> {
  if (host === 'jellyfin.local') {
    await loginJellyfin(page);
    return;
  }
  if (host === 'jellyseerr.local') {
    await loginJellyseerr(page);
    return;
  }
  if (host === 'qbittorrent.local') {
    await loginQbittorrent(page);
    return;
  }
  if (host === 'sabnzbd.local') {
    await loginSabnzbd(page);
    return;
  }
  await loginCommonForm(page, stackAdminUsername, stackAdminPassword);
}

test.use({
  launchOptions: {
    args: [`--host-resolver-rules=${resolverRules},EXCLUDE localhost`],
  },
});

test.describe('UI screenshot capture', () => {
  test.skip(Boolean(testSkipReason), testSkipReason);

  test.beforeAll(async () => {
    fs.mkdirSync(screenshotDir, { recursive: true });
  });

  for (const host of hosts) {
    test(`capture ${host}`, async ({ browser }) => {
      const context = await browser.newContext({
        viewport: { width: 1680, height: 945 },
      });
      const page = await context.newPage();

      const targetPath = hostPathOverrides[host] || '/';
      const url = `http://${host}${targetPath}`;
      const response = await page.goto(url, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1200);
      await ensureLoggedInIfNeeded(host, page);

      // Prefer meaningful dashboard pages after login.
      if (host === 'jellyfin.local') {
        await page.goto('http://jellyfin.local/web/#/home', { waitUntil: 'domcontentloaded' });
      } else if (host === 'jellyseerr.local') {
        await page.goto('http://jellyseerr.local/', { waitUntil: 'domcontentloaded' });
      } else if (host === 'maintainerr.local') {
        await page.goto('http://maintainerr.local/rules', { waitUntil: 'domcontentloaded' });
      }
      await page.waitForTimeout(1200);

      if (response) {
        const status = response.status();
        if (strictMode) {
          expect(
            acceptableStatusCodes.has(status),
            `${host} returned unexpected HTTP ${status}`,
          ).toBeTruthy();
        } else if (!acceptableStatusCodes.has(status)) {
          // Keep best-effort capture in non-strict mode.
          // eslint-disable-next-line no-console
          console.warn(`[WARN] ${host} returned HTTP ${status} during screenshot capture`);
        }
      }

      const filePath = path.join(screenshotDir, `${safeName(host)}.png`);
      await page.screenshot({ path: filePath, fullPage: true });
      await context.close();
    });
  }
});
