import { AlertOctagon, AlertTriangle, CheckCircle2 } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { asArray } from "@/lib/coerce";
import { useValidateMigration } from "./hooks";

/**
 * Pre-upgrade migration safety check card. Renders the
 * `useValidateMigration()` payload as three colour-coded sections:
 *   - blockers (red) — must clear before an upgrade can run
 *   - warnings (amber) — non-blocking but worth seeing
 *   - ok message (green) — all clear, safe to upgrade
 *
 * Returns `null` on error so a flaky probe never poisons the home
 * route. The card is meant to be conditionally rendered by the parent
 * (banner pattern) — when there is nothing to say it does not mount.
 */
export function MigrationCheckCard() {
  const query = useValidateMigration();

  if (query.isLoading) {
    return (
      <Card data-testid="migration-check-card-loading">
        <CardHeader>
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-56" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (query.error || !query.data) {
    // Defensive: the home-route shouldn't surface a noisy error for
    // an optional, ambient pre-flight check. If the endpoint flakes
    // we render nothing.
    return null;
  }

  const blockers = asArray<string>(query.data.blockers);
  const warnings = asArray<string>(query.data.warnings);
  const ok = query.data.ok === true && blockers.length === 0;

  return (
    <Card data-testid="migration-check-card">
      <CardHeader>
        <CardTitle>Pre-upgrade safety check</CardTitle>
        <CardDescription>
          Migration validation against the current configuration
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {blockers.length > 0 ? (
          <div
            role="alert"
            data-testid="migration-check-blockers"
            className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-3 text-sm text-danger"
          >
            <AlertOctagon
              className="mt-0.5 size-4 shrink-0"
              aria-hidden
            />
            <div className="space-y-1">
              <div className="font-medium">
                {blockers.length === 1
                  ? "1 blocker"
                  : `${blockers.length} blockers`}
              </div>
              <ul className="ml-4 list-disc space-y-1">
                {blockers.map((line, i) => (
                  <li key={`blocker-${i}`}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {warnings.length > 0 ? (
          <div
            role="status"
            data-testid="migration-check-warnings"
            className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_10%,transparent)] p-3 text-sm text-warning"
          >
            <AlertTriangle
              className="mt-0.5 size-4 shrink-0"
              aria-hidden
            />
            <div className="space-y-1">
              <div className="font-medium">
                {warnings.length === 1
                  ? "1 warning"
                  : `${warnings.length} warnings`}
              </div>
              <ul className="ml-4 list-disc space-y-1">
                {warnings.map((line, i) => (
                  <li key={`warning-${i}`}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {ok && warnings.length === 0 ? (
          <div
            role="status"
            data-testid="migration-check-ok"
            className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-success)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-success)_10%,transparent)] p-3 text-sm text-success"
          >
            <CheckCircle2
              className="mt-0.5 size-4 shrink-0"
              aria-hidden
            />
            <div className="font-medium">
              Migration safety check passed — safe to upgrade.
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * Helper for the home-route to decide whether to mount the card at
 * all. Returns true iff the validate-migration probe has surfaced
 * anything worth showing (blockers, warnings, or a passing OK).
 */
export function migrationCheckHasContent(
  data: { ok?: boolean; blockers?: readonly string[]; warnings?: readonly string[] } | undefined,
): boolean {
  if (!data) return false;
  const blockers = asArray(data.blockers);
  const warnings = asArray(data.warnings);
  return blockers.length > 0 || warnings.length > 0 || data.ok === true;
}
