import { expect, test } from '@playwright/test';

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
    'tautulli.local',
  ].join(',');

const acceptableCodes = new Set([200, 301, 302, 303, 307, 308, 401, 403]);
const hosts = hostCsv
  .split(',')
  .map((v) => v.trim())
  .filter(Boolean);

const testSkipReason =
  nodeIp.length === 0
    ? 'STACK_NODE_IP is not set; export STACK_NODE_IP=<cluster node ip> before running Playwright ingress tests.'
    : '';

test.describe('Ingress host routing', () => {
  test.skip(Boolean(testSkipReason), testSkipReason);

  for (const host of hosts) {
    test(`${host} returns an expected HTTP status`, async ({ request }) => {
      const response = await request.get(`http://${nodeIp}/`, {
        headers: { Host: host },
        maxRedirects: 0,
      });
      const status = response.status();
      expect(
        acceptableCodes.has(status),
        `${host} returned unexpected HTTP ${status}`,
      ).toBeTruthy();
    });
  }

  test('jellyfin.local should not redirect to startup wizard path', async ({ request }) => {
    const response = await request.get(`http://${nodeIp}/`, {
      headers: { Host: 'jellyfin.local' },
      maxRedirects: 0,
    });
    const location = response.headers()['location'] || '';
    expect(location.toLowerCase()).not.toContain('/wizard/start');
  });

  test('homepage.local should expose stack services in /api/services', async ({ request }) => {
    const response = await request.get(`http://${nodeIp}/api/services`, {
      headers: { Host: 'homepage.local' },
      maxRedirects: 0,
    });
    expect(response.status()).toBe(200);

    const payload = (await response.json()) as Array<{
      services?: Array<{ name?: string }>;
    }>;
    const names = new Set<string>();
    for (const group of payload || []) {
      for (const service of group.services || []) {
        if (service?.name) names.add(service.name);
      }
    }

    expect(names.has('Jellyfin')).toBeTruthy();
    expect(names.has('Jellyseerr')).toBeTruthy();
    expect(names.has('Sonarr')).toBeTruthy();
    expect(names.has('Radarr')).toBeTruthy();
    expect(names.has('qBittorrent')).toBeTruthy();
    expect(names.has('Jellyfin Setup QR')).toBeTruthy();
    expect(names.has('Samsung TV Quick Start')).toBeTruthy();
    expect(names.has('Vizio Quick Start')).toBeTruthy();
    expect(names.has('TCL Quick Start')).toBeTruthy();
  });
});
