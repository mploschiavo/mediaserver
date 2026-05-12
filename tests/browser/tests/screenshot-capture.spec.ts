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
const qbUsername = stackAdminUsername;
const qbPassword = stackAdminPassword;
const jellyseerrUsername = process.env.JELLYSEERR_USERNAME || stackAdminUsername;
const jellyseerrPassword = process.env.JELLYSEERR_PASSWORD || stackAdminPassword;
const sabUsername = process.env.SABNZBD_USERNAME || stackAdminUsername;
const sabPassword = process.env.SABNZBD_PASSWORD || stackAdminPassword;

const hosts = hostCsv
  .split(',')
  .map((v) => v.trim())
  .filter(Boolean);

// ``hosts`` is the full set every browser request must be able to
// resolve. For the per-app screenshot loop we want ONE entry per
// service. Selection rules:
//   * Drop non-app prefixes (controller / authelia / authentik / auth).
//   * Prefer the ``.iomio.io`` form when present — the Authelia
//     session cookie is scoped to ``.iomio.io`` so per-app captures
//     reuse the bootstrap auth. On a remote k8s box ``.local``
//     hostnames don't resolve at all (mDNS doesn't cross the LAN).
//   * Require the service to have BOTH ``.iomio.io`` and ``.local``
//     forms — that's the marker for an actually-deployed app.
//     Single-form ``.iomio.io`` entries (``emby``/``mythtv``/
//     ``nzbget``/``jdownloader``/``plex``/``jf``) are Envoy stubs
//     for non-deployed services and would just emit 404 PNGs.
const _NON_APP_PREFIXES = new Set<string>([
  'auth', 'authelia', 'authentik',
  'm',  // controller host
]);
const slugSiblingCount = new Map<string, number>();
for (const h of hosts) {
  if (h.endsWith('.iomio.io') || h.endsWith('.local')) {
    const slug = h.split('.')[0];
    slugSiblingCount.set(slug, (slugSiblingCount.get(slug) ?? 0) + 1);
  }
}
const capturedAppHosts: string[] = hosts.filter((h: string) => {
  if (!h.endsWith('.iomio.io')) return false;
  const slug = h.split('.')[0];
  if (_NON_APP_PREFIXES.has(slug)) return false;
  return (slugSiblingCount.get(slug) ?? 0) >= 2;
});

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
  // ``capturedAppHosts`` is already deduped to one entry per
  // service, so the leading slug (segment before the first dot)
  // uniquely identifies the app. Drop the TLD/domain entirely so
  // the README can ``![](bazarr.png)`` without per-deployment
  // hostname noise leaking into doc filenames.
  return host.split('.')[0].toLowerCase().replace(/[^a-z0-9-]/g, '_');
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

  for (const host of capturedAppHosts) {
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

  // Controller dashboard — every top-level route under
  // ``ui/src/routes/`` saved as one PNG per page. Ratchet at
  // ``tests/unit/ratchets/test_screenshot_route_coverage.py``
  // pins this list against the route filesystem.
  //
  // Two deployment modes:
  //
  // 1. Compose / direct (default): port-9100 access, no auth.
  //    Used when ``STACK_CONTROLLER_HOST`` is unset.
  // 2. K8s via Envoy + Authelia: served at host
  //    ``${STACK_CONTROLLER_HOST}`` (e.g. ``m.iomio.io``) under
  //    the prefix ``${STACK_CONTROLLER_PREFIX}`` (e.g.
  //    ``/app/media-stack-ui``). Auth flow: visit any controller
  //    URL → Envoy ext_authz → Authelia portal at
  //    ``${STACK_AUTHELIA_HOST}`` → submit credentials → cookie
  //    set → redirect back. Cookie persists for the rest of the
  //    capture loop.
  const controllerHost = process.env.STACK_CONTROLLER_HOST || '';
  const controllerPrefix = (process.env.STACK_CONTROLLER_PREFIX || '').replace(/\/$/, '');
  const autheliaHost = process.env.STACK_AUTHELIA_HOST || '';
  const controllerPort = process.env.CONTROLLER_PORT || '9100';
  const controllerRoutes: { name: string; path: string }[] = [
    { name: 'dashboard', path: '/' },
    { name: 'apps', path: '/apps' },
    { name: 'jobs', path: '/jobs' },
    { name: 'ops', path: '/ops' },
    { name: 'routing', path: '/routing' },
    { name: 'auth', path: '/auth' },
    { name: 'security', path: '/security' },
    { name: 'users', path: '/users' },
    { name: 'sessions', path: '/sessions' },
    { name: 'bans', path: '/bans' },
    { name: 'audit-log', path: '/audit-log' },
    { name: 'logs', path: '/logs' },
    { name: 'media-integrity', path: '/media-integrity' },
    { name: 'guardrails', path: '/guardrails' },
    { name: 'livetv', path: '/livetv' },
    { name: 'content', path: '/content' },
    { name: 'snapshots', path: '/snapshots' },
    { name: 'webhooks', path: '/webhooks' },
    { name: 'api-docs', path: '/api-docs' },
    { name: 'me', path: '/me' },
    { name: 'about', path: '/about' },
  ];

  function controllerUrlFor(routePath: string): string {
    if (controllerHost) {
      const safePath = routePath === '/' ? '' : routePath;
      return `http://${controllerHost}${controllerPrefix}${safePath || '/'}`;
    }
    return `http://${nodeIp}:${controllerPort}${routePath}`;
  }

  // Single test that loops every controller route. Shares one
  // browser context (and thus one Authelia cookie) across the
  // whole loop — that's the only mode where the k8s capture is
  // sensible, because logging into Authelia 21 times would be
  // wasteful and flaky. 21 routes × ~7s each + auth bootstrap
  // ~= ~3min, well past Playwright's 30s default.
  test('capture controller routes', async ({ browser }) => {
    test.setTimeout(5 * 60 * 1000);
    const context = await browser.newContext({
      viewport: { width: 1680, height: 945 },
    });
    const page = await context.newPage();

    if (controllerHost && autheliaHost) {
      // Authelia bootstrap: visit a controller URL, follow the
      // 302 redirect to Authelia, submit the form, wait for the
      // redirect back to the controller UI.
      const bootstrapUrl = controllerUrlFor('/');
      try {
        await page.goto(bootstrapUrl, { waitUntil: 'domcontentloaded', timeout: 15000 });
        await page.waitForTimeout(2000);
        // On the Authelia portal, fill admin credentials. Common
        // selectors used by Authelia 4.x portal.
        const loggedIn = await loginCommonForm(page, stackAdminUsername, stackAdminPassword);
        if (loggedIn) {
          await page.waitForTimeout(3000);
        }
      } catch (err) {
        console.warn(`[WARN] Authelia bootstrap failed: ${err}`);
      }
    }

    for (const { name, path: routePath } of controllerRoutes) {
      const url = controllerUrlFor(routePath);
      try {
        const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
        // 4s margin for SPA hydration + initial data load.
        await page.waitForTimeout(4000);
        if (response && !acceptableStatusCodes.has(response.status()) && strictMode) {
          expect(
            acceptableStatusCodes.has(response.status()),
            `Controller ${routePath} returned HTTP ${response.status()}`,
          ).toBeTruthy();
        }
      } catch (err) {
        console.warn(`[WARN] Controller route ${routePath} not reachable at ${url}: ${err}`);
      }
      const filePath = path.join(
        screenshotDir,
        `controller_${name.replace(/[\/-]/g, '_')}.png`,
      );
      await page.screenshot({ path: filePath, fullPage: true });
    }
    await context.close();
  });
});
