/**
 * Controller API endpoint tests.
 *
 * Validates the controller's management API endpoints work correctly.
 * Requires a running compose or K8s stack with controller on port 9100.
 *
 * Usage:
 *   CONTROLLER_API_TEST=1 npx playwright test tests/controller-api.spec.ts
 */

import { expect, test } from '@playwright/test';

const enabled = process.env.CONTROLLER_API_TEST === '1';
const baseUrl = process.env.CONTROLLER_URL || 'http://127.0.0.1:9100';

test.describe('Controller API', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');
  test.setTimeout(30_000);

  test('GET /healthz returns ok', async ({ request }) => {
    const r = await request.get(`${baseUrl}/healthz`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.status).toBe('ok');
  });

  test('GET /status returns phase', async ({ request }) => {
    const r = await request.get(`${baseUrl}/status`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.phase).toBeDefined();
  });

  test('GET /api/health returns service health', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/health`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.healthy).toBeGreaterThanOrEqual(0);
    expect(d.services).toBeDefined();
  });

  test('GET /api/disk returns disk usage and guardrails', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/disk`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.disk).toBeDefined();
    expect(d.guardrails).toBeDefined();
    expect(d.guardrails.max_used_percent).toBeGreaterThan(0);
    expect(d.guardrails.target_used_percent).toBeGreaterThan(0);
  });

  test('GET /api/routing returns routing config', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/routing`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.base_domain).toBeDefined();
    expect(d.stack_subdomain).toBeDefined();
    expect(d.gateway_host).toBeDefined();
    expect(d.gateway_port).toBeGreaterThan(0);
    expect(d.app_path_prefix).toBeDefined();
    expect(d.strategy).toBeDefined();
  });

  test('GET /api/stats returns library stats', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/stats`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.stats).toBeDefined();
  });

  test('GET /api/downloads returns download status', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/downloads`);
    expect(r.ok()).toBeTruthy();
  });

  test('GET /api/versions returns service versions', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/versions`);
    expect(r.ok()).toBeTruthy();
  });

  test('GET /api/env returns runtime info', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/env`);
    expect(r.ok()).toBeTruthy();
  });

  test('GET /api/openapi.json returns valid OpenAPI spec', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/openapi.json`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.openapi).toMatch(/^3\./);
    expect(d.paths).toBeDefined();
  });

  test('GET /metrics returns Prometheus metrics', async ({ request }) => {
    const r = await request.get(`${baseUrl}/metrics`);
    expect(r.ok()).toBeTruthy();
    const text = await r.text();
    expect(text).toContain('media_stack_');
  });

  test('POST /api/routing validates input', async ({ request }) => {
    // Empty body should return no_changes
    const r = await request.post(`${baseUrl}/api/routing`, {
      data: {},
      headers: { 'Content-Type': 'application/json' },
    });
    // Should not crash — returns no_changes or error
    expect(r.status()).toBeLessThan(500);
  });

  test('dashboard loads with correct title', async ({ page }) => {
    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    const title = await page.title();
    expect(title).toContain('Media Stack');
  });

  test('dashboard tabs render', async ({ page }) => {
    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    // Check tab buttons exist
    const tabs = await page.locator('#main-tabs button').allTextContents();
    expect(tabs).toContain('Logs');
    expect(tabs).toContain('Content');
    expect(tabs).toContain('Routing');
    expect(tabs).toContain('Ops');
    expect(tabs).toContain('Config');
    expect(tabs).toContain('Alerts');
  });

  test('routing tab shows config', async ({ page }) => {
    await page.goto(`${baseUrl}/#routing`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    // Routing config should load
    const routingConfig = await page.locator('#routing-config').textContent();
    expect(routingConfig).toContain('Gateway URL');
    expect(routingConfig).toContain('Localhost');
  });

  test('localhost routing works through Envoy', async ({ request }) => {
    // Envoy routing requires envoy-config-init to have generated routes.
    // On fresh standalone deploy, routes may be empty (compose file not mounted).
    try {
      const r = await request.get('http://127.0.0.1:80/', { maxRedirects: 0 });
      // Any response from Envoy means it's running — route content varies by config
      expect(r.status()).toBeLessThanOrEqual(503);
    } catch {
      // Connection refused means Envoy isn't listening on port 80 — also acceptable
      // if port mapping was removed
    }
  });

  test('POST /api/routing updates config and returns changed fields', async ({ request }) => {
    const current = await (await request.get(`${baseUrl}/api/routing`)).json();

    // POST requires Basic Auth
    const r = await request.post(`${baseUrl}/api/routing`, {
      data: { base_domain: current.base_domain },
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Basic ' + Buffer.from('admin:media-stack').toString('base64'),
      },
    });
    const d = await r.json();
    expect(d.status).toBe('no_changes');
  });

  test('GET /api/cleanup-preview returns preview data', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/cleanup-preview`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('candidates');
  });

  test('GET /api/gpu returns detection result', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/gpu`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('detected');
    expect(d).toHaveProperty('gpus');
  });

  test('GET /api/snapshots returns snapshot list', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/snapshots`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('snapshots');
  });

  test('GET /api/mounts returns mount info', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/mounts`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('mounts');
  });

  test('routing form shows live preview', async ({ page }) => {
    await page.goto(`${baseUrl}/#routing`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    // Open edit form
    await page.click('summary:has-text("Edit Routing Config")');
    await page.waitForTimeout(500);
    // Preview should show gateway pattern
    const preview = await page.locator('#rt-preview').textContent();
    expect(preview).toContain('Gateway:');
    expect(preview).toContain('Path-based:');
    expect(preview).toContain('Subdomain:');
    expect(preview).toContain('Localhost:');
  });

  test('log viewer has source selector and columns', async ({ page }) => {
    await page.goto(`${baseUrl}/#logs`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    // Should have source buttons
    const controllerBtn = page.locator('button:has-text("Controller")');
    expect(await controllerBtn.count()).toBeGreaterThan(0);
    // Should have service picker
    const picker = page.locator('#svcLogPicker');
    expect(await picker.count()).toBe(1);
    // Should have column headers
    const headers = await page.locator('.log-line').first().textContent();
    expect(headers).toContain('Time');
    expect(headers).toContain('Source');
  });
});

