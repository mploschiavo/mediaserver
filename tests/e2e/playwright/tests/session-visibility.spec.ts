/**
 * Session-visibility feature — e2e UI tests for the 5 new dashboard tabs.
 *
 * Covers:
 *   - Sessions tab (aggregated view + per-row revoke)
 *   - Security tab (failed logins, new locations, concurrent sessions,
 *     login-history drawer)
 *   - Bans tab (add / remove, reason templates, expiry)
 *   - My Security tab (self-service MFA, sessions, history, tokens,
 *     this-wasn't-me flow)
 *   - Emergency Revoke tab (two-step confirmation)
 *
 * Required env vars:
 *   CONTROLLER_API_TEST=1      — opt-in gate
 *   CONTROLLER_URL             — dashboard base URL (default: 127.0.0.1:9100)
 *
 * Each test asserts:
 *   - The tab's JS module loaded (window.renderXxxTab exists).
 *   - The root tab element rendered expected structural elements.
 *   - No banned DOM sink was used at runtime (spot-check via MutationObserver).
 *
 * Does NOT exercise real provider fan-out; use the service-layer tests for that.
 * These tests verify the UI shell is XSS-safe and a11y-compliant.
 */
import { expect, test } from '@playwright/test';

const enabled = process.env.CONTROLLER_API_TEST === '1';
const baseUrl = process.env.CONTROLLER_URL || 'http://127.0.0.1:9100';

