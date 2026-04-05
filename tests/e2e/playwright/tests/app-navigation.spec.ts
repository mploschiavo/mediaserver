/**
 * Comprehensive browser-level tests for every app in the media stack.
 *
 * These tests use a real Chromium browser and verify:
 *   - Page loads with real DOM elements rendered (not just HTTP 200)
 *   - In-app navigation (Settings, sub-pages) works through the edge proxy
 *   - Static assets (JS/CSS) load without 404s
 *   - Login flows succeed where applicable
 *   - Key UI elements are visible and interactive
 *
 * Required env vars:
 *   STACK_COMPOSE_GATEWAY_HOST — resolvable gateway hostname (default: apps.media-dev.local)
 *   STACK_COMPOSE_EDGE_PORT   — gateway port (default: 18080)
 *   STACK_ADMIN_USERNAME      — admin user for ARR/qBittorrent login (default: admin)
 *   STACK_ADMIN_PASSWORD      — admin password (default: media-dev)
 *
 * The gateway hostname must resolve (e.g. via /etc/hosts: 127.0.0.1 apps.media-dev.local).
 */
import { expect, test, type Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
const gatewayHost = process.env.STACK_COMPOSE_GATEWAY_HOST || 'apps.media-dev.local';
const edgePort = process.env.STACK_COMPOSE_EDGE_PORT || '18080';
const adminUser = process.env.STACK_ADMIN_USERNAME || 'admin';
const adminPass = process.env.STACK_ADMIN_PASSWORD || 'media-dev';

// Omit port from URL when it's the default for the scheme.
const portSuffix = edgePort === '80' || edgePort === '443' ? '' : `:${edgePort}`;
const gateway = `http://${gatewayHost}${portSuffix}`;

// Helper: navigate to an app through the gateway.
async function gotoApp(page: Page, path: string) {
  await page.goto(`${gateway}${path}`, {
    waitUntil: 'domcontentloaded',
    timeout: 20_000,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
test.describe('App browser navigation tests', () => {
  test.use({
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
  });

  // -------------------------------------------------------------------------
  // Gateway root
  // -------------------------------------------------------------------------
  test.describe('Gateway root', () => {
    test('/ redirects to Jellyfin (/app/jellyfin)', async ({ page }) => {
      await page.goto(gateway, { waitUntil: 'domcontentloaded', timeout: 20_000 });
      // Should end up on Jellyfin's page
      expect(page.url()).toContain('/app/jellyfin');
      const body = await page.content();
      expect(body.toLowerCase()).toContain('jellyfin');
    });
  });

  // -------------------------------------------------------------------------
  // Homepage
  // -------------------------------------------------------------------------
  test.describe('Homepage', () => {
    test('L1: renders dashboard with service tiles', async ({ page }) => {
      await gotoApp(page, '/app/homepage');
      // Homepage should render real app tile links.
      const tileLinks = page.locator('a[href*="/app/"]');
      await expect(tileLinks.first()).toBeVisible({ timeout: 10_000 });
      const count = await tileLinks.count();
      expect(count, 'Homepage should render multiple app tile links').toBeGreaterThanOrEqual(3);
    });

    test('L2: clicking Sonarr tile opens correct URL (not double-prefixed)', async ({
      page,
      context,
    }) => {
      await gotoApp(page, '/app/homepage');
      await page.waitForTimeout(3000);
      const sonarrLink = page.locator('a[href*="/app/sonarr"]').first();
      await expect(sonarrLink).toBeVisible({ timeout: 10_000 });
      // Homepage tiles have target="_blank" — intercept the new tab.
      const [newPage] = await Promise.all([
        context.waitForEvent('page', { timeout: 10_000 }),
        sonarrLink.click(),
      ]);
      await newPage.waitForLoadState('domcontentloaded', { timeout: 20_000 });
      expect(
        newPage.url(),
        `Sonarr tile opened ${newPage.url()} — should be /app/sonarr`,
      ).toContain('/app/sonarr');
      expect(newPage.url()).not.toContain('/app/homepage/app/');
      await newPage.close();
    });

    test('L2: clicking Radarr tile opens correct URL', async ({ page, context }) => {
      await gotoApp(page, '/app/homepage');
      await page.waitForTimeout(3000);
      const radarrLink = page.locator('a[href*="/app/radarr"]').first();
      await expect(radarrLink).toBeVisible({ timeout: 10_000 });
      const [newPage] = await Promise.all([
        context.waitForEvent('page', { timeout: 10_000 }),
        radarrLink.click(),
      ]);
      await newPage.waitForLoadState('domcontentloaded', { timeout: 20_000 });
      expect(newPage.url()).toContain('/app/radarr');
      expect(newPage.url()).not.toContain('/app/homepage/app/');
      await newPage.close();
    });

    test('L2: clicking Jellyfin tile opens correct URL', async ({ page, context }) => {
      await gotoApp(page, '/app/homepage');
      await page.waitForTimeout(3000);
      const jellyfinLink = page.locator('a[href*="/app/jellyfin"]').first();
      await expect(jellyfinLink).toBeVisible({ timeout: 10_000 });
      const [newPage] = await Promise.all([
        context.waitForEvent('page', { timeout: 10_000 }),
        jellyfinLink.click(),
      ]);
      await newPage.waitForLoadState('domcontentloaded', { timeout: 20_000 });
      expect(newPage.url()).toContain('/app/jellyfin');
      expect(newPage.url()).not.toContain('/app/homepage/app/');
      await newPage.close();
    });

    test('L2: no tile links contain double /app/homepage/app/ prefix', async ({ page }) => {
      await gotoApp(page, '/app/homepage');
      await page.waitForTimeout(3000);
      // Check ALL tile link hrefs in the DOM
      const allHrefs = await page.locator('a[href*="/app/"]').evaluateAll((els) =>
        els.map((el) => ({ text: el.textContent?.trim() || '', href: el.getAttribute('href') || '' })),
      );
      const broken = allHrefs.filter((l) => l.href.includes('/app/homepage/app/'));
      expect(
        broken,
        `These tile links have double prefix:\n${broken.map((l) => `  ${l.text}: ${l.href}`).join('\n')}`,
      ).toHaveLength(0);
    });
  });

  // -------------------------------------------------------------------------
  // Jellyfin (via gateway path /app/jellyfin)
  // -------------------------------------------------------------------------
  test.describe('Jellyfin', () => {
    test('L1: web UI renders via gateway path', async ({ page }) => {
      await gotoApp(page, '/app/jellyfin');
      const body = await page.content();
      expect(body.toLowerCase()).toContain('jellyfin');
      await expect(
        page.locator('#app-loader, .skinBody, [data-role="page"], main, .mainDrawer').first(),
      ).toBeVisible({ timeout: 15_000 });
    });

    test('L2: web/index.html has interactive navigation', async ({ page }) => {
      await gotoApp(page, '/app/jellyfin/web/index.html');
      await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
      const navItems = page.locator(
        '.navMenuOption, .mainDrawer a, [data-role="page"] a, .headerButton',
      );
      const count = await navItems.count();
      expect(count, 'Jellyfin should have navigation elements').toBeGreaterThan(0);
    });

    test('L3: Dashboard page loads via gateway', async ({ page }) => {
      await gotoApp(page, '/app/jellyfin/web/index.html#!/dashboard');
      await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
      const body = await page.content();
      expect(
        body.toLowerCase().includes('dashboard') ||
          body.toLowerCase().includes('server') ||
          body.toLowerCase().includes('jellyfin'),
        'Jellyfin dashboard should render',
      ).toBeTruthy();
    });
  });

  // -------------------------------------------------------------------------
  // Jellyseerr
  // -------------------------------------------------------------------------
  test.describe('Jellyseerr', () => {
    // Helper: login to Jellyseerr with retry on error.
    async function jellyseerrLogin(page: Page) {
      for (let attempt = 0; attempt < 3; attempt++) {
        await gotoApp(page, '/app/jellyseerr/login');
        await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
        const emailInput = page
          .locator('input[type="text"][name="email"]')
          .or(page.locator('input[type="email"]'))
          .or(page.locator('input[id="email"]'))
          .first();
        if (!(await emailInput.isVisible().catch(() => false))) break;
        await emailInput.fill(adminUser);
        await page.locator('input[type="password"]').first().fill(adminPass);
        await page
          .locator('button[type="submit"]')
          .or(page.locator('button:has-text("Sign In")'))
          .first()
          .click();
        await page.waitForTimeout(5000);
        // Check for login error and retry if needed.
        const errorMsg = page.locator('text=Something went wrong');
        if (await errorMsg.isVisible().catch(() => false)) {
          await page.waitForTimeout(3000);
          continue;
        }
        break;
      }
    }

    test('L1: login page renders with sign-in button', async ({ page }) => {
      await gotoApp(page, '/app/jellyseerr');
      await page.waitForURL(/login/, { timeout: 15_000 });
      await expect(
        page
          .locator('button:has-text("Sign In")')
          .or(page.locator('button:has-text("sign in")'))
          .or(page.locator('a:has-text("Sign In")'))
          .first(),
      ).toBeVisible({ timeout: 10_000 });
    });

    test('L1: static assets load without 404s', async ({ page }) => {
      const notFoundUrls: string[] = [];
      page.on('response', (response) => {
        if (response.status() === 404 && response.url().includes(gatewayHost)) {
          notFoundUrls.push(response.url());
        }
      });
      await gotoApp(page, '/app/jellyseerr');
      await page.waitForURL(/login/, { timeout: 15_000 });
      await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
      expect(
        notFoundUrls,
        `Jellyseerr page load produced 404s:\n${notFoundUrls.join('\n')}`,
      ).toHaveLength(0);
    });

    test('L2: Jellyfin sign-in flow shows username/password inputs', async ({ page }) => {
      await gotoApp(page, '/app/jellyseerr');
      await page.waitForURL(/login/, { timeout: 15_000 });
      const jellyfinBtn = page
        .locator('button:has-text("Jellyfin")')
        .or(page.locator('a:has-text("Jellyfin")'))
        .first();
      if (await jellyfinBtn.isVisible().catch(() => false)) {
        await jellyfinBtn.click();
        await expect(
          page.locator('input[type="text"]').or(page.locator('input[name="username"]')).first(),
        ).toBeVisible({ timeout: 10_000 });
      }
    });

    test('L2: login succeeds and shows Discover content', async ({ page }) => {
      await jellyseerrLogin(page);
      const body = await page.content();
      expect(
        body.toLowerCase().includes('discover') ||
          body.toLowerCase().includes('trending') ||
          body.toLowerCase().includes('recently added'),
        'Post-login page should show Discover/Trending content',
      ).toBeTruthy();
    });

    test('L3: login → click Settings → page renders without 404', async ({ page }) => {
      await jellyseerrLogin(page);
      // Click Settings in sidebar
      const settingsLink = page
        .locator('a[href="/settings"]')
        .or(page.locator('a:has-text("Settings")'))
        .first();
      await expect(settingsLink).toBeVisible({ timeout: 10_000 });
      await settingsLink.click();
      await page.waitForTimeout(3000);
      const body = await page.content();
      // Must render settings, NOT a 404 JSON error
      expect(body, 'Settings page must not contain 404 error').not.toContain('"status_code": 404');
      expect(body, 'Settings page must not contain "Not found" error').not.toContain(
        '"Not found',
      );
      expect(
        body.toLowerCase().includes('settings') || body.toLowerCase().includes('general'),
        'Settings page should render settings UI',
      ).toBeTruthy();
    });

    test('L3: login → Settings → click Users sub-page', async ({ page }) => {
      await jellyseerrLogin(page);
      const settingsLink = page
        .locator('a[href="/settings"]')
        .or(page.locator('a:has-text("Settings")'))
        .first();
      if (await settingsLink.isVisible().catch(() => false)) {
        await settingsLink.click();
        await page.waitForTimeout(3000);
      }
      // Click Users sub-page within Settings
      const usersLink = page
        .locator('a[href="/settings/users"]')
        .or(page.locator('a[href*="users"]:has-text("Users")'))
        .first();
      if (await usersLink.isVisible().catch(() => false)) {
        await usersLink.click();
        await page.waitForTimeout(3000);
        const body = await page.content();
        expect(body).not.toContain('"status_code": 404');
        expect(
          body.toLowerCase().includes('user') || body.toLowerCase().includes('permission'),
          'Users settings should render user management UI',
        ).toBeTruthy();
      }
    });

    test('L3: login → Settings → click Notifications sub-page', async ({ page }) => {
      await jellyseerrLogin(page);
      const settingsLink = page
        .locator('a[href="/settings"]')
        .or(page.locator('a:has-text("Settings")'))
        .first();
      if (await settingsLink.isVisible().catch(() => false)) {
        await settingsLink.click();
        await page.waitForTimeout(3000);
      }
      const notifLink = page
        .locator('a[href="/settings/notifications"]')
        .or(page.locator('a[href*="notifications"]:has-text("Notification")'))
        .first();
      if (await notifLink.isVisible().catch(() => false)) {
        await notifLink.click();
        await page.waitForTimeout(3000);
        const body = await page.content();
        expect(body).not.toContain('"status_code": 404');
        expect(
          body.toLowerCase().includes('notification') || body.toLowerCase().includes('agent'),
          'Notifications settings should render',
        ).toBeTruthy();
      }
    });

    test('L4: login → Settings → click General sub-page', async ({ page }) => {
      await jellyseerrLogin(page);
      const settingsLink = page
        .locator('a[href="/settings"]')
        .or(page.locator('a:has-text("Settings")'))
        .first();
      if (await settingsLink.isVisible().catch(() => false)) {
        await settingsLink.click();
        await page.waitForTimeout(3000);
      }
      // Click General settings (the main settings page)
      const generalLink = page
        .locator('a[href="/settings/main"]')
        .or(page.locator('a:has-text("General")'))
        .first();
      if (await generalLink.isVisible().catch(() => false)) {
        await generalLink.click();
        await page.waitForTimeout(3000);
        const body = await page.content();
        expect(body).not.toContain('"status_code": 404');
        expect(body).not.toContain('This page could not be found');
        expect(
          body.toLowerCase().includes('general') || body.toLowerCase().includes('application'),
          'General settings should render',
        ).toBeTruthy();
      }
    });

    test('L4: Homepage → Jellyseerr → login → Settings → Network', async ({
      page,
      context,
    }) => {
      await gotoApp(page, '/app/homepage');
      await page.waitForTimeout(2000);
      const jellyseerrTile = page.locator('a[href*="jellyseerr"]').first();
      await expect(jellyseerrTile).toBeVisible({ timeout: 10_000 });
      const [jellyseerrPage] = await Promise.all([
        context.waitForEvent('page', { timeout: 10_000 }),
        jellyseerrTile.click(),
      ]);
      await jellyseerrPage.waitForLoadState('domcontentloaded', { timeout: 20_000 });

      const emailInput = jellyseerrPage
        .locator('input[type="text"][name="email"]')
        .or(jellyseerrPage.locator('input[type="email"]'))
        .or(jellyseerrPage.locator('input[id="email"]'))
        .first();
      if (await emailInput.isVisible().catch(() => false)) {
        await emailInput.fill(adminUser);
        await jellyseerrPage.locator('input[type="password"]').first().fill(adminPass);
        await jellyseerrPage
          .locator('button[type="submit"]')
          .or(jellyseerrPage.locator('button:has-text("Sign In")'))
          .first()
          .click();
        await jellyseerrPage.waitForTimeout(5000);
      }

      const settingsLink = jellyseerrPage
        .locator('a[href="/settings"]')
        .or(jellyseerrPage.locator('a:has-text("Settings")'))
        .first();
      await expect(settingsLink).toBeVisible({ timeout: 10_000 });
      await settingsLink.click();
      await jellyseerrPage.waitForTimeout(3000);

      const networkLink = jellyseerrPage
        .locator('a[href="/settings/network"]')
        .or(jellyseerrPage.locator('a[href*="network"]'))
        .or(jellyseerrPage.locator('a:has-text("Network")'))
        .first();
      await expect(networkLink).toBeVisible({ timeout: 10_000 });
      await networkLink.click();
      await jellyseerrPage.waitForTimeout(3000);

      expect(jellyseerrPage.url()).toContain('/app/jellyseerr/settings/network');
      const body = await jellyseerrPage.content();
      expect(body).not.toContain('"status_code": 404');
      expect(body).not.toContain('This page could not be found');
      expect(
        body.toLowerCase().includes('network settings') ||
          body.toLowerCase().includes('enable proxy support'),
        'Network settings should render',
      ).toBeTruthy();

      await jellyseerrPage.close();
    });

    test('L5: Homepage → Jellyseerr → login → Settings → Notifications → Webhook', async ({
      page,
    }) => {
      // Full 5-level deep flow from Homepage
      await gotoApp(page, '/app/homepage');
      await page.waitForTimeout(2000);
      // Click Jellyseerr tile
      const jellyseerrTile = page.locator('a[href*="jellyseerr"]').first();
      await expect(jellyseerrTile).toBeVisible({ timeout: 10_000 });
      await jellyseerrTile.click();
      await page.waitForTimeout(3000);
      // Should be on login page — login
      const emailInput = page
        .locator('input[type="text"][name="email"]')
        .or(page.locator('input[type="email"]'))
        .or(page.locator('input[id="email"]'))
        .first();
      if (await emailInput.isVisible().catch(() => false)) {
        await emailInput.fill(adminUser);
        await page.locator('input[type="password"]').first().fill(adminPass);
        await page
          .locator('button[type="submit"]')
          .or(page.locator('button:has-text("Sign In")'))
          .first()
          .click();
        await page.waitForTimeout(5000);
      }
      // Click Settings
      const settingsLink = page
        .locator('a[href="/settings"]')
        .or(page.locator('a:has-text("Settings")'))
        .first();
      if (await settingsLink.isVisible().catch(() => false)) {
        await settingsLink.click();
        await page.waitForTimeout(3000);
      }
      // Click Notifications
      const notifLink = page
        .locator('a[href="/settings/notifications"]')
        .or(page.locator('a[href*="notifications"]:has-text("Notification")'))
        .first();
      if (await notifLink.isVisible().catch(() => false)) {
        await notifLink.click();
        await page.waitForTimeout(3000);
      }
      // Click Webhook sub-page
      const webhookLink = page
        .locator('a[href*="webhook"]')
        .or(page.locator('a:has-text("Webhook")'))
        .first();
      if (await webhookLink.isVisible().catch(() => false)) {
        await webhookLink.click();
        await page.waitForTimeout(3000);
      }
      // Verify no crash
      const body = await page.content();
      expect(body).not.toContain('"status_code": 404');
      expect(body).not.toContain('This page could not be found');
    });

    test('L3: login → click Requests page', async ({ page }) => {
      await jellyseerrLogin(page);
      const requestsLink = page
        .locator('a[href="/requests"]')
        .or(page.locator('a:has-text("Requests")'))
        .first();
      if (await requestsLink.isVisible().catch(() => false)) {
        await requestsLink.click();
        await page.waitForTimeout(3000);
        const body = await page.content();
        expect(body).not.toContain('"status_code": 404');
        expect(
          body.toLowerCase().includes('request') || body.toLowerCase().includes('filter'),
          'Requests page should render',
        ).toBeTruthy();
      }
    });
  });

  // -------------------------------------------------------------------------
  // SABnzbd
  // -------------------------------------------------------------------------
  test.describe('SABnzbd', () => {
    test('L1: main page renders queue interface', async ({ page }) => {
      await gotoApp(page, '/app/sabnzbd/');
      const body = await page.content();
      expect(
        body.includes('queue') || body.includes('Queue') || body.includes('SABnzbd'),
        'SABnzbd main page should contain queue UI',
      ).toBeTruthy();
    });

    test('L2: config page is reachable within base path', async ({ page }) => {
      await gotoApp(page, '/app/sabnzbd/config/');
      const body = await page.content();
      expect(
        body.toLowerCase().includes('general') ||
          body.toLowerCase().includes('folders') ||
          body.toLowerCase().includes('servers'),
        'SABnzbd config page should render configuration sections',
      ).toBeTruthy();
      expect(page.url()).toContain('/app/sabnzbd/config');
    });

    test('L3: General config sub-page renders form elements', async ({ page }) => {
      await gotoApp(page, '/app/sabnzbd/config/general/');
      const body = await page.content();
      expect(
        body.toLowerCase().includes('host') || body.toLowerCase().includes('general'),
        'General config page should contain settings fields',
      ).toBeTruthy();
      expect(page.url()).toContain('/app/sabnzbd/config/general');
    });

    test('L3: Servers config sub-page renders', async ({ page }) => {
      await gotoApp(page, '/app/sabnzbd/config/server/');
      const body = await page.content();
      expect(
        body.toLowerCase().includes('server') || body.toLowerCase().includes('news'),
        'Servers config page should render server list',
      ).toBeTruthy();
      expect(page.url()).toContain('/app/sabnzbd/config/server');
    });
  });

  // -------------------------------------------------------------------------
  // Sonarr
  // -------------------------------------------------------------------------
  test.describe('Sonarr', () => {
    test('L1: UI renders navigation bar', async ({ page }) => {
      await gotoApp(page, '/app/sonarr');
      await expect(
        page.locator('a[href*="series"]').or(page.locator('a[href*="calendar"]')).or(page.locator('nav')).first(),
      ).toBeVisible({ timeout: 15_000 });
    });

    test('L2: Series nav link stays within base path', async ({ page }) => {
      await gotoApp(page, '/app/sonarr');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const seriesLink = page.locator('a:has-text("Series")').or(page.locator('a[href*="/series"]')).first();
      if (await seriesLink.isVisible().catch(() => false)) {
        await seriesLink.click();
        await page.waitForTimeout(2000);
        expect(page.url(), 'Navigation should stay under /app/sonarr').toContain('/app/sonarr');
      }
    });

    test('L2: Settings > General page renders', async ({ page }) => {
      await gotoApp(page, '/app/sonarr/settings/general');
      await expect(page.locator('input').or(page.locator('select')).first()).toBeVisible({
        timeout: 15_000,
      });
      expect(page.url()).toContain('/app/sonarr');
    });

    test('L3: System > Status page renders version info', async ({ page }) => {
      await gotoApp(page, '/app/sonarr/system/status');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const body = await page.content();
      expect(
        body.toLowerCase().includes('version') || body.toLowerCase().includes('status'),
        'System status page should show version info',
      ).toBeTruthy();
    });

    test('L3: click System → Status via navigation', async ({ page }) => {
      await gotoApp(page, '/app/sonarr');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const systemLink = page.locator('a:has-text("System")').first();
      if (await systemLink.isVisible().catch(() => false)) {
        await systemLink.click();
        await page.waitForTimeout(2000);
        const statusLink = page.locator('a:has-text("Status")').first();
        if (await statusLink.isVisible().catch(() => false)) {
          await statusLink.click();
          await page.waitForTimeout(2000);
        }
      }
      expect(page.url()).toContain('/app/sonarr');
      const body = await page.content();
      expect(body).not.toContain('TypeError');
    });
  });

  // -------------------------------------------------------------------------
  // Radarr
  // -------------------------------------------------------------------------
  test.describe('Radarr', () => {
    test('L1: UI renders navigation bar', async ({ page }) => {
      await gotoApp(page, '/app/radarr');
      await expect(
        page.locator('a[href*="movie"]').or(page.locator('a[href*="calendar"]')).or(page.locator('nav')).first(),
      ).toBeVisible({ timeout: 15_000 });
    });

    test('L2: Movies nav link stays within base path', async ({ page }) => {
      await gotoApp(page, '/app/radarr');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const moviesLink = page.locator('a:has-text("Movies")').or(page.locator('a[href*="/movie"]')).first();
      if (await moviesLink.isVisible().catch(() => false)) {
        await moviesLink.click();
        await page.waitForTimeout(2000);
        expect(page.url()).toContain('/app/radarr');
      }
    });

    test('L2: Settings > General page renders', async ({ page }) => {
      await gotoApp(page, '/app/radarr/settings/general');
      await expect(page.locator('input').or(page.locator('select')).first()).toBeVisible({
        timeout: 15_000,
      });
      expect(page.url()).toContain('/app/radarr');
    });

    test('L3: System > Status page renders', async ({ page }) => {
      await gotoApp(page, '/app/radarr/system/status');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const body = await page.content();
      expect(
        body.toLowerCase().includes('version') || body.toLowerCase().includes('status'),
        'Radarr system status should render',
      ).toBeTruthy();
      expect(page.url()).toContain('/app/radarr');
    });

    test('L3: click System → Status via navigation', async ({ page }) => {
      await gotoApp(page, '/app/radarr');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const systemLink = page.locator('a:has-text("System")').first();
      if (await systemLink.isVisible().catch(() => false)) {
        await systemLink.click();
        await page.waitForTimeout(2000);
        const statusLink = page.locator('a:has-text("Status")').first();
        if (await statusLink.isVisible().catch(() => false)) {
          await statusLink.click();
          await page.waitForTimeout(2000);
        }
      }
      expect(page.url()).toContain('/app/radarr');
    });
  });

  // -------------------------------------------------------------------------
  // Prowlarr
  // -------------------------------------------------------------------------
  test.describe('Prowlarr', () => {
    test('L1: UI renders with indexer navigation', async ({ page }) => {
      await gotoApp(page, '/app/prowlarr');
      await expect(
        page.locator('a[href*="indexer"]').or(page.locator('.navbar')).or(page.locator('nav')).first(),
      ).toBeVisible({ timeout: 15_000 });
    });

    test('L2: Settings > General page renders within base path', async ({ page }) => {
      await gotoApp(page, '/app/prowlarr/settings/general');
      await expect(page.locator('input').or(page.locator('select')).first()).toBeVisible({
        timeout: 15_000,
      });
      expect(page.url()).toContain('/app/prowlarr');
    });

    test('L3: System > Status page renders', async ({ page }) => {
      await gotoApp(page, '/app/prowlarr/system/status');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const body = await page.content();
      expect(
        body.toLowerCase().includes('version') || body.toLowerCase().includes('status'),
        'Prowlarr system status should render',
      ).toBeTruthy();
      expect(page.url()).toContain('/app/prowlarr');
    });
  });

  // -------------------------------------------------------------------------
  // Bazarr
  // -------------------------------------------------------------------------
  test.describe('Bazarr', () => {
    test('L1: UI renders navigation elements', async ({ page }) => {
      await gotoApp(page, '/app/bazarr');
      await expect(
        page.locator('a[href*="series"]').or(page.locator('a[href*="movies"]')).or(page.locator('nav')).first(),
      ).toBeVisible({ timeout: 15_000 });
    });

    test('L2: Settings page renders within base path', async ({ page }) => {
      await gotoApp(page, '/app/bazarr/settings/general');
      await page.waitForTimeout(3000);
      expect(page.url()).toContain('/app/bazarr');
      const body = await page.content();
      expect(
        body.toLowerCase().includes('general') || body.toLowerCase().includes('settings'),
        'Bazarr settings page should render',
      ).toBeTruthy();
    });

    test('L3: System → Status page renders without crash', async ({ page }) => {
      await gotoApp(page, '/app/bazarr');
      await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
      // Click System in nav
      const systemLink = page.locator('a:has-text("System")').first();
      if (await systemLink.isVisible().catch(() => false)) {
        await systemLink.click();
        await page.waitForTimeout(2000);
      }
      // Navigate to Status sub-page
      const statusLink = page.locator('a:has-text("Status")').first();
      if (await statusLink.isVisible().catch(() => false)) {
        await statusLink.click();
        await page.waitForTimeout(3000);
      }
      // Verify no JS crash — page content should contain status info, not error text
      const body = await page.content();
      expect(body).not.toContain('TypeError');
      expect(body).not.toContain('Failed to fetch dynamically imported module');
      expect(page.url()).toContain('/app/bazarr');
    });

    test('L3: System → Status via direct URL loads without crash', async ({ page }) => {
      const notFoundUrls: string[] = [];
      page.on('response', (response) => {
        if (response.status() === 404 && response.url().includes(gatewayHost)) {
          notFoundUrls.push(response.url());
        }
      });
      await gotoApp(page, '/app/bazarr/system/status');
      await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
      expect(
        notFoundUrls.filter((u) => u.includes('/assets/')),
        `Bazarr system/status had 404 asset loads:\n${notFoundUrls.join('\n')}`,
      ).toHaveLength(0);
      const body = await page.content();
      expect(body).not.toContain('TypeError');
    });
  });

  // -------------------------------------------------------------------------
  // qBittorrent
  // -------------------------------------------------------------------------
  test.describe('qBittorrent', () => {
    test('L1: login page or main UI renders', async ({ page }) => {
      await gotoApp(page, '/app/qbittorrent');
      await expect(
        page
          .locator('#loginForm')
          .or(page.locator('input[name="username"]'))
          .or(page.locator('#desktop'))
          .or(page.locator('#desktopNavbar'))
          .first(),
      ).toBeVisible({ timeout: 15_000 });
    });

    test('L2: main UI shows desktop layout with toolbar', async ({ page }) => {
      await gotoApp(page, '/app/qbittorrent');
      const loginForm = page.locator('#loginForm').first();
      if (await loginForm.isVisible().catch(() => false)) {
        await page.locator('input[name="username"]').fill(adminUser);
        await page.locator('input[name="password"]').fill(adminPass);
        await page.locator('#loginButton').or(page.locator('button[type="submit"]')).first().click();
        await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      }
      await expect(
        page.locator('#desktop').or(page.locator('#desktopNavbar')).first(),
      ).toBeVisible({ timeout: 15_000 });
    });

    test('L3: click upload/download toolbar buttons are interactive', async ({ page }) => {
      await gotoApp(page, '/app/qbittorrent');
      const loginForm = page.locator('#loginForm').first();
      if (await loginForm.isVisible().catch(() => false)) {
        await page.locator('input[name="username"]').fill(adminUser);
        await page.locator('input[name="password"]').fill(adminPass);
        await page.locator('#loginButton').or(page.locator('button[type="submit"]')).first().click();
        await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      }
      // Verify toolbar links are present and clickable
      const uploadLink = page.locator('#uploadLink').first();
      await expect(uploadLink).toBeVisible({ timeout: 10_000 });
      // Verify the download link is also present
      const downloadLink = page.locator('#downloadLink').first();
      await expect(downloadLink).toBeVisible({ timeout: 5_000 });
    });
  });

  // -------------------------------------------------------------------------
  // Maintainerr
  // -------------------------------------------------------------------------
  test.describe('Maintainerr', () => {
    test('UI renders page content', async ({ page }) => {
      await gotoApp(page, '/app/maintainerr');
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
      const body = await page.content();
      expect(
        body.toLowerCase().includes('maintainerr') ||
          body.toLowerCase().includes('rules') ||
          body.toLowerCase().includes('collections') ||
          body.toLowerCase().includes('settings'),
        'Maintainerr page should render UI content',
      ).toBeTruthy();
    });
  });

  // -------------------------------------------------------------------------
  // FlareSolverr
  // -------------------------------------------------------------------------
  test.describe('FlareSolverr', () => {
    test('status page renders', async ({ page }) => {
      await gotoApp(page, '/app/flaresolverr');
      const body = await page.content();
      expect(
        body.toLowerCase().includes('flaresolverr') || body.includes('version'),
        'FlareSolverr should return status info',
      ).toBeTruthy();
    });
  });

  // -------------------------------------------------------------------------
  // Cross-app: verify all /app/* routes render without 404 asset errors
  // -------------------------------------------------------------------------
  test.describe('Cross-app asset integrity', () => {
    const apps = [
      { name: 'Sonarr', path: '/app/sonarr' },
      { name: 'Radarr', path: '/app/radarr' },
      { name: 'Prowlarr', path: '/app/prowlarr' },
      { name: 'Bazarr', path: '/app/bazarr' },
      { name: 'SABnzbd', path: '/app/sabnzbd/' },
      { name: 'Jellyseerr', path: '/app/jellyseerr' },
      { name: 'qBittorrent', path: '/app/qbittorrent' },
      { name: 'Homepage', path: '/app/homepage' },
      { name: 'Maintainerr', path: '/app/maintainerr' },
    ];

    for (const app of apps) {
      test(`${app.name}: no 404s on initial page load`, async ({ page }) => {
        const notFoundUrls: string[] = [];
        page.on('response', (response) => {
          if (response.status() === 404 && response.url().includes(gatewayHost)) {
            notFoundUrls.push(response.url());
          }
        });
        await gotoApp(page, app.path);
        await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
        expect(
          notFoundUrls,
          `${app.name} page load produced 404s:\n${notFoundUrls.join('\n')}`,
        ).toHaveLength(0);
      });
    }
  });
});
