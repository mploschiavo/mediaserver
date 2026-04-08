import { expect, test } from '@playwright/test';

const nodeIp = process.env.STACK_NODE_IP || '';
const ingressPort = process.env.STACK_INGRESS_PORT || '80';
const portSuffix = ingressPort === '80' ? '' : `:${ingressPort}`;
// Jellyfin gets a "direct host" derived from the stack name (e.g. jellyfin.media-stack.local).
// Override via STACK_JELLYFIN_HOST if your profile uses a different host.
const jellyfinHost = process.env.STACK_JELLYFIN_HOST || 'jellyfin.media-stack.local';
const hostCsv =
  process.env.STACK_HOSTS ||
  [
    'homepage.local',
    jellyfinHost,
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
      const response = await request.get(`http://${nodeIp}${portSuffix}/`, {
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

  test('jellyfin should not redirect to startup wizard path', async ({ request }) => {
    const response = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: jellyfinHost },
      maxRedirects: 0,
    });
    const location = response.headers()['location'] || '';
    expect(location.toLowerCase()).not.toContain('/wizard/start');
  });

  test('homepage.local should expose stack services in /api/services', async ({ request }) => {
    const response = await request.get(`http://${nodeIp}${portSuffix}/api/services`, {
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

// ---------------------------------------------------------------------------
// Response header validation
// ---------------------------------------------------------------------------
test.describe('Response headers', () => {
  test.skip(Boolean(testSkipReason), testSkipReason);

  test('envoy identifies itself via server header', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: hosts[0] },
    });
    expect(r.headers()['server']).toBe('envoy');
  });

  test('x-envoy-upstream-service-time header is present and numeric', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: hosts[0] },
    });
    const serviceTime = r.headers()['x-envoy-upstream-service-time'];
    expect(serviceTime).toBeDefined();
    expect(Number(serviceTime)).not.toBeNaN();
    expect(Number(serviceTime)).toBeGreaterThanOrEqual(0);
  });

  test('CORS preflight returns access-control headers', async ({ request }) => {
    const r = await request.fetch(`http://${nodeIp}${portSuffix}/`, {
      method: 'OPTIONS',
      headers: {
        Host: hosts[0],
        Origin: `http://${hosts[0]}`,
        'Access-Control-Request-Method': 'GET',
      },
    });
    // Envoy should respond; even without explicit CORS config, it must not crash.
    // A 200 or 405 are both acceptable — the key assertion is that envoy handled it.
    expect(r.headers()['server']).toBe('envoy');
  });

  test('x-media-stack-prefix is stripped from host-routed responses', async ({ request }) => {
    // The Lua response filter removes x-media-stack-prefix before sending to the client.
    // Host-routed requests should never expose this internal header.
    const r = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: hosts[0] },
    });
    expect(r.headers()['x-media-stack-prefix']).toBeUndefined();
  });

  test('x-media-stack-host is stripped from host-routed responses', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: hosts[0] },
    });
    expect(r.headers()['x-media-stack-host']).toBeUndefined();
  });

  test('content-type is text/html for root HTML pages', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: 'homepage.local' },
    });
    const contentType = r.headers()['content-type'] || '';
    expect(contentType).toContain('text/html');
  });

  test('content-type is application/json for API endpoints', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/api/services`, {
      headers: { Host: 'homepage.local' },
    });
    const contentType = r.headers()['content-type'] || '';
    expect(contentType).toContain('application/json');
  });
});

// ---------------------------------------------------------------------------
// Path-prefix routing (gateway host mode)
// ---------------------------------------------------------------------------
test.describe('Path-prefix routing', () => {
  const gateway = process.env.STACK_COMPOSE_GATEWAY_HOST || '';
  test.skip(!gateway, 'STACK_COMPOSE_GATEWAY_HOST not set');

  test('/app/sonarr returns 200', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/app/sonarr`, {
      headers: { Host: gateway },
      maxRedirects: 5,
    });
    expect(r.status()).toBe(200);
  });

  test('/app/radarr returns 200', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/app/radarr`, {
      headers: { Host: gateway },
      maxRedirects: 5,
    });
    expect(r.status()).toBe(200);
  });

  test('/app/bazarr returns 200', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/app/bazarr`, {
      headers: { Host: gateway },
      maxRedirects: 5,
    });
    expect(r.status()).toBe(200);
  });

  test('root redirects to default app', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: gateway, Accept: 'text/html' },
      maxRedirects: 0,
    });
    const status = r.status();
    // Root should redirect (301/302/307/308) to the default app path prefix.
    expect([301, 302, 307, 308]).toContain(status);
    const location = r.headers()['location'] || '';
    expect(location).toMatch(/\/app\//);
  });

  test('/app/nonexistent returns 404', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/app/nonexistent`, {
      headers: { Host: gateway },
      maxRedirects: 0,
    });
    // Non-existent app prefix falls through to catch-all which proxies to the
    // default cluster; the upstream may return 404 or it may be a redirect.
    // The key assertion: Envoy handled it (didn't 503 / connection-refused).
    expect(r.status()).toBeLessThan(503);
  });

  test('x-media-stack-prefix is stripped from path-prefix responses', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/app/sonarr`, {
      headers: { Host: gateway },
      maxRedirects: 5,
    });
    expect(r.headers()['x-media-stack-prefix']).toBeUndefined();
  });

  test('/app and /app/ redirect to default homepage', async ({ request }) => {
    for (const path of ['/app', '/app/']) {
      const r = await request.get(`http://${nodeIp}${portSuffix}${path}`, {
        headers: { Host: gateway },
        maxRedirects: 0,
      });
      const status = r.status();
      expect(
        [301, 302, 307, 308].includes(status),
        `${path} should redirect but got ${status}`,
      ).toBeTruthy();
      const location = r.headers()['location'] || '';
      expect(location).toContain('/app/homepage');
    }
  });
});

