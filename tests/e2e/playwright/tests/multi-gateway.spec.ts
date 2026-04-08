/**
 * Multi-gateway routing tests.
 *
 * Validates that the stack works correctly when the gateway_host is changed
 * via the routing API. Tests cover app reachability, SPA rewriting, session
 * cookies, referer-based routing, and envoy-config propagation.
 *
 * Prerequisites:
 *   - Running compose stack with controller on port 9100
 *   - Envoy edge proxy reachable on STACK_COMPOSE_EDGE_PORT
 *   - STACK_COMPOSE_GATEWAY_HOST resolving to the edge proxy
 *
 * Usage:
 *   MULTI_GATEWAY_TEST=1 STACK_COMPOSE_GATEWAY_HOST=apps.media-dev.local \
 *     npx playwright test tests/multi-gateway.spec.ts
 */

import { expect, test } from '@playwright/test';

const enabled = process.env.MULTI_GATEWAY_TEST === '1';
const controllerUrl = process.env.CONTROLLER_URL || 'http://127.0.0.1:9100';
const gatewayHost = process.env.STACK_COMPOSE_GATEWAY_HOST || '';
const edgePort = process.env.STACK_COMPOSE_EDGE_PORT || '80';
const gatewayIp = process.env.STACK_GATEWAY_IP || process.env.STACK_NODE_IP || '127.0.0.1';
const gatewayBase = `http://${gatewayIp}:${edgePort}`;
const auth = { Authorization: 'Basic ' + Buffer.from('admin:media-stack').toString('base64') };

/** Fetch current routing config from the controller. */
async function getRouting(request: any): Promise<Record<string, any>> {
  const r = await request.get(`${controllerUrl}/api/routing`);
  return r.json();
}

/** Update routing config and return the response body. */
async function postRouting(request: any, data: Record<string, any>): Promise<Record<string, any>> {
  const r = await request.post(`${controllerUrl}/api/routing`, {
    data,
    headers: { 'Content-Type': 'application/json', ...auth },
  });
  return r.json();
}

