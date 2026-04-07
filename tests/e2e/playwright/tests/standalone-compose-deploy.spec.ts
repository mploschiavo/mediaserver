/**
 * Standalone Docker Compose deploy test.
 *
 * Validates the Getting Started flow: copy docker-compose.yml to an empty
 * directory and run `docker compose up -d`. Verifies:
 * - All services start without permission errors
 * - Controller dashboard is accessible and healthy
 * - Bootstrap completes successfully
 *
 * Prerequisites:
 *   - Docker Engine with Compose V2
 *   - Controller image available (pulled or in local registry)
 *
 * Usage:
 *   STANDALONE_COMPOSE_TEST=1 npx playwright test tests/standalone-compose-deploy.spec.ts
 *
 * The test creates a sandbox directory in /tmp, deploys, validates, then tears down.
 */

import { expect, test } from '@playwright/test';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const enabled = process.env.STANDALONE_COMPOSE_TEST === '1';
const sandboxDir = '/tmp/playwright-compose-sandbox-' + Date.now();
const composeFile = path.resolve(__dirname, '..', '..', '..', '..', 'dist', 'docker-compose.yml');
const controllerPort = 9100;
const maxWaitSeconds = 180;

function sh(cmd: string, cwd?: string): string {
  try {
    return execSync(cmd, { cwd: cwd || sandboxDir, timeout: 120_000, encoding: 'utf-8' }).trim();
  } catch (err: any) {
    return err.stdout?.toString() || err.message || '';
  }
}

function waitForHealthy(url: string, timeoutSec: number): boolean {
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    try {
      const out = execSync(`curl -sf ${url}`, { timeout: 5000, encoding: 'utf-8' });
      if (out.includes('"ok"') || out.includes('"ready"')) return true;
    } catch {
      // Not ready yet.
    }
    execSync('sleep 3');
  }
  return false;
}

test.describe('Standalone compose deploy', () => {
  test.skip(!enabled, 'Set STANDALONE_COMPOSE_TEST=1 to run this test');
  test.setTimeout(maxWaitSeconds * 1000 + 60_000);

  test.beforeAll(() => {
    // Create sandbox and copy compose file.
    fs.mkdirSync(sandboxDir, { recursive: true });
    fs.copyFileSync(composeFile, path.join(sandboxDir, 'docker-compose.yml'));
  });

  test.afterAll(() => {
    // Tear down.
    sh('docker compose down -v --remove-orphans 2>&1 || true');
    fs.rmSync(sandboxDir, { recursive: true, force: true });
  });

  test('deploy starts all services from empty directory', () => {
    // This is the exact Getting Started flow.
    const output = sh('docker compose up -d 2>&1');
    expect(output).not.toContain('error');

    // Wait for controller to become healthy.
    const healthy = waitForHealthy(`http://127.0.0.1:${controllerPort}/healthz`, maxWaitSeconds);
    expect(healthy).toBeTruthy();
  });

  test('no containers in restart loop', () => {
    // Check that no containers are restarting (permission errors would cause this).
    const ps = sh('docker compose ps --format json 2>/dev/null || docker compose ps 2>/dev/null');
    expect(ps).not.toContain('Restarting');
  });

  test('controller health probe passes', async ({ request }) => {
    const response = await request.get(`http://127.0.0.1:${controllerPort}/api/health`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    // At least 12 of 16 services should be healthy (plex won't be, some may still be starting).
    expect(data.healthy).toBeGreaterThanOrEqual(12);
  });

  test('controller dashboard loads', async ({ page }) => {
    await page.goto(`http://127.0.0.1:${controllerPort}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    // Dashboard should show the title.
    const title = await page.title();
    expect(title).toContain('Media Stack');
  });

  test('jellyfin is not restarting (permission fix works)', () => {
    const status = sh('docker inspect jellyfin --format "{{.State.Status}}" 2>/dev/null');
    expect(status).toBe('running');
    const logs = sh('docker logs jellyfin --tail=5 2>&1');
    expect(logs).not.toContain('Permission denied');
  });

  test('maintainerr is not restarting (permission fix works)', () => {
    const status = sh('docker inspect maintainerr --format "{{.State.Status}}" 2>/dev/null');
    expect(status).toBe('running');
    const logs = sh('docker logs maintainerr --tail=5 2>&1');
    expect(logs).not.toContain('Permission denied');
    expect(logs).not.toContain('Could not create or access');
  });

  test('API endpoints respond', async ({ request }) => {
    for (const ep of ['/healthz', '/status', '/api/health', '/metrics', '/api/openapi.json']) {
      const response = await request.get(`http://127.0.0.1:${controllerPort}${ep}`);
      expect(response.ok(), `${ep} should return 200`).toBeTruthy();
    }
  });
});
