/**
 * Accessibility tests for the 5 session-visibility tabs.
 *
 * Uses @axe-core/playwright to check each tab against WCAG 2.1 AA +
 * best-practices rules. Target: zero violations on the tab shells.
 *
 * Required env vars:
 *   CONTROLLER_API_TEST=1
 *   CONTROLLER_URL             — default 127.0.0.1:9100
 *
 * Install (once) in tests/e2e/playwright:
 *   npm i --save-dev @axe-core/playwright axe-core
 *
 * Each test runs the tab in isolation (inject script → render root →
 * run axe). The goal is to keep the tab-shell markup a11y-clean
 * independent of whatever the live fetch response adds at runtime;
 * for that we mock the fetch with a canonical happy-path payload.
 */
import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const enabled = process.env.CONTROLLER_API_TEST === '1';
const baseUrl = process.env.CONTROLLER_URL || 'http://127.0.0.1:9100';

// Shared a11y config — WCAG 2.1 AA + best-practices. Colour-contrast
// tolerances rely on the production CSS; the tab-shell markup under
// test here uses semantic elements so contrast should follow.
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'];

// Helper: prepare a root div and run the tab's render function.
async function mountTab(page: any, scriptUrl: string, rootId: string,
                       renderFn: string) {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.addScriptTag({ url: scriptUrl });
  await page.evaluate(
    (args: { rootId: string; renderFn: string }) => {
      const root = document.createElement('div');
      root.id = args.rootId;
      document.body.appendChild(root);
      (window as any)[args.renderFn](root);
    },
    { rootId, renderFn },
  );
  // Allow async fetches a beat to settle.
  await page.waitForTimeout(300);
}

async function runAxe(page: any, rootSelector: string) {
  return new AxeBuilder({ page })
    .include(rootSelector)
    .withTags(AXE_TAGS)
    .analyze();
}

test.describe('Session-visibility tabs — accessibility', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');

  test.use({
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  });

  test('sessions tab has zero axe violations (empty state)', async ({ page }) => {
    await page.route('**/api/sessions/active', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [] }),
      }),
    );
    await mountTab(page, '/static/tab_sessions.js',
                   'tab-sessions', 'renderSessionsTab');
    const results = await runAxe(page, '#tab-sessions');
    expect(results.violations).toEqual([]);
  });

  test('sessions tab has zero axe violations (populated)', async ({ page }) => {
    await page.route('**/api/sessions/active', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          sessions: [
            {
              provider: 'controller', session_id: 's1',
              username: 'alice', device_class: 'DESKTOP',
              client_ip: '1.2.3.4', first_seen_ip: true,
              connected_since: '2026-04-24T00:00:00Z',
              last_activity: '2026-04-24T10:00:00Z',
              revokable: true, device: 'Chrome', client: 'Web',
            },
          ],
        }),
      }),
    );
    await mountTab(page, '/static/tab_sessions.js',
                   'tab-sessions', 'renderSessionsTab');
    const results = await runAxe(page, '#tab-sessions');
    expect(results.violations).toEqual([]);
  });

  test('security tab has zero axe violations', async ({ page }) => {
    const empty = JSON.stringify({ clusters: [], alerts: [] });
    await page.route('**/api/security/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: empty }));
    await mountTab(page, '/static/tab_security.js',
                   'tab-security', 'renderSecurityTab');
    const results = await runAxe(page, '#tab-security');
    expect(results.violations).toEqual([]);
  });

  test('bans tab has zero axe violations', async ({ page }) => {
    await page.route('**/api/bans/**', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ bans: [] }),
      }),
    );
    await mountTab(page, '/static/tab_bans.js',
                   'tab-bans', 'renderBansTab');
    const results = await runAxe(page, '#tab-bans');
    expect(results.violations).toEqual([]);
  });

  test('my-security tab has zero axe violations', async ({ page }) => {
    await page.route('**/api/me/mfa-state', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ enrolled: false, enrolled_methods: [] }),
      }),
    );
    await page.route('**/api/me/sessions', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ sessions: [], current_session_id: '' }),
      }),
    );
    await page.route('**/api/me/login-history', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ entries: [] }),
      }),
    );
    await page.route('**/api/me/tokens', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ tokens: [] }),
      }),
    );
    await mountTab(page, '/static/tab_me_security.js',
                   'tab-me-security', 'renderMeSecurityTab');
    const results = await runAxe(page, '#tab-me-security');
    expect(results.violations).toEqual([]);
  });

  test('emergency-revoke tab has zero axe violations (initial)', async ({ page }) => {
    await mountTab(page, '/static/tab_emergency_revoke.js',
                   'tab-emergency-revoke', 'renderEmergencyRevokeTab');
    const results = await runAxe(page, '#tab-emergency-revoke');
    expect(results.violations).toEqual([]);
  });

  test('emergency-revoke dialog has zero axe violations', async ({ page }) => {
    await mountTab(page, '/static/tab_emergency_revoke.js',
                   'tab-emergency-revoke', 'renderEmergencyRevokeTab');
    await page.locator('#tab-emergency-revoke button.ms-danger-btn').click();
    const results = await runAxe(page, '#tab-emergency-revoke');
    expect(results.violations).toEqual([]);
  });
});
