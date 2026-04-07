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
    // Envoy should serve app routes on localhost:80
    const r = await request.get('http://127.0.0.1:80/app/homepage', {
      maxRedirects: 0,
    });
    // 200 or 301/302 redirect means Envoy is routing
    expect(r.status()).toBeLessThan(404);
  });
});
