import { expect, test } from '@playwright/test';

const nodeIp = process.env.STACK_NODE_IP || '';
const testSkipReason =
  nodeIp.length === 0
    ? 'STACK_NODE_IP is not set; export STACK_NODE_IP=<cluster node ip> before running Playwright UX smoke tests.'
    : '';

// Compose path-prefix smoke: probe via gateway IP + edge port with Host-header routing.
// Set STACK_GATEWAY_IP (or STACK_NODE_IP as fallback) and optionally
// STACK_COMPOSE_GATEWAY_HOST / STACK_COMPOSE_EDGE_PORT to override defaults.
const gatewayIp = process.env.STACK_GATEWAY_IP || process.env.STACK_NODE_IP || '';
const gatewayHost = process.env.STACK_COMPOSE_GATEWAY_HOST || 'apps.media-dev.local';
const edgePort = process.env.STACK_COMPOSE_EDGE_PORT || '18080';
const pathPrefixSkipReason =
  gatewayIp.length === 0
    ? 'STACK_GATEWAY_IP (or STACK_NODE_IP) is not set; export STACK_GATEWAY_IP=<host> before running compose path-prefix smoke tests.'
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

test.describe('Compose path-prefix smoke checks', () => {
  test.skip(Boolean(pathPrefixSkipReason), pathPrefixSkipReason);

  const gatewayBase = `http://${gatewayIp}:${edgePort}`;
  const hostHeader = { Host: gatewayHost };

  test('Jellyseerr /app/jellyseerr root does not escape base path', async ({ request }) => {
    // Follow redirects: the final URL must remain under /app/jellyseerr.
    const response = await request.get(`${gatewayBase}/app/jellyseerr`, {
      headers: hostHeader,
      maxRedirects: 5,
    });
    expect([200, 304]).toContain(response.status());
    const finalUrl = response.url();
    expect(
      finalUrl,
      `Navigation escaped /app/jellyseerr base path — ended up at ${finalUrl}`,
    ).toMatch(/\/app\/jellyseerr/);
    const body = await response.text();
    expect(body.toLowerCase()).toContain('jellyseerr');
  });

  test('Jellyseerr /app/jellyseerr/login is directly reachable', async ({ request }) => {
    // With BASE_URL set, /app/jellyseerr/login must be a valid route — not a 404.
    const response = await request.get(`${gatewayBase}/app/jellyseerr/login`, {
      headers: hostHeader,
      maxRedirects: 3,
    });
    expect(
      [200, 304],
      `/app/jellyseerr/login returned ${response.status()} — broken base-path routing`,
    ).toContain(response.status());
    const body = await response.text();
    expect(body.toLowerCase()).toContain('jellyseerr');
  });

  test('Jellyseerr API status is reachable via base path', async ({ request }) => {
    const response = await request.get(`${gatewayBase}/app/jellyseerr/api/v1/status`, {
      headers: hostHeader,
      maxRedirects: 0,
    });
    expect(
      response.status(),
      '/app/jellyseerr/api/v1/status must be reachable through the gateway',
    ).toBe(200);
  });

  test('Jellyseerr first-party static assets are reachable via base path', async ({ request }) => {
    const mainResponse = await request.get(`${gatewayBase}/app/jellyseerr`, {
      headers: hostHeader,
      maxRedirects: 5,
    });
    expect([200, 304]).toContain(mainResponse.status());
    const html = await mainResponse.text();

    // Extract a _next static asset reference — with BASE_URL set these are prefixed
    // /app/jellyseerr/_next/static/... so a local asset 4xx means base-path is broken.
    const assetMatch = html.match(/\/app\/jellyseerr\/_next\/static\/[^"'\s>]+\.js/);
    if (!assetMatch) {
      // If no inline asset reference, at minimum the page must look like HTML.
      expect(html, 'Response must be an HTML document').toMatch(/<!doctype html/i);
      return;
    }
    const assetPath = assetMatch[0];
    const assetResponse = await request.get(`${gatewayBase}${assetPath}`, {
      headers: hostHeader,
      maxRedirects: 0,
    });
    expect(
      assetResponse.status(),
      `First-party static asset ${assetPath} returned 4xx — assets are not routed through base path`,
    ).toBeLessThan(400);
  });
});