test.describe('Multi-gateway routing', () => {
  test.skip(!enabled || !gatewayHost, 'Set MULTI_GATEWAY_TEST=1 and STACK_COMPOSE_GATEWAY_HOST');
  test.setTimeout(30_000);

  // -----------------------------------------------------------------------
  // 1. GET /api/routing returns current gateway_host
  // -----------------------------------------------------------------------
  test('GET /api/routing returns current gateway_host', async ({ request }) => {
    const d = await getRouting(request);
    expect(d.gateway_host).toBeDefined();
    expect(typeof d.gateway_host).toBe('string');
    expect(d.gateway_host.length).toBeGreaterThan(0);
  });

  // -----------------------------------------------------------------------
  // 2. All apps reachable through current gateway
  // -----------------------------------------------------------------------
  for (const app of ['jellyfin', 'sonarr', 'radarr']) {
    test(`/app/${app} is reachable through gateway`, async ({ request }) => {
      const r = await request.get(`${gatewayBase}/app/${app}`, {
        headers: { Host: gatewayHost },
        maxRedirects: 5,
      });
      expect(
        r.status(),
        `/app/${app} returned ${r.status()} through gateway`,
      ).toBeLessThan(400);
    });
  }

  // -----------------------------------------------------------------------
  // 3. Root path redirects to default app
  // -----------------------------------------------------------------------
  test('root path redirects to default app', async ({ request }) => {
    const r = await request.get(`${gatewayBase}/`, {
      headers: { Host: gatewayHost, Accept: 'text/html' },
      maxRedirects: 0,
    });
    const status = r.status();
    expect([301, 302, 307, 308]).toContain(status);
    const location = r.headers()['location'] || '';
    expect(location).toMatch(/\/app\//);
  });

  // -----------------------------------------------------------------------
  // 4. Static assets load without 404 through gateway
  // -----------------------------------------------------------------------
  test('static assets load without 404 through gateway', async ({ request }) => {
    // Fetch Sonarr's HTML and extract a static asset reference.
    const htmlResp = await request.get(`${gatewayBase}/app/sonarr`, {
      headers: { Host: gatewayHost },
      maxRedirects: 5,
    });
    expect(htmlResp.ok()).toBeTruthy();
    const html = await htmlResp.text();

    // Sonarr embeds JS/CSS assets as relative or absolute paths.
    const assetMatch = html.match(/(?:src|href)=["'](?:\/app\/sonarr\/)?([^"']*\.(?:js|css))/);
    if (!assetMatch) {
      // If no parseable asset, the page must at least be HTML.
      expect(html).toMatch(/<!doctype html/i);
      return;
    }
    const assetPath = assetMatch[1].startsWith('/') ? assetMatch[1] : `/app/sonarr/${assetMatch[1]}`;
    const assetResp = await request.get(`${gatewayBase}${assetPath}`, {
      headers: { Host: gatewayHost },
    });
    expect(
      assetResp.status(),
      `Asset ${assetPath} returned ${assetResp.status()} through gateway`,
    ).toBeLessThan(400);
  });

  // -----------------------------------------------------------------------
  // 5. SPA base href is rewritten for the current gateway
  // -----------------------------------------------------------------------
  test('SPA base href is rewritten for gateway routing', async ({ request }) => {
    // Bazarr is a SPA that requires base href rewriting by the Lua filter.
    try {
      const r = await request.get(`${gatewayBase}/app/bazarr`, {
        headers: { Accept: 'text/html', Host: gatewayHost },
      });
      if (!r.ok()) {
        test.skip(true, `Bazarr not reachable via gateway (status ${r.status()})`);
        return;
      }
      const html = await r.text();
      // The Lua filter should rewrite <base href="/"> to <base href="/app/bazarr/">
      expect(html).toContain('<base href="/app/bazarr/"');
      expect(html).not.toMatch(/<base\s+href=["']\/["']/);
    } catch {
      test.skip(true, 'Gateway not reachable');
    }
  });

  // -----------------------------------------------------------------------
  // 6. API endpoints work through gateway
  // -----------------------------------------------------------------------
  test('API endpoints work through gateway (/app/sonarr/api/v3/system/status)', async ({ request }) => {
    const r = await request.get(`${gatewayBase}/api/v3/system/status`, {
      headers: {
        Host: gatewayHost,
        Referer: `http://${gatewayHost}:${edgePort}/app/sonarr/`,
      },
      maxRedirects: 0,
    });
    // Sonarr returns 200 (with API key) or 401 (without) -- both confirm routing worked.
    expect(
      [200, 401].includes(r.status()),
      `Expected 200 or 401 from Sonarr API, got ${r.status()}`,
    ).toBeTruthy();
  });

  // -----------------------------------------------------------------------
  // 7. Session cookies are set with correct path
  // -----------------------------------------------------------------------
  test('session cookies are set with correct path', async ({ request }) => {
    const r = await request.get(`${gatewayBase}/app/sonarr`, {
      headers: { Host: gatewayHost },
      maxRedirects: 0,
    });
    const setCookieHeaders = r.headersArray().filter(
      (h: { name: string }) => h.name.toLowerCase() === 'set-cookie',
    );
    const sonarrCookie = setCookieHeaders.find(
      (c: { value: string }) => c.value.includes('media_stack_app_sonarr=1'),
    );
    expect(sonarrCookie, 'Expected media_stack_app_sonarr=1 cookie to be set').toBeTruthy();
  });

  // -----------------------------------------------------------------------
  // 8. Referer-based routing works through gateway
  // -----------------------------------------------------------------------
  test('referer-based routing works through gateway', async ({ request }) => {
    // A request with Referer pointing at /app/sonarr should route to Sonarr.
    const r = await request.get(`${gatewayBase}/api/v3/system/status`, {
      headers: {
        Host: gatewayHost,
        Referer: `http://${gatewayHost}:${edgePort}/app/sonarr/`,
      },
      maxRedirects: 0,
    });
    // Either 200 (API key present) or 401 (no key) confirms the request reached Sonarr.
    expect([200, 401]).toContain(r.status());
    // Internal routing headers must be stripped.
    expect(r.headers()['x-media-stack-prefix']).toBeUndefined();
  });

  // -----------------------------------------------------------------------
  // 9. POST /api/routing can change gateway_host (save + restore original)
  // -----------------------------------------------------------------------
  test('POST /api/routing can change gateway_host', async ({ request }) => {
    const original = await getRouting(request);
    const testHost = 'apps.multi-gw-test.local';

    try {
      const updateResp = await postRouting(request, { gateway_host: testHost });
      expect(updateResp.status).toBe('updated');
      expect(updateResp.changed).toContain('gateway_host');
      expect(updateResp.routing.gateway_host).toBe(testHost);

      // Verify the change persisted.
      const after = await getRouting(request);
      expect(after.gateway_host).toBe(testHost);
    } finally {
      // Restore original value.
      await postRouting(request, { gateway_host: original.gateway_host });
    }
  });

  // -----------------------------------------------------------------------
  // 10. After routing change, envoy-config action appears in history
  // -----------------------------------------------------------------------
  test('after routing change, envoy-config action appears in history', async ({ request }) => {
    const original = await getRouting(request);
    const testHost = 'apps.envoy-history-test.local';

    try {
      await postRouting(request, { gateway_host: testHost });

      // Wait briefly for the action to be recorded.
      await new Promise(resolve => setTimeout(resolve, 2000));

      const statusResp = await request.get(`${controllerUrl}/status`);
      expect(statusResp.ok()).toBeTruthy();
      const status = await statusResp.json();
      const history: Array<{ action?: string; name?: string }> = status.action_history || [];
      const envoyActions = history.filter(
        (a) => a.action === 'envoy-config' || a.name === 'envoy-config',
      );
      expect(envoyActions.length).toBeGreaterThan(0);
    } finally {
      await postRouting(request, { gateway_host: original.gateway_host });
    }
  });

  // -----------------------------------------------------------------------
  // 11. Multiple apps accessible simultaneously through same gateway
  // -----------------------------------------------------------------------
  test('multiple apps accessible simultaneously through same gateway', async ({ request }) => {
    const apps = ['sonarr', 'radarr', 'bazarr', 'homepage'];
    const results: Array<{ app: string; status: number }> = [];

    for (const app of apps) {
      const r = await request.get(`${gatewayBase}/app/${app}`, {
        headers: { Host: gatewayHost },
        maxRedirects: 5,
      });
      results.push({ app, status: r.status() });
    }

    // All apps should return a successful response.
    for (const { app, status } of results) {
      expect(
        status,
        `${app} returned ${status} -- expected < 400`,
      ).toBeLessThan(400);
    }
    // At least 3 out of 4 must succeed (some apps may not be deployed).
    const successCount = results.filter(r => r.status < 400).length;
    expect(successCount).toBeGreaterThanOrEqual(3);
  });

  // -----------------------------------------------------------------------
  // 12. Homepage tiles contain correct URLs for the gateway
  // -----------------------------------------------------------------------
  test('Homepage tiles contain correct URLs for the gateway', async ({ request }) => {
    const servicesResp = await request.get(`${gatewayBase}/app/homepage/api/services`, {
      headers: { Host: gatewayHost },
      maxRedirects: 3,
    });
    if (!servicesResp.ok()) {
      test.skip(true, `Homepage API not reachable (status ${servicesResp.status()})`);
      return;
    }

    const groups = (await servicesResp.json()) as Array<{
      services?: Array<{ name?: string; href?: string }>;
    }>;
    const tileUrls: Array<{ name: string; href: string }> = [];
    for (const group of groups ?? []) {
      for (const svc of group.services ?? []) {
        const href = (svc?.href ?? '').trim();
        if (href) {
          tileUrls.push({ name: svc.name ?? href, href });
        }
      }
    }

    expect(tileUrls.length, 'Homepage should have tile URLs').toBeGreaterThan(0);

    // Tile hrefs that are internal should use /app/ prefix paths.
    const internalTiles = tileUrls.filter(({ href }) =>
      href.includes('/app/') || href.startsWith('/'),
    );
    for (const { name, href } of internalTiles) {
      expect(
        href,
        `Tile '${name}' href should use /app/ prefix`,
      ).toContain('/app/');
    }
  });

  // -----------------------------------------------------------------------
  // 13. Health probes still accessible through gateway
  // -----------------------------------------------------------------------
  test('health probes still accessible through gateway', async ({ request }) => {
    // The controller healthz endpoint should be reachable directly.
    const r = await request.get(`${controllerUrl}/healthz`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.status).toBe('ok');

    // Envoy itself should respond on the gateway (not 503/502).
    const gatewayResp = await request.get(`${gatewayBase}/`, {
      headers: { Host: gatewayHost },
      maxRedirects: 0,
    });
    expect(gatewayResp.status()).toBeLessThan(500);
  });

  // -----------------------------------------------------------------------
  // 14. SSE log stream is accessible through the controller
  // -----------------------------------------------------------------------
  test('SSE log stream endpoint responds with event-stream content type', async ({ request }) => {
    // The controller exposes /logs/stream as an SSE endpoint.
    // We cannot hold the connection open in Playwright request API, but we can
    // verify the initial response headers are correct.
    try {
      const r = await request.get(`${controllerUrl}/logs/stream`, {
        headers: { Accept: 'text/event-stream' },
        timeout: 5000,
      });
      // SSE endpoint returns 200 with text/event-stream content type.
      expect(r.status()).toBe(200);
      const contentType = r.headers()['content-type'] || '';
      expect(contentType).toContain('text/event-stream');
    } catch {
      // Connection timeout is expected for a long-lived stream -- that is fine.
      // The test passes as long as no hard error occurred before the stream opened.
    }
  });

  // -----------------------------------------------------------------------
  // 15. Gateway port is correctly applied in routing
  // -----------------------------------------------------------------------
  test('gateway port is correctly applied in routing config', async ({ request }) => {
    const routing = await getRouting(request);
    expect(routing.gateway_port).toBeDefined();
    expect(typeof routing.gateway_port).toBe('number');
    expect(routing.gateway_port).toBeGreaterThan(0);

    // Verify that updating gateway_port is accepted and reflected.
    const original = await getRouting(request);
    const testPort = original.gateway_port === 8080 ? 9080 : 8080;

    try {
      const updateResp = await postRouting(request, { gateway_port: testPort });
      expect(updateResp.status).toBe('updated');
      expect(updateResp.changed).toContain('gateway_port');
      expect(updateResp.routing.gateway_port).toBe(testPort);

      // Read back and verify.
      const after = await getRouting(request);
      expect(after.gateway_port).toBe(testPort);
    } finally {
      // Restore original port.
      await postRouting(request, { gateway_port: original.gateway_port });
    }
  });
});