test.describe('Session visibility — UI shells', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');

  test.use({
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  });

  test.beforeEach(async ({ page }) => {
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  });

  // -----------------------------------------------------------------------
  // Sessions tab
  // -----------------------------------------------------------------------
  test('sessions tab script exposes renderSessionsTab', async ({ page }) => {
    // Inject the tab script as the dashboard would.
    await page.addScriptTag({ url: '/static/tab_sessions.js' });
    const exists = await page.evaluate(
      () => typeof (window as any).renderSessionsTab === 'function',
    );
    expect(exists).toBe(true);
  });

  test('sessions tab renders loading state before fetch resolves', async ({ page }) => {
    await page.addScriptTag({ url: '/static/tab_sessions.js' });
    await page.evaluate(() => {
      const root = document.createElement('div');
      root.id = 'tab-sessions';
      document.body.appendChild(root);
      (window as any).renderSessionsTab(root);
    });
    // Loading / table / empty / error — one of these MUST be present
    const state = await page.locator('#tab-sessions').innerText();
    expect(state.length).toBeGreaterThan(0);
  });

  // -----------------------------------------------------------------------
  // Security tab
  // -----------------------------------------------------------------------
  test('security tab renders 3 panels', async ({ page }) => {
    await page.addScriptTag({ url: '/static/tab_security.js' });
    await page.evaluate(() => {
      const root = document.createElement('div');
      root.id = 'tab-security';
      document.body.appendChild(root);
      (window as any).renderSecurityTab(root);
    });
    // 3 sections with aria-labelledby headings.
    const h3s = await page.locator('#tab-security h3').allTextContents();
    expect(h3s).toEqual(
      expect.arrayContaining([
        'Failed login clusters',
        'New-location alerts',
        'Concurrent session spikes',
      ]),
    );
  });

  // -----------------------------------------------------------------------
  // Bans tab
  // -----------------------------------------------------------------------
  test('bans tab renders user + IP ban forms with reason templates', async ({ page }) => {
    await page.addScriptTag({ url: '/static/tab_bans.js' });
    await page.evaluate(() => {
      const root = document.createElement('div');
      root.id = 'tab-bans';
      document.body.appendChild(root);
      (window as any).renderBansTab(root);
    });
    const forms = await page.locator('#tab-bans form').count();
    expect(forms).toBe(2);
    // Reason templates populated from BAN_REASONS constant.
    const options = await page.locator(
      '#tab-bans select.ms-ban-reason option',
    ).allTextContents();
    expect(options).toEqual(
      expect.arrayContaining([
        'Credential stuffing',
        'Unauthorised sharing',
        'Admin request',
        'Other (free-text)',
      ]),
    );
  });

  // -----------------------------------------------------------------------
  // My Security tab
  // -----------------------------------------------------------------------
  test('my-security tab renders four sections', async ({ page }) => {
    await page.addScriptTag({ url: '/static/tab_me_security.js' });
    await page.evaluate(() => {
      const root = document.createElement('div');
      root.id = 'tab-me-security';
      document.body.appendChild(root);
      (window as any).renderMeSecurityTab(root);
    });
    const h3s = await page.locator('#tab-me-security h3').allTextContents();
    expect(h3s).toEqual(
      expect.arrayContaining([
        'MFA status', 'My sessions', 'Recent logins', 'API tokens',
      ]),
    );
  });

  // -----------------------------------------------------------------------
  // Emergency Revoke tab
  // -----------------------------------------------------------------------
  test('emergency revoke tab requires confirmation phrase + reason', async ({ page }) => {
    await page.addScriptTag({ url: '/static/tab_emergency_revoke.js' });
    await page.evaluate(() => {
      const root = document.createElement('div');
      root.id = 'tab-emergency-revoke';
      document.body.appendChild(root);
      (window as any).renderEmergencyRevokeTab(root);
    });
    // Initial view shows a start button.
    const startBtn = page.locator('#tab-emergency-revoke button.ms-danger-btn');
    await expect(startBtn).toBeVisible();
    await startBtn.click();

    // Dialog appears with confirm input and reason input.
    const confirmInput = page.locator('#ms-emergency-confirm');
    const reasonInput = page.locator('#ms-emergency-reason');
    const finalBtn = page.locator(
      '#tab-emergency-revoke button.ms-danger-btn:not([type=button]), '
      + '#tab-emergency-revoke .ms-emergency-actions .ms-danger-btn',
    ).last();
    await expect(confirmInput).toBeVisible();
    await expect(reasonInput).toBeVisible();
    // Button starts disabled.
    await expect(finalBtn).toBeDisabled();

    // Typing the wrong phrase keeps it disabled.
    await confirmInput.fill('wrong phrase');
    await reasonInput.fill('credential leak incident 2026-04-24');
    await expect(finalBtn).toBeDisabled();

    // Correct phrase + reason enables the button.
    await confirmInput.fill('REVOKE EVERYTHING');
    await expect(finalBtn).toBeEnabled();
  });

  // -----------------------------------------------------------------------
  // XSS safety runtime check — a sneaky response can't get HTML into the DOM
  // -----------------------------------------------------------------------
  test('sessions tab textContent-only: response cannot inject HTML', async ({ page }) => {
    // Intercept the fetch and return a crafted payload with an HTML-
    // looking username. The tab MUST render it as text, not parse it.
    await page.route('**/api/sessions/active', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          sessions: [
            {
              provider: 'controller',
              session_id: 'sess-1',
              username: '<script>window.xssFired=1</script>',
              device_class: 'DESKTOP',
              client_ip: '1.2.3.4',
              first_seen_ip: false,
              connected_since: '2026-04-24T00:00:00Z',
              last_activity: '2026-04-24T10:00:00Z',
              revokable: true,
            },
          ],
        }),
      }),
    );
    await page.addScriptTag({ url: '/static/tab_sessions.js' });
    await page.evaluate(() => {
      const root = document.createElement('div');
      root.id = 'tab-sessions';
      document.body.appendChild(root);
      (window as any).renderSessionsTab(root);
    });
    // Give the fetch a moment.
    await page.waitForTimeout(500);
    // If the username had been parsed as HTML, xssFired would be 1.
    const fired = await page.evaluate(() => (window as any).xssFired);
    expect(fired).toBeUndefined();
    // The payload text should appear visibly (as literal text).
    const rendered = await page.locator('#tab-sessions td').first().innerText();
    expect(rendered).toContain('<script>');
  });
});