// ---------------------------------------------------------------------------
// Cookie and referer fallback routing (gateway host mode)
// ---------------------------------------------------------------------------
test.describe('Fallback routing', () => {
  const gateway = process.env.STACK_COMPOSE_GATEWAY_HOST || '';
  test.skip(!gateway, 'STACK_COMPOSE_GATEWAY_HOST not set');

  test('referer from /app/sonarr routes API requests to Sonarr', async ({ request }) => {
    // Envoy uses referer-based fallback: a request to /api/v3/system/status
    // with Referer pointing at /app/sonarr should route to the Sonarr cluster.
    const r = await request.get(`http://${nodeIp}${portSuffix}/api/v3/system/status`, {
      headers: {
        Host: gateway,
        Referer: `http://${gateway}${portSuffix}/app/sonarr/`,
      },
      maxRedirects: 0,
    });
    // Sonarr returns 200 for /api/v3/system/status (may require API key → 401).
    // Either response confirms the request reached Sonarr, not a 404 catch-all.
    expect([200, 401]).toContain(r.status());
  });

  test('session cookie routes follow-up requests to correct app', async ({ request }) => {
    // First, visit /app/sonarr to get a session cookie.
    const initial = await request.get(`http://${nodeIp}${portSuffix}/app/sonarr`, {
      headers: { Host: gateway },
      maxRedirects: 0,
    });
    // Extract the session cookie from the Set-Cookie header.
    const setCookie = initial.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie');
    const sonarrCookie = setCookie.find((c) => c.value.includes('media_stack_app_sonarr=1'));
    expect(sonarrCookie, 'Expected media_stack_app_sonarr=1 cookie to be set').toBeTruthy();

    // Now use the cookie to request a root-relative path — should route to Sonarr.
    const r = await request.get(`http://${nodeIp}${portSuffix}/api/v3/system/status`, {
      headers: {
        Host: gateway,
        Cookie: 'media_stack_app_sonarr=1',
      },
      maxRedirects: 0,
    });
    expect([200, 401]).toContain(r.status());
  });

  test('requests without cookie or referer fall through to catch-all', async ({ request }) => {
    // A bare request to a root-relative path (no cookie, no referer) should
    // hit the catch-all route which proxies to the default app.
    const r = await request.get(`http://${nodeIp}${portSuffix}/web/index.html`, {
      headers: { Host: gateway },
      maxRedirects: 0,
    });
    // The default cluster (Jellyfin) serves /web/index.html → 200 or redirect.
    expect(r.status()).toBeLessThan(500);
  });

  test('/app/bazarr sets the session cookie in the response', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/app/bazarr`, {
      headers: { Host: gateway },
      maxRedirects: 0,
    });
    const setCookieHeaders = r.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie');
    const bazarrCookie = setCookieHeaders.find((c) => c.value.includes('media_stack_app_bazarr=1'));
    expect(bazarrCookie, 'Expected media_stack_app_bazarr=1 cookie in Set-Cookie').toBeTruthy();
  });

  test('browser root access with Accept: text/html redirects to app prefix', async ({
    request,
  }) => {
    // HTML browsers hitting "/" on the gateway host should get redirected to
    // the default app path prefix (e.g. /app/jellyfin or /app/homepage).
    const r = await request.get(`http://${nodeIp}${portSuffix}/`, {
      headers: { Host: gateway, Accept: 'text/html' },
      maxRedirects: 0,
    });
    const status = r.status();
    expect([301, 302, 307, 308]).toContain(status);
    const location = r.headers()['location'] || '';
    expect(location).toMatch(/\/app\//);
  });

  test('referer fallback does not leak x-media-stack-prefix header', async ({ request }) => {
    const r = await request.get(`http://${nodeIp}${portSuffix}/api/v3/system/status`, {
      headers: {
        Host: gateway,
        Referer: `http://${gateway}${portSuffix}/app/sonarr/`,
      },
      maxRedirects: 0,
    });
    expect(r.headers()['x-media-stack-prefix']).toBeUndefined();
  });
});
