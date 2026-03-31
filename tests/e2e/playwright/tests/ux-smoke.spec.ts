import { expect, test } from '@playwright/test';

const nodeIp = process.env.STACK_NODE_IP || '';
const testSkipReason =
  nodeIp.length === 0
    ? 'STACK_NODE_IP is not set; export STACK_NODE_IP=<cluster node ip> before running Playwright UX smoke tests.'
    : '';

test.describe('UX smoke checks', () => {
  test.skip(Boolean(testSkipReason), testSkipReason);

  test('Jellyfin web shell renders', async ({ request }) => {
    const response = await request.get(`http://${nodeIp}/web/index.html`, {
      headers: { Host: 'jellyfin.local' },
      maxRedirects: 1,
    });
    expect([200, 304]).toContain(response.status());
    const body = await response.text();
    expect(body.toLowerCase()).toContain('jellyfin');
  });

  test('Jellyseerr login shell renders', async ({ request }) => {
    const response = await request.get(`http://${nodeIp}/login`, {
      headers: { Host: 'jellyseerr.local' },
      maxRedirects: 1,
    });
    expect([200, 304]).toContain(response.status());
    const body = await response.text();
    expect(body.toLowerCase()).toContain('jellyseerr');
  });

  test('Homepage root renders dashboard shell', async ({ request }) => {
    const response = await request.get(`http://${nodeIp}/`, {
      headers: { Host: 'homepage.local' },
      maxRedirects: 1,
    });
    expect([200, 304]).toContain(response.status());
    const body = await response.text();
    // Homepage app shell should include document title + the API endpoint used by widgets.
    expect(body.toLowerCase()).toContain('homepage');
    expect(body).toContain('/api/services');
  });
});

