// Shared `page.route` helpers that install realistic JSON fixtures for
// every endpoint the dashboard touches. The shapes mirror
// `ui/src/api/shapes.ts`. Each helper is independently callable; the
// `mockAll(page)` convenience installs the lot in one go.

import type { Page, Route } from "@playwright/test";
import type {
  AuditLogShape,
  BrandingShape,
  EnforceReportShape,
  HealthShape,
  IdentityShape,
  MediaIntegrityProgressShape,
  MediaIntegrityStatusShape,
  ReconcileReportShape,
  SessionsShape,
} from "../../src/api/shapes";

const JSON_HEADERS = { "content-type": "application/json" };

function fulfillJson<T>(route: Route, body: T, status = 200): Promise<void> {
  return route.fulfill({
    status,
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
}

export const fixtures = {
  health: { status: "ok" } satisfies HealthShape,
  branding: {
    brand: { product_name: "Media Stack", logo_url: "/branding/icon.svg" },
    product_name: "Media Stack",
    logo_url: "/branding/icon.svg",
  } satisfies BrandingShape,
  identity: {
    authenticated: true,
    user: "operator",
    username: "operator",
    display_name: "Operator",
    email: "ops@local",
    is_admin: true,
  } satisfies IdentityShape,
  status: {
    last_enforce: {
      ts: "2026-04-24T10:00:00Z",
      detail: { changes: 0 },
    },
    last_reconcile: {
      ts: "2026-04-24T10:00:00Z",
      detail: {
        bytes_freed: 1_073_741_824,
        servarr: {
          results: {
            radarr: {
              ts: "2026-04-24T10:00:00Z",
              total_resolved: 12,
              bytes_freed: 5_368_709_120,
              total_needs_review: 0,
              total_failures: 0,
            },
            sonarr: {
              ts: "2026-04-24T10:00:00Z",
              total_resolved: 7,
              bytes_freed: 2_147_483_648,
              total_needs_review: 1,
              total_failures: 0,
            },
          },
        },
      },
    },
    policy_version: 4,
    servarr_adapters: ["radarr", "sonarr"],
    bazarr_present: true,
    missing_api_keys: [],
  } satisfies MediaIntegrityStatusShape,
  progressIdle: { in_progress: false } satisfies MediaIntegrityProgressShape,
  reconcileReport: {
    dry_run: false,
    bytes_freed: 1_073_741_824,
    servarr: { results: {} },
    bazarr: null,
  } satisfies ReconcileReportShape,
  enforceReport: {
    servarr: {},
    bazarr: null,
    changes: 0,
  } satisfies EnforceReportShape,
  auditLog: { entries: [] } satisfies AuditLogShape,
  sessions: { sessions: [] } satisfies SessionsShape,
};

export async function mockHealth(page: Page): Promise<void> {
  await page.route("**/api/health", (route) => fulfillJson(route, fixtures.health));
}

export async function mockBranding(page: Page): Promise<void> {
  await page.route("**/api/branding", (route) =>
    fulfillJson(route, fixtures.branding),
  );
}

export async function mockAuth(page: Page): Promise<void> {
  await page.route("**/api/auth/identity", (route) =>
    fulfillJson(route, fixtures.identity),
  );
  await page.route("**/api/auth/logout", (route) =>
    route.fulfill({ status: 204 }),
  );
}

export async function mockMediaIntegrityStatus(page: Page): Promise<void> {
  await page.route("**/api/media-integrity/status", (route) =>
    fulfillJson(route, fixtures.status),
  );
  await page.route("**/api/media-integrity/progress", (route) =>
    fulfillJson(route, fixtures.progressIdle),
  );
}

export async function mockReconcile(page: Page): Promise<void> {
  await page.route("**/api/media-integrity/reconcile**", (route) =>
    fulfillJson(route, fixtures.reconcileReport),
  );
}

export async function mockEnforce(page: Page): Promise<void> {
  await page.route("**/api/media-integrity/enforce-config", (route) =>
    fulfillJson(route, fixtures.enforceReport),
  );
}

export async function mockAdminLegacy(page: Page): Promise<void> {
  // Command palette posts to legacy /api/admin/* endpoints.
  await page.route("**/api/admin/reconcile**", (route) =>
    fulfillJson(route, { ok: true }),
  );
  await page.route("**/api/admin/enforce-config", (route) =>
    fulfillJson(route, { ok: true }),
  );
}

export async function mockLogs(page: Page): Promise<void> {
  await page.route("**/api/audit-log**", (route) =>
    fulfillJson(route, fixtures.auditLog),
  );
}

export async function mockUsers(page: Page): Promise<void> {
  await page.route("**/api/sessions/active", (route) =>
    fulfillJson(route, fixtures.sessions),
  );
}

export async function mockAll(page: Page): Promise<void> {
  await mockHealth(page);
  await mockBranding(page);
  await mockAuth(page);
  await mockMediaIntegrityStatus(page);
  await mockReconcile(page);
  await mockEnforce(page);
  await mockAdminLegacy(page);
  await mockLogs(page);
  await mockUsers(page);
}