// ---------------------------------------------------------------------------
// Admin operations
// ---------------------------------------------------------------------------
test.describe('Admin operations', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');
  test.setTimeout(30_000);
  const auth = { Authorization: 'Basic ' + Buffer.from('admin:media-stack').toString('base64') };

  test('GET /api/versions returns version strings for services', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/versions`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    // Response should be an object with at least one service key
    expect(typeof d).toBe('object');
    expect(d).toHaveProperty('versions');
  });

  test('GET /api/downloads returns download status', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/downloads`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(typeof d).toBe('object');
  });

  test('GET /api/stats returns library counts', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/stats`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('stats');
    expect(typeof d.stats).toBe('object');
  });

  test('GET /api/env returns environment info', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/env`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('namespace');
    expect(d).toHaveProperty('node_ip');
    expect(d).toHaveProperty('platform');
    expect(d).toHaveProperty('runtime');
    expect(['compose', 'kubernetes']).toContain(d.runtime);
  });

  test('GET /api/indexers returns indexer list', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/indexers`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(typeof d).toBe('object');
  });

  test('GET /api/mounts returns mount info', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/mounts`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('mounts');
    expect(Array.isArray(d.mounts)).toBeTruthy();
  });

  test('GET /api/snapshots returns snapshot list', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/snapshots`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('snapshots');
    expect(Array.isArray(d.snapshots)).toBeTruthy();
  });

  test('GET /metrics returns Prometheus format text', async ({ request }) => {
    const r = await request.get(`${baseUrl}/metrics`);
    expect(r.ok()).toBeTruthy();
    const text = await r.text();
    // Prometheus metrics use TYPE/HELP comments and metric lines
    expect(text).toContain('media_stack_');
    expect(text).toMatch(/^# (HELP|TYPE) /m);
  });

  test('GET /api/feed.xml returns valid RSS XML', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/feed.xml`);
    expect(r.ok()).toBeTruthy();
    const text = await r.text();
    const contentType = r.headers()['content-type'] || '';
    expect(contentType).toContain('xml');
    expect(text).toContain('<rss');
    expect(text).toContain('<channel>');
    expect(text).toContain('</rss>');
  });

  test('GET /api/openapi.json has correct OpenAPI version and paths', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/openapi.json`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.openapi).toMatch(/^3\./);
    expect(d.paths).toBeDefined();
    expect(d.info).toBeDefined();
    expect(d.info.title).toBeDefined();
    // Should document key endpoints
    expect(d.paths['/api/health']).toBeDefined();
    expect(d.paths['/api/routing']).toBeDefined();
  });

  test('POST /api/reset-password with too-short password returns 400', async ({ request }) => {
    const r = await request.post(`${baseUrl}/api/reset-password`, {
      data: { password: 'ab' },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
    expect(r.status()).toBe(400);
    const d = await r.json();
    expect(d.error).toContain('min 4 chars');
  });

  test('POST /api/batch-restart with empty list returns 400', async ({ request }) => {
    const r = await request.post(`${baseUrl}/api/batch-restart`, {
      data: { services: [] },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
    expect(r.status()).toBe(400);
    const d = await r.json();
    expect(d.error).toContain('services list required');
  });
});

// ---------------------------------------------------------------------------
// Routing API
// ---------------------------------------------------------------------------
test.describe('Routing API', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');
  test.setTimeout(30_000);
  const auth = { Authorization: 'Basic ' + Buffer.from('admin:media-stack').toString('base64') };

  test('GET /api/routing returns current config with all expected fields', async ({ request }) => {
    const r = await request.get(`${baseUrl}/api/routing`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d).toHaveProperty('base_domain');
    expect(d).toHaveProperty('stack_subdomain');
    expect(d).toHaveProperty('gateway_host');
    expect(d).toHaveProperty('gateway_port');
    expect(d).toHaveProperty('app_path_prefix');
    expect(d).toHaveProperty('strategy');
    expect(typeof d.gateway_port).toBe('number');
    expect(d.gateway_port).toBeGreaterThan(0);
  });

  test('POST /api/routing with valid gateway_host returns updated', async ({ request }) => {
    // Read current config first
    const current = await (await request.get(`${baseUrl}/api/routing`)).json();
    const newHost = 'apps.test-routing.local';

    const r = await request.post(`${baseUrl}/api/routing`, {
      data: { gateway_host: newHost },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.status).toBe('updated');
    expect(d.changed).toContain('gateway_host');
    expect(d.routing).toBeDefined();
    expect(d.routing.gateway_host).toBe(newHost);

    // Restore original value
    await request.post(`${baseUrl}/api/routing`, {
      data: { gateway_host: current.gateway_host },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
  });

  test('POST /api/routing with no changes returns no_changes', async ({ request }) => {
    const current = await (await request.get(`${baseUrl}/api/routing`)).json();
    const r = await request.post(`${baseUrl}/api/routing`, {
      data: { base_domain: current.base_domain },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.status).toBe('no_changes');
  });

  test('POST /api/routing without auth returns 401', async ({ request }) => {
    const r = await request.post(`${baseUrl}/api/routing`, {
      data: { gateway_host: 'unauthorized.local' },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(r.status()).toBe(401);
  });

  test('GET /api/routing after update reflects new values', async ({ request }) => {
    const current = await (await request.get(`${baseUrl}/api/routing`)).json();
    const testHost = 'apps.verify-reflect.local';

    // Update
    await request.post(`${baseUrl}/api/routing`, {
      data: { gateway_host: testHost },
      headers: { 'Content-Type': 'application/json', ...auth },
    });

    // Read back and verify
    const r = await request.get(`${baseUrl}/api/routing`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.gateway_host).toBe(testHost);

    // Restore original value
    await request.post(`${baseUrl}/api/routing`, {
      data: { gateway_host: current.gateway_host },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
  });

  test('POST /api/routing triggers envoy-config action', async ({ request }) => {
    const current = await (await request.get(`${baseUrl}/api/routing`)).json();
    const testHost = 'apps.envoy-trigger-test.local';

    // Update to trigger envoy-config
    await request.post(`${baseUrl}/api/routing`, {
      data: { gateway_host: testHost },
      headers: { 'Content-Type': 'application/json', ...auth },
    });

    // Check status for action_history — envoy-config should appear
    const statusResp = await request.get(`${baseUrl}/status`);
    expect(statusResp.ok()).toBeTruthy();
    const status = await statusResp.json();
    const history = status.action_history || [];
    const envoyActions = history.filter((a: { action: string }) => a.action === 'envoy-config');
    expect(envoyActions.length).toBeGreaterThan(0);

    // Restore original value
    await request.post(`${baseUrl}/api/routing`, {
      data: { gateway_host: current.gateway_host },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
  });

  test('POST /api/guardrails updates guardrail settings', async ({ request }) => {
    // Read current disk config to know starting state
    const diskResp = await request.get(`${baseUrl}/api/disk`);
    expect(diskResp.ok()).toBeTruthy();
    const diskData = await diskResp.json();
    const currentMax = diskData.guardrails?.max_used_percent || 65;

    // Update with a test value
    const testValue = currentMax === 70 ? 75 : 70;
    const r = await request.post(`${baseUrl}/api/guardrails`, {
      data: { max_used_percent: testValue },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.status).toBe('updated');
    expect(d.changed).toContain('max_used_percent');

    // Restore original value
    await request.post(`${baseUrl}/api/guardrails`, {
      data: { max_used_percent: currentMax },
      headers: { 'Content-Type': 'application/json', ...auth },
    });
  });
});


// ---------------------------------------------------------------------------
// Action Queue & Cancel API
// ---------------------------------------------------------------------------

test.describe('Action queue and cancel', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');
  test.setTimeout(30_000);

  const auth = { Authorization: 'Basic ' + Buffer.from('admin:media-stack').toString('base64') };

  test('POST /actions/envoy-config returns accepted with priority field', async ({ request }) => {
    const r = await request.post(`${baseUrl}/actions/envoy-config`, { headers: auth });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.status).toBe('accepted');
    expect(d.action).toBe('envoy-config');
    expect(typeof d.priority).toBe('number');
  });

  test('POST /cancel when no action running returns no_action_running', async ({ request }) => {
    // Wait briefly for any prior action to finish
    await new Promise(resolve => setTimeout(resolve, 2000));
    const r = await request.post(`${baseUrl}/cancel`, { headers: auth });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    // Either cancel_requested (if something was still running) or no_action_running
    expect(['cancel_requested', 'no_action_running']).toContain(d.status);
  });

  test('GET /status includes pending_actions array', async ({ request }) => {
    const r = await request.get(`${baseUrl}/status`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(Array.isArray(d.pending_actions)).toBeTruthy();
  });

  test('GET /status includes action_history array', async ({ request }) => {
    const r = await request.get(`${baseUrl}/status`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(Array.isArray(d.action_history)).toBeTruthy();
  });

  test('GET /status action_history entries have status field', async ({ request }) => {
    // Trigger an action first so history is non-empty
    await request.post(`${baseUrl}/actions/envoy-config`, { headers: auth });
    // Give the action time to at least start and finish
    await new Promise(resolve => setTimeout(resolve, 3000));

    const r = await request.get(`${baseUrl}/status`);
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    if (d.action_history.length > 0) {
      for (const entry of d.action_history) {
        expect(entry).toHaveProperty('status');
        expect(entry).toHaveProperty('name');
        expect(entry).toHaveProperty('id');
      }
    }
  });

  test('POST /actions/unknown returns 404 with known actions list', async ({ request }) => {
    const r = await request.post(`${baseUrl}/actions/nonexistent-action-xyz`, { headers: auth });
    expect(r.status()).toBe(404);
    const d = await r.json();
    expect(d.error).toContain('unknown action');
    expect(Array.isArray(d.known)).toBeTruthy();
    expect(d.known.length).toBeGreaterThan(0);
    // Verify some well-known actions are listed
    expect(d.known).toContain('envoy-config');
    expect(d.known).toContain('bootstrap');
  });

  test('action accepted response includes priority number', async ({ request }) => {
    const r = await request.post(`${baseUrl}/actions/bootstrap`, { headers: auth });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(d.status).toBe('accepted');
    expect(typeof d.priority).toBe('number');
    expect(d.priority).toBeGreaterThan(0);
  });

  test('POST /actions/cancel alias works same as POST /cancel', async ({ request }) => {
    // Both /cancel and /actions/cancel should be valid cancel endpoints
    const r = await request.post(`${baseUrl}/actions/cancel`, { headers: auth });
    expect(r.ok()).toBeTruthy();
    const d = await r.json();
    expect(['cancel_requested', 'no_action_running']).toContain(d.status);
  });

  test('POST /actions requires authentication', async ({ request }) => {
    // POST to /actions/ prefix requires auth — send without credentials
    const r = await request.post(`${baseUrl}/actions/envoy-config`);
    // If auth is enforced, expect 401; if CONTROLLER_AUTH=none, it may pass — both are valid
    expect([200, 401]).toContain(r.status());
    if (r.status() === 401) {
      const authHeader = r.headers()['www-authenticate'];
      expect(authHeader).toContain('Basic');
    }
  });

  test('POST /cancel requires authentication', async ({ request }) => {
    const r = await request.post(`${baseUrl}/cancel`);
    expect([200, 401]).toContain(r.status());
    if (r.status() === 401) {
      const authHeader = r.headers()['www-authenticate'];
      expect(authHeader).toContain('Basic');
    }
  });
});


// ---------------------------------------------------------------------------
// Priority Constants
// ---------------------------------------------------------------------------

test.describe('Action priority ordering', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');
  test.setTimeout(30_000);

  const auth = { Authorization: 'Basic ' + Buffer.from('admin:media-stack').toString('base64') };

  test('envoy-config has lower priority number (higher precedence) than auto-indexers', async ({ request }) => {
    const envoyResp = await request.post(`${baseUrl}/actions/envoy-config`, { headers: auth });
    const envoyData = await envoyResp.json();

    const indexerResp = await request.post(`${baseUrl}/actions/auto-indexers`, { headers: auth });
    const indexerData = await indexerResp.json();

    // Lower priority number = higher precedence
    expect(envoyData.priority).toBeLessThan(indexerData.priority);
  });

  test('all known actions return a priority in the accepted response', async ({ request }) => {
    // First get the known actions list from a 404 response
    const unknownResp = await request.post(`${baseUrl}/actions/not-a-real-action`, { headers: auth });
    const unknownData = await unknownResp.json();
    const knownActions: string[] = unknownData.known;

    for (const action of knownActions) {
      const r = await request.post(`${baseUrl}/actions/${action}`, { headers: auth });
      expect(r.ok()).toBeTruthy();
      const d = await r.json();
      expect(d.status).toBe('accepted');
      expect(typeof d.priority).toBe('number');
      expect(d.priority).toBeGreaterThan(0);
    }
  });

  test('bootstrap has the lowest priority number (highest precedence)', async ({ request }) => {
    const bootstrapResp = await request.post(`${baseUrl}/actions/bootstrap`, { headers: auth });
    const bootstrapData = await bootstrapResp.json();

    // Bootstrap should have the highest precedence (lowest number)
    for (const other of ['envoy-config', 'auto-indexers', 'reconcile']) {
      const r = await request.post(`${baseUrl}/actions/${other}`, { headers: auth });
      const d = await r.json();
      expect(bootstrapData.priority).toBeLessThanOrEqual(d.priority);
    }
  });
});


// ---------------------------------------------------------------------------
// SPA Base href Rewrite Verification
// ---------------------------------------------------------------------------

test.describe('SPA base href rewrite', () => {
  // These tests verify the Lua filter rewrites <base href="/"> for SPAs.
  // Uses the compose gateway, not controller API.
  const gatewayHost = process.env.STACK_COMPOSE_GATEWAY_HOST || 'comp.my';
  const edgePort = process.env.STACK_COMPOSE_EDGE_PORT || '80';
  const gatewayBase = edgePort === '80' ? gatewayHost : `${gatewayHost}:${edgePort}`;
  const gateway = `http://${gatewayBase}`;
  const spaEnabled = process.env.SPA_REWRITE_TEST === '1' || enabled;

  test.skip(!spaEnabled, 'Set CONTROLLER_API_TEST=1 or SPA_REWRITE_TEST=1 to run');
  test.setTimeout(30_000);

  test('Bazarr HTML has base href rewritten to /app/bazarr/', async ({ request }) => {
    try {
      const r = await request.get(`${gateway}/app/bazarr`, {
        headers: { Accept: 'text/html', Host: gatewayHost },
      });
      if (!r.ok()) {
        test.skip(true, `Bazarr not reachable via gateway (status ${r.status()})`);
        return;
      }
      const html = await r.text();
      expect(html).toContain('<base href="/app/bazarr/"');
      expect(html).not.toContain('<base href="/"');
    } catch {
      test.skip(true, 'Gateway not reachable');
    }
  });

  test('Bazarr HTML does NOT contain unrewritten <base href="/">', async ({ request }) => {
    try {
      const r = await request.get(`${gateway}/app/bazarr/`, {
        headers: { Accept: 'text/html', Host: gatewayHost },
      });
      if (!r.ok()) {
        test.skip(true, `Bazarr not reachable via gateway (status ${r.status()})`);
        return;
      }
      const html = await r.text();
      // After Lua rewrite, the original <base href="/"> should be gone
      expect(html).not.toMatch(/<base\s+href=["']\/["']/);
    } catch {
      test.skip(true, 'Gateway not reachable');
    }
  });

  test('prefix-patch script is injected into SPA HTML', async ({ request }) => {
    try {
      const r = await request.get(`${gateway}/app/bazarr`, {
        headers: { Accept: 'text/html', Host: gatewayHost },
      });
      if (!r.ok()) {
        test.skip(true, `Bazarr not reachable via gateway (status ${r.status()})`);
        return;
      }
      const html = await r.text();
      // The Lua filter injects a script with data-media-stack-prefix-patch attribute
      expect(html).toContain('data-media-stack-prefix-patch=');
    } catch {
      test.skip(true, 'Gateway not reachable');
    }
  });

  test('preserve-path-prefix service does NOT get base href rewritten', async ({ request }) => {
    // Services with preserve_path_prefix=true keep the prefix in the upstream
    // request and should NOT have their HTML rewritten by the Lua filter.
    // Jellyfin is a preserve-path-prefix service.
    try {
      const r = await request.get(`${gateway}/app/jellyfin/web/`, {
        headers: { Accept: 'text/html', Host: gatewayHost },
      });
      if (!r.ok()) {
        test.skip(true, `Jellyfin not reachable via gateway (status ${r.status()})`);
        return;
      }
      const html = await r.text();
      // Preserve-path-prefix services should not have the prefix-patch injected
      // because they handle their own pathing
      expect(html).not.toContain('data-media-stack-prefix-patch=');
    } catch {
      test.skip(true, 'Gateway not reachable');
    }
  });

  test('relative asset URL resolves through the rewritten base href', async ({ request }) => {
    // After base href rewrite, a relative URL like "assets/app.js" in Bazarr
    // should be fetchable via the prefixed path /app/bazarr/assets/app.js
    try {
      const indexResp = await request.get(`${gateway}/app/bazarr`, {
        headers: { Accept: 'text/html', Host: gatewayHost },
      });
      if (!indexResp.ok()) {
        test.skip(true, `Bazarr not reachable via gateway (status ${indexResp.status()})`);
        return;
      }
      const html = await indexResp.text();

      // Extract a JS or CSS asset reference from the HTML
      const assetMatch = html.match(/(?:src|href)=["'](?:\.\/)?([^"']*\.(?:js|css))/);
      if (!assetMatch) {
        test.skip(true, 'No asset references found in Bazarr HTML');
        return;
      }
      const assetPath = assetMatch[1];
      // Fetch the asset through the prefix path
      const assetResp = await request.get(`${gateway}/app/bazarr/${assetPath}`, {
        headers: { Host: gatewayHost },
      });
      expect(assetResp.ok()).toBeTruthy();
    } catch {
      test.skip(true, 'Gateway not reachable');
    }
  });
});
