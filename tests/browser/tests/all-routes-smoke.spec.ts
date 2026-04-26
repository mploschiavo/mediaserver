import { expect, test } from '@playwright/test';

/**
 * Route-smoke: visit every page in the dashboard and assert it
 * renders without throwing a runtime error.
 *
 * Why this exists: v1.3.41 shipped with a bundled JS that still
 * claimed to be 1.3.20 (package.json drift), and the resulting stale
 * ExposureCard threw "Cannot read properties of undefined (reading
 * 'exposure')" the moment an operator clicked Edge Gateway. The page
 * crashed, the ErrorBoundary swallowed the message into a generic
 * 'Something went wrong' card, and the only signal was operator
 * frustration. This test fails on that exact pattern by:
 *
 *   1. Listening to the page console + uncaught exceptions.
 *   2. Visiting every route under the SPA basepath.
 *   3. Asserting the ErrorBoundary fallback ('Something went
 *      wrong!') is NOT present anywhere on the page after settle.
 *
 * Run against a live compose / k8s deploy via:
 *   STACK_UI_URL=http://127.0.0.1:9101 \
 *   STACK_BASIC_USER=admin STACK_BASIC_PASS=… \
 *   pnpm playwright test all-routes-smoke
 *
 * Skip silently when STACK_UI_URL is unset so unit-test runs don't
 * fail on a missing live stack.
 */

const baseUrl = process.env.STACK_UI_URL || '';
const basicUser = process.env.STACK_BASIC_USER || '';
const basicPass = process.env.STACK_BASIC_PASS || '';
const skipReason =
  baseUrl.length === 0
    ? 'STACK_UI_URL is not set; export STACK_UI_URL=http://127.0.0.1:9101 before running route-smoke tests.'
    : '';

// Mirrors ui/src/routes/*.tsx — page routes only (test files,
// __root, the $ catch-all, and $placeholder are excluded).
const ROUTES = [
  '/',
  '/api-docs',
  '/audit-log',
  '/auth',
  '/bans',
  '/content',
  '/guardrails',
  '/jobs',
  '/livetv',
  '/logs',
  '/me',
  '/media-integrity',
  '/ops',
  '/routing',
  '/security',
  '/sessions',
  '/snapshots',
  '/users',
  '/webhooks',
];

test.describe('All routes smoke', () => {
  test.skip(Boolean(skipReason), skipReason);

  for (const route of ROUTES) {
    test(`${route} renders without runtime error`, async ({ page }) => {
      const consoleErrors: string[] = [];
      const pageErrors: string[] = [];

      page.on('console', (msg) => {
        if (msg.type() === 'error') consoleErrors.push(msg.text());
      });
      page.on('pageerror', (err) => {
        pageErrors.push(err.message);
      });

      // Use HTTP Basic for the controller's auth-required APIs.
      // The UI proxies /api/* to the controller; without credentials
      // every API call 401s and the page renders but with empty
      // shells (still passes the no-error check, which is fine).
      const headers: Record<string, string> = {};
      if (basicUser && basicPass) {
        headers.Authorization =
          'Basic ' + Buffer.from(`${basicUser}:${basicPass}`).toString('base64');
      }
      await page.setExtraHTTPHeaders(headers);

      const response = await page.goto(`${baseUrl}${route}`, {
        waitUntil: 'networkidle',
        timeout: 30_000,
      });
      expect(
        response?.status(),
        `${route} returned a non-2xx HTTP status`,
      ).toBeLessThan(400);

      // The ErrorBoundary card surfaces 'Something went wrong!' when
      // any descendant component threw. Asserting its absence is the
      // strongest signal that the page rendered without a runtime
      // error in this run.
      const errorCard = page.getByText('Something went wrong!', {
        exact: false,
      });
      await expect(errorCard, `${route} rendered the ErrorBoundary fallback`).not.toBeVisible();

      // Page-level uncaught exceptions are the hard failure signal —
      // they NEVER reach the ErrorBoundary.
      expect(
        pageErrors,
        `${route} threw uncaught exceptions: ${pageErrors.join(' | ')}`,
      ).toEqual([]);

      // Console errors are softer (some are network 401s on
      // protected endpoints when no auth is provided) — surface them
      // as test annotations instead of failing the build, but flag
      // any 'Cannot read properties of undefined' match because
      // that's the exact regression pattern we lock in here.
      const propertyErrors = consoleErrors.filter((m) =>
        m.includes('Cannot read properties of undefined'),
      );
      expect(
        propertyErrors,
        `${route} logged a 'Cannot read properties of undefined' error: ${propertyErrors.join(' | ')}`,
      ).toEqual([]);
    });
  }
});
