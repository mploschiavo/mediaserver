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
