import { expect, test } from '@playwright/test';
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
};

function safeName(host: string): string {
  return host.replace(/[^a-zA-Z0-9.-]/g, '_').replace(/\./g, '_').toLowerCase();
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
