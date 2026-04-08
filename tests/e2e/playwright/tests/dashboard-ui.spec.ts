/**
 * Dashboard UI E2E tests for the Media Stack Controller.
 *
 * These tests run a real Chromium browser against the controller dashboard
 * and verify that key UI elements render, tab navigation works, action
 * buttons trigger confirm dialogs, and theme toggling functions correctly.
 *
 * Required env vars:
 *   CONTROLLER_API_TEST=1      — opt-in gate (tests skip otherwise)
 *   CONTROLLER_URL             — dashboard base URL (default: http://127.0.0.1:9100)
 */
import { expect, test } from '@playwright/test';

const enabled = process.env.CONTROLLER_API_TEST === '1';
const baseUrl = process.env.CONTROLLER_URL || 'http://127.0.0.1:9100';

test.describe('Dashboard UI', () => {
  test.skip(!enabled, 'Set CONTROLLER_API_TEST=1 to run');

  test.use({
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  });

  // Navigate to the dashboard before each test.
  test.beforeEach(async ({ page }) => {
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 15_000 });
  });

  // -----------------------------------------------------------------------
  // 1. Dashboard loads and shows phase badge
  // -----------------------------------------------------------------------
  test('dashboard loads and shows phase badge', async ({ page }) => {
    const badge = page.locator('#hbadge');
    await expect(badge).toBeVisible();
    // Badge text is set by JS to the phase (IDLE, RUNNING, COMPLETE, ERROR).
    await expect(badge).not.toHaveText('...');
  });

  // -----------------------------------------------------------------------
  // 2. Status card contains phase text (idle/running/complete)
  // -----------------------------------------------------------------------
  test('status card contains phase text', async ({ page }) => {
    const statusCard = page.locator('#status-card');
    await expect(statusCard).toBeVisible();
    // The status card renders a .phase element with one of the known states.
    const phase = statusCard.locator('.phase');
    await expect(phase).toBeVisible();
    const text = await phase.textContent();
    expect(
      ['idle', 'running', 'complete', 'error'].some((s) => text!.toLowerCase().includes(s)),
      `Phase text "${text}" should contain idle, running, complete, or error`,
    ).toBe(true);
  });

  // -----------------------------------------------------------------------
  // 3. Action history table is visible with column headers
  // -----------------------------------------------------------------------
  test('action history card renders with column headers', async ({ page }) => {
    // The history card may be hidden if there is no history; verify the
    // card element exists and if visible, check for expected table headers.
    const histCard = page.locator('#hist-card');
    // Wait briefly for status data to load and render history.
    await page.waitForTimeout(2000);
    const isVisible = await histCard.isVisible();
    if (isVisible) {
      const headers = histCard.locator('th');
      const headerTexts = await headers.allTextContents();
      const joined = headerTexts.join(' ').toLowerCase();
      expect(joined).toContain('action');
      expect(joined).toContain('status');
      expect(joined).toContain('duration');
    } else {
      // No history yet — the card is correctly hidden.
      await expect(histCard).toBeHidden();
    }
  });

  // -----------------------------------------------------------------------
  // 4. Action buttons section has expected buttons
  // -----------------------------------------------------------------------
  test('action buttons section has expected buttons', async ({ page }) => {
    const actionCard = page.locator('#action-buttons');
    await expect(actionCard).toBeVisible();
    // Check for known action button labels.
    const buttons = actionCard.locator('button');
    const allText = (await buttons.allTextContents()).join(' ');
    expect(allText).toContain('Configure All');
    expect(allText).toContain('Discover Indexers');
    expect(allText).toContain('Rebuild Routing');
  });

  // -----------------------------------------------------------------------
  // 5. Tab navigation: Logs tab shows log content area
  // -----------------------------------------------------------------------
  test('tab navigation: Logs tab shows log content area', async ({ page }) => {
    const logsTab = page.locator('#main-tabs button', { hasText: 'Logs' });
    await logsTab.click();
    const logsPanel = page.locator('#tab-logs');
    await expect(logsPanel).toBeVisible();
    // The log container should exist.
    await expect(page.locator('#logs')).toBeVisible();
  });

  // -----------------------------------------------------------------------
  // 6. Tab navigation: Content tab shows content area
  // -----------------------------------------------------------------------
  test('tab navigation: Content tab shows content area', async ({ page }) => {
    const contentTab = page.locator('#main-tabs button', { hasText: 'Content' });
    await contentTab.click();
    const contentPanel = page.locator('#tab-content');
    await expect(contentPanel).toBeVisible();
    // Libraries subtab should be the default active subtab.
    await expect(page.locator('#lib-libraries')).toBeVisible();
  });

  // -----------------------------------------------------------------------
  // 7. Tab navigation: Routing tab shows routing config
  // -----------------------------------------------------------------------
  test('tab navigation: Routing tab shows routing config', async ({ page }) => {
    const routingTab = page.locator('#main-tabs button', { hasText: 'Routing' });
    await routingTab.click();
    const routingPanel = page.locator('#tab-routing');
    await expect(routingPanel).toBeVisible();
    // DNS/routing entries should render.
    await expect(page.locator('#dns-entries')).toBeVisible();
  });

  // -----------------------------------------------------------------------
  // 8. Tab navigation: Ops tab shows operations
  // -----------------------------------------------------------------------
  test('tab navigation: Ops tab shows operations', async ({ page }) => {
    const opsTab = page.locator('#main-tabs button', { hasText: 'Ops' });
    await opsTab.click();
    const opsPanel = page.locator('#tab-ops');
    await expect(opsPanel).toBeVisible();
    // Health SLA subtab is the default.
    await expect(page.locator('#ops-sla')).toBeVisible();
  });

  // -----------------------------------------------------------------------
  // 9. Tab navigation: Config tab shows profile
  // -----------------------------------------------------------------------
  test('tab navigation: Config tab shows profile view', async ({ page }) => {
    const configTab = page.locator('#main-tabs button', { hasText: 'Config' });
    await configTab.click();
    const configPanel = page.locator('#tab-profile');
    await expect(configPanel).toBeVisible();
    await expect(page.locator('#cfg-profile')).toBeVisible();
  });

  // -----------------------------------------------------------------------
  // 10. Tab navigation: Alerts tab shows webhooks
  // -----------------------------------------------------------------------
  test('tab navigation: Alerts tab shows webhooks section', async ({ page }) => {
    const alertsTab = page.locator('#main-tabs button', { hasText: 'Alerts' });
    await alertsTab.click();
    const alertsPanel = page.locator('#tab-webhooks');
    await expect(alertsPanel).toBeVisible();
    // Webhook URL input should be present.
    await expect(page.locator('#webhookUrl')).toBeVisible();
  });

  // -----------------------------------------------------------------------
  // 11. Cancel button appears when action is running
  // -----------------------------------------------------------------------
  test('cancel button appears in status card when action is running', async ({ page }) => {
    // Check the status card for a running action with cancel button.
    const statusCard = page.locator('#status-card');
    await expect(statusCard).toBeVisible();
    await page.waitForTimeout(2000);
    const phase = statusCard.locator('.phase');
    const phaseText = (await phase.textContent()) || '';
    if (phaseText.toLowerCase() === 'running') {
      // When running, a Cancel button should be present.
      const cancelBtn = statusCard.locator('button', { hasText: 'Cancel' });
      await expect(cancelBtn).toBeVisible();
    } else {
      // Not running — cancel button should not be present.
      const cancelBtn = statusCard.locator('button', { hasText: 'Cancel' });
      await expect(cancelBtn).toHaveCount(0);
    }
  });

  // -----------------------------------------------------------------------
  // 12. History table shows Status column
  // -----------------------------------------------------------------------
  test('history table shows Status column when history exists', async ({ page }) => {
    await page.waitForTimeout(2000);
    const histCard = page.locator('#hist-card');
    const isVisible = await histCard.isVisible();
    if (isVisible) {
      const statusHeader = histCard.locator('th', { hasText: 'Status' });
      await expect(statusHeader).toBeVisible();
    } else {
      // No history — card is hidden, which is valid.
      await expect(histCard).toBeHidden();
    }
  });

  // -----------------------------------------------------------------------
  // 13. Action history entries have status dots (ok/err/warn)
  // -----------------------------------------------------------------------
  test('action history entries have status dots', async ({ page }) => {
    await page.waitForTimeout(2000);
    const histCard = page.locator('#hist-card');
    const isVisible = await histCard.isVisible();
    if (isVisible) {
      // Each row has a colored dot indicating status.
      const dots = histCard.locator('tbody .dot');
      const count = await dots.count();
      expect(count, 'History rows should have status dots').toBeGreaterThan(0);
      // Each dot should have one of the status classes.
      for (let i = 0; i < Math.min(count, 5); i++) {
        const cls = await dots.nth(i).getAttribute('class');
        expect(
          cls,
          `Dot class "${cls}" should contain ok, err, or warn`,
        ).toMatch(/ok|err|warn/);
      }
    }
  });

  // -----------------------------------------------------------------------
  // 14. Confirm dialog appears when clicking an action button
  // -----------------------------------------------------------------------
  test('confirm dialog appears when clicking action button', async ({ page }) => {
    // Click the "Configure All" button to trigger the confirm overlay.
    const configureBtn = page.locator('#action-buttons button', { hasText: 'Configure All' });
    await expect(configureBtn).toBeVisible();
    await configureBtn.click();
    // The confirm overlay should appear.
    const overlay = page.locator('.confirm-overlay');
    await expect(overlay).toBeVisible();
    const confirmBox = page.locator('.confirm-box');
    await expect(confirmBox).toBeVisible();
    // Should show the action title.
    const title = confirmBox.locator('h3');
    await expect(title).toHaveText('Configure All');
    // Should have Cancel and Run buttons.
    await expect(confirmBox.locator('button', { hasText: 'Cancel' })).toBeVisible();
    await expect(confirmBox.locator('button', { hasText: 'Run' })).toBeVisible();
  });

  // -----------------------------------------------------------------------
  // 15. Confirm dialog can be dismissed with Cancel
  // -----------------------------------------------------------------------
  test('confirm dialog can be dismissed with Cancel', async ({ page }) => {
    // Open the confirm dialog.
    const discoverBtn = page.locator('#action-buttons button', { hasText: 'Discover Indexers' });
    await expect(discoverBtn).toBeVisible();
    await discoverBtn.click();
    const overlay = page.locator('.confirm-overlay');
    await expect(overlay).toBeVisible();
    // Click Cancel to dismiss.
    const cancelBtn = page.locator('.confirm-box button', { hasText: 'Cancel' });
    await cancelBtn.click();
    // Overlay should be gone (confirm-root emptied).
    await expect(overlay).toBeHidden();
  });

  // -----------------------------------------------------------------------
  // 16. Dark/light mode toggle works
  // -----------------------------------------------------------------------
  test('dark/light mode toggle works', async ({ page }) => {
    const themeBtn = page.locator('#themeBtn');
    await expect(themeBtn).toBeVisible();
    // Default is dark mode — button says "Light".
    const initialText = await themeBtn.textContent();
    // Click to toggle.
    await themeBtn.click();
    // After toggle, button text should change.
    const newText = await themeBtn.textContent();
    expect(newText).not.toBe(initialText);
    // Body class should reflect the theme.
    if (newText === 'Dark') {
      await expect(page.locator('body')).toHaveClass(/light/);
    } else {
      // Toggled back to dark — body should not have 'light' class.
      const bodyClass = await page.locator('body').getAttribute('class');
      expect(bodyClass || '').not.toContain('light');
    }
    // Toggle back to original state.
    await themeBtn.click();
    const restoredText = await themeBtn.textContent();
    expect(restoredText).toBe(initialText);
  });

  // -----------------------------------------------------------------------
  // 17. Dashboard title shows "Media Stack Controller"
  // -----------------------------------------------------------------------
  test('dashboard title shows "Media Stack Controller"', async ({ page }) => {
    const heading = page.locator('header h1');
    await expect(heading).toBeVisible();
    const text = await heading.textContent();
    expect(text).toContain('Media Stack Controller');
  });

  // -----------------------------------------------------------------------
  // 18. Services card renders with service rows or table
  // -----------------------------------------------------------------------
  test('services card renders service list', async ({ page }) => {
    const servicesList = page.locator('#services-list');
    await expect(servicesList).toBeVisible();
    // Wait for services to load and render.
    await page.waitForTimeout(3000);
    // Should have a service table with rows.
    const svcTable = page.locator('#svc-table');
    const tableVisible = await svcTable.isVisible();
    if (tableVisible) {
      const rows = svcTable.locator('tbody tr');
      const count = await rows.count();
      expect(count, 'Service table should have at least one row').toBeGreaterThan(0);
    }
  });

  // -----------------------------------------------------------------------
  // 19. SSE connection dot is present in header
  // -----------------------------------------------------------------------
  test('SSE connection dot is present in header', async ({ page }) => {
    const sseDot = page.locator('#sse-dot');
    await expect(sseDot).toBeVisible();
    const cls = await sseDot.getAttribute('class');
    // Should have either 'live' or 'dead' class.
    expect(cls).toMatch(/live|dead/);
  });

  // -----------------------------------------------------------------------
  // 20. Pending queue pills render with priority labels
  // -----------------------------------------------------------------------
  test('pending queue pills render with priority labels when queue is populated', async ({
    page,
  }) => {
    await page.waitForTimeout(2000);
    const statusCard = page.locator('#status-card');
    // Check if there are pending actions displayed as pills.
    const queueLabel = statusCard.locator('text=Queue');
    const hasQueue = await queueLabel.isVisible().catch(() => false);
    if (hasQueue) {
      // Queue pills should contain priority labels like P30.
      const pills = statusCard.locator('span:has-text("P")');
      const count = await pills.count();
      expect(count, 'Queue should have at least one pill with priority').toBeGreaterThan(0);
    }
    // If no queue, that is a valid state — nothing to assert.
  });

  // -----------------------------------------------------------------------
  // 21. Progress bar appears during running action
  // -----------------------------------------------------------------------
  test('progress bar appears when action is running', async ({ page }) => {
    await page.waitForTimeout(2000);
    const statusCard = page.locator('#status-card');
    const phase = statusCard.locator('.phase');
    const phaseText = (await phase.textContent()) || '';
    if (phaseText.toLowerCase() === 'running') {
      const progressBar = statusCard.locator('.progress-bar');
      await expect(progressBar).toBeVisible();
    }
    // If not running, no progress bar expected — valid state.
  });

  // -----------------------------------------------------------------------
  // New UI features: per-service password reset, controller in list,
  // hide disabled filter, gateway URL display
  // -----------------------------------------------------------------------

  test('hide disabled toggle filters disabled services', async ({ page }) => {
    await page.waitForTimeout(2000);
    const cb = page.locator('#hideDisabledCb');
    await expect(cb).toBeVisible();
    // Toggle should be checked by default
    await expect(cb).toBeChecked();
    // Uncheck to show disabled services
    await cb.uncheck();
    await page.waitForTimeout(500);
    const disabledRows = page.locator('#svc-table td:has-text("Not enabled")');
    const count = await disabledRows.count();
    // Re-check to hide them
    await cb.check();
    await page.waitForTimeout(500);
    const afterCount = await page.locator('#svc-table td:has-text("Not enabled")').count();
    // After hiding, disabled count should be 0 or less than before
    expect(afterCount).toBeLessThanOrEqual(count);
  });

  test('controller appears in service list when disabled filter is off', async ({ page }) => {
    await page.waitForTimeout(2000);
    // Uncheck hide disabled to show all services
    const cb = page.locator('#hideDisabledCb');
    await cb.uncheck();
    await page.waitForTimeout(500);
    const svcTable = page.locator('#svc-table');
    const controllerRow = svcTable.locator('td:has-text("Media Stack Controller")');
    await expect(controllerRow.first()).toBeVisible();
  });

  test('password reset modal shows per-service checkboxes', async ({ page }) => {
    const resetBtn = page.locator('button:has-text("Reset Password")');
    await resetBtn.click();
    await page.waitForTimeout(300);
    // Modal should appear with service checkboxes
    const overlay = page.locator('.confirm-overlay');
    await expect(overlay).toBeVisible();
    const checkboxes = overlay.locator('.pw-svc-cb');
    const count = await checkboxes.count();
    expect(count).toBeGreaterThanOrEqual(2);
    // All should be checked by default
    for (let i = 0; i < count; i++) {
      await expect(checkboxes.nth(i)).toBeChecked();
    }
    // Close modal
    await overlay.click({ position: { x: 5, y: 5 } });
  });

  test('routing panel displays gateway URL from API', async ({ page }) => {
    // Navigate to routing tab
    const routingTab = page.locator('button:has-text("Routing")');
    await routingTab.click();
    await page.waitForTimeout(1000);
    const routingPanel = page.locator('#routing-config');
    await expect(routingPanel).toBeVisible();
    // Should contain Gateway URL row
    const gwRow = routingPanel.locator('td:has-text("Gateway URL")');
    await expect(gwRow).toBeVisible();
    // The adjacent cell should have an http:// link
    const gwLink = routingPanel.locator('a[href^="http://"]').first();
    await expect(gwLink).toBeVisible();
  });

  test('routing edit form populates from current config', async ({ page }) => {
    const routingTab = page.locator('button:has-text("Routing")');
    await routingTab.click();
    await page.waitForTimeout(1000);
    // Open the edit section
    const editToggle = page.locator('summary:has-text("Edit Routing Config")');
    await editToggle.click();
    await page.waitForTimeout(300);
    // Check that form fields are populated
    const gwPrefix = page.locator('#rt-gwprefix');
    const domain = page.locator('#rt-domain');
    const subdomain = page.locator('#rt-subdomain');
    await expect(gwPrefix).toBeVisible();
    await expect(domain).toBeVisible();
    const gwVal = await gwPrefix.inputValue();
    const domVal = await domain.inputValue();
    // Should have non-empty values
    expect(gwVal.length).toBeGreaterThan(0);
    expect(domVal.length).toBeGreaterThan(0);
  });

  // -----------------------------------------------------------------------
  // API docs
  // -----------------------------------------------------------------------

  test('API docs page loads with Redoc UI', async ({ page }) => {
    await page.goto(baseUrl + '/api/docs', { waitUntil: 'domcontentloaded', timeout: 15_000 });
    // Page should contain the Redoc container and load the spec
    await page.waitForSelector('redoc', { timeout: 10_000 });
    // Title from the OpenAPI spec should render
    const heading = page.locator('h1:has-text("Media Stack Controller")');
    await expect(heading).toBeVisible({ timeout: 10_000 });
    // At least one API tag section should be visible
    const tagSection = page.locator('[data-section-id]').first();
    await expect(tagSection).toBeVisible({ timeout: 10_000 });
  });
});
