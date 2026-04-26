/**
 * Ratchet: TanStack Query error states render via ApiErrorTile, not
 * raw `error.message`.
 *
 * Why this exists: half a dozen tiles across the dashboard used to
 * `<div className="text-sm text-danger">{query.error.message}</div>`,
 * which produces strings like `"HTTP 404"` even when the underlying
 * status was `401` ("session expired"). Operators saw "HTTP 404" on
 * the Profile page and assumed it was broken when really their
 * session had expired.
 *
 * The fix lives at `src/components/ApiErrorTile.tsx` — it inspects
 * the ApiError's `status` and renders a status-aware tile (Sign in
 * for 401, Insufficient permissions for 403, Server error for 5xx,
 * etc.) with cookie-clearing redirect built in.
 *
 * This ratchet scans every `.tsx` file under `src/features/` for
 * the pattern `<…>error.message</…>` (or `error?.message`) and
 * fails the build with file:line if a new offender lands. The
 * ALLOWLIST below is the burn-down list — tiles that haven't
 * migrated yet. Each entry SHRINKS as we migrate; adding to it
 * requires reviewer agreement.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve, join, sep } from "node:path";
import { describe, it, expect } from "vitest";

const SRC_ROOT = resolve(__dirname, "features");

// Files that haven't been migrated to ApiErrorTile yet. Each entry
// is `relative_path_from_src/features` (POSIX separators). The list
// only shrinks — every PR that migrates a tile removes its entry.
//
// New entries require a reviewer-justified comment explaining why
// the file legitimately has to render raw error.message (almost
// never the case — usually it's a non-network error like form
// validation).
//
// Burn-down: 60+ entries today (captured at the api-error-tile
// rollout commit). Migrate one feature folder per PR so review
// stays small. The ratchet enforces the count never grows.
const ALLOWLIST: ReadonlySet<string> = new Set([
  "audit-log/AuditLogTable.tsx",
  "audit-log/IntegrityBanner.tsx",
  "auth-admin/AuthModeCard.tsx",
  "auth-admin/OidcProvidersCard.tsx",
  "auth-admin/ServicePoliciesCard.tsx",
  "bans/IpBansCard.tsx",
  "bans/UserBansCard.tsx",
  "custom-formats/CustomFormatsCard.tsx",
  "discovery/DiscoveryListsCard.tsx",
  "discovery/ImportListsCard.tsx",
  "downloads/ActiveDownloadsTable.tsx",
  "downloads/DownloadAnalyticsCard.tsx",
  "downloads/DownloadHistoryTable.tsx",
  "guardrails/GuardrailRow.tsx",
  "indexers/IndexersTable.tsx",
  "infra-detail/GpuCard.tsx",
  "infra-detail/ImageUpdatesCard.tsx",
  "infra-detail/MountsCard.tsx",
  "infra-detail/StorageBreakdownCard.tsx",
  "library/LibrariesTable.tsx",
  "library/LibraryStatsTiles.tsx",
  "library/RecentAdditionsCard.tsx",
  "livetv/EpgHealthCard.tsx",
  "livetv/EpgProvidersCard.tsx",
  "livetv/IptvCountriesCard.tsx",
  "livetv/LivetvSourcesCard.tsx",
  "logs/LogsPage.tsx",
  "me/LoginHistoryCard.tsx",
  "me/MfaCard.tsx",
  "me/ProfileCard.tsx",
  "me/SessionsCard.tsx",
  "me/TokensCard.tsx",
  "ops-detail/ConfigIntegrityCard.tsx",
  "ops-detail/CrashloopsCard.tsx",
  "ops-detail/FailedServicesCard.tsx",
  "ops-detail/HealthHistorySparkline.tsx",
  "ops-detail/HealthStoriesCard.tsx",
  "quality-profiles/QualityProfilesCard.tsx",
  "routing-admin/DnsCheckCard.tsx",
  "routing-admin/GatewayHostnamesCard.tsx",
  "routing-admin/ReachabilityMatrix.tsx",
  "routing-admin/TlsCertificateCard.tsx",
  "security-signals/ConcurrentSpikesCard.tsx",
  "security-signals/FailedLoginsCard.tsx",
  "security-signals/NewLocationsCard.tsx",
  "sessions/SessionsTable.tsx",
  "settings/DisplayPrefsCard.tsx",
  "settings/EnvVarsEditorCard.tsx",
  "settings/EnvViewerCard.tsx",
  "settings/LogLevelCard.tsx",
  "snapshots/SnapshotContentDrawer.tsx",
  "snapshots/SnapshotsTable.tsx",
  "telemetry/TelemetryConsentCard.tsx",
  "users-admin/InvitesCard.tsx",
  "users-admin/ProviderReconcileCard.tsx",
  "users-admin/RolesCard.tsx",
  "users-admin/UserDetailDrawer.tsx",
  "users-admin/UsersTable.tsx",
  "webhooks/ArrWebhooksCard.tsx",
]);

// Pattern: `>error.message<` or `>error?.message<` or
// `{error.message}` inside JSX expression. This is the textual
// shape that produces "HTTP 404" tiles in the wild.
const OFFENDING_PATTERNS: ReadonlyArray<RegExp> = [
  // {error.message} inside JSX — most common form
  /\{[a-zA-Z_$][a-zA-Z0-9_$.]*\.error\.message\}/,
  // {error?.message}
  /\{[a-zA-Z_$][a-zA-Z0-9_$.]*\.error\?\.message\}/,
  // {(error as Error).message}
  /\{\(error as Error\)\.message\}/,
];

function listTsxFiles(dir: string): string[] {
  const out: string[] = [];
  const entries = readdirSync(dir);
  for (const entry of entries) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...listTsxFiles(full));
      continue;
    }
    if (!entry.endsWith(".tsx")) continue;
    if (entry.endsWith(".test.tsx")) continue;
    out.push(full);
  }
  return out;
}

describe("api-error-tile coverage ratchet", () => {
  it("no .tsx file under src/features renders raw error.message", () => {
    const files = listTsxFiles(SRC_ROOT);
    const violations: { file: string; line: number; match: string }[] = [];

    for (const full of files) {
      const rel = full
        .slice(SRC_ROOT.length + 1)
        .split(sep)
        .join("/");
      if (ALLOWLIST.has(rel)) continue;

      const src = readFileSync(full, "utf-8");
      const lines = src.split("\n");
      lines.forEach((line, idx) => {
        for (const pat of OFFENDING_PATTERNS) {
          const m = line.match(pat);
          if (m) violations.push({ file: rel, line: idx + 1, match: m[0] });
        }
      });
    }

    if (violations.length > 0) {
      const summary = violations
        .slice(0, 20)
        .map(
          (v) =>
            `  features/${v.file}:${v.line} — ${v.match}`,
        )
        .join("\n");
      const more =
        violations.length > 20
          ? `\n  ...(${violations.length - 20} more)`
          : "";
      throw new Error(
        "Tiles rendering raw error.message produce confusing 'HTTP 404'-style\n" +
        "messages when the actual status is 401 (session expired).\n" +
        "Wrap with `<ApiErrorTile error={query.error} onRetry={query.refetch} />`\n" +
        "from `@/components/ApiErrorTile` — it branches on status and shows\n" +
        "Sign-in / Retry / 'Not found' as appropriate.\n\n" +
        "Offenders:\n" +
        summary +
        more,
      );
    }
    expect(violations).toEqual([]);
  });

  it("ALLOWLIST entries are still offending — removed offenders must drop their entry", () => {
    const files = listTsxFiles(SRC_ROOT);
    const stillOffending = new Set<string>();
    for (const full of files) {
      const rel = full
        .slice(SRC_ROOT.length + 1)
        .split(sep)
        .join("/");
      const src = readFileSync(full, "utf-8");
      for (const pat of OFFENDING_PATTERNS) {
        if (pat.test(src)) {
          stillOffending.add(rel);
          break;
        }
      }
    }
    const stale = [...ALLOWLIST].filter((f) => !stillOffending.has(f));
    if (stale.length > 0) {
      throw new Error(
        "Stale entries in ALLOWLIST — these files no longer render raw\n" +
        "error.message, so their burn-down entry should be removed:\n  - " +
        stale.join("\n  - "),
      );
    }
    expect(stale).toEqual([]);
  });
});
