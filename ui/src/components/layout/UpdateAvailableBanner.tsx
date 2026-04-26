import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useStackUpdate } from "@/features/stack-lifecycle/hooks";

// Five-minute poll interval. The `useStackUpdate` Tanstack Query
// already polls every 60s (so the operator sees "newer release
// available" without reloading); this component layers on a separate
// foreground-only re-evaluation so we don't churn the controller
// when the tab is backgrounded for hours.
const POLL_INTERVAL_MS = 5 * 60 * 1000;

/**
 * "App is out of date" drift banner.
 *
 * Compares the *running* controller version (returned by
 * `GET /api/stack/update` as `current_version` — the controller's
 * own running release, NOT the upstream `latest_version`) to the
 * SPA's build-time version baked in by Vite (`VITE_BUILD_VERSION`,
 * sourced from `ui/package.json` "version").
 *
 * When they differ, the SPA is running cached HTML/JS that pre-dates
 * the most recent controller deploy. The Refresh button:
 *   1. Unregisters the active service worker (so its precache can no
 *      longer serve the stale shell on the next navigation), and
 *   2. Reloads the page, forcing fresh asset fetches.
 *
 * Renders nothing while the probe is loading, on probe error, when
 * the build version is missing/dev (no `VITE_BUILD_VERSION`), or
 * when the running version matches the build version.
 */
export function UpdateAvailableBanner() {
  const update = useStackUpdate();
  // The stack-update query already polls every 60s, but pause the
  // *banner-side* re-render trigger when the tab is backgrounded so
  // we don't surface an out-of-date toast the moment a user comes
  // back from lunch — we want to bias toward "live data" on
  // refocus. Implementation detail: we just refetch on visibility
  // change + every 5 min when visible.
  const refetch = update.refetch;
  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    let timer: ReturnType<typeof setInterval> | undefined;
    const start = () => {
      if (timer) clearInterval(timer);
      timer = setInterval(() => {
        if (document.visibilityState === "visible") {
          void refetch();
        }
      }, POLL_INTERVAL_MS);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        void refetch();
        start();
      } else if (timer) {
        clearInterval(timer);
        timer = undefined;
      }
    };
    start();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refetch]);

  const [reloading, setReloading] = useState(false);

  const handleRefresh = useCallback(() => {
    setReloading(true);
    // Best-effort SW unregister. The Refresh path must work even if
    // the SW APIs are missing (Safari incognito, locked-down browsers,
    // tests) — fall through to a plain `location.reload()` regardless.
    const finish = () => {
      if (typeof window !== "undefined") {
        window.location.reload();
      }
    };
    try {
      if (
        typeof navigator !== "undefined" &&
        navigator.serviceWorker?.getRegistration
      ) {
        navigator.serviceWorker
          .getRegistration()
          .then((reg) => (reg ? reg.unregister() : undefined))
          .then(finish, finish);
        return;
      }
    } catch {
      // Swallow — fall through to plain reload.
    }
    finish();
  }, []);

  // Hide while the probe is in flight or has errored — we never want
  // a transient network blip to flash a "you're out of date" banner.
  if (update.isLoading || update.error) return null;

  const buildVersion = (import.meta.env.VITE_BUILD_VERSION ?? "").trim();
  const runningVersion = (update.data?.current_version ?? "").trim();

  // The build version isn't injected during dev (vite serves the
  // raw source) and is only meaningful when both sides report a
  // semver string. If either is empty, drift is undefined — render
  // nothing rather than nag.
  if (!buildVersion || !runningVersion) return null;
  if (buildVersion === runningVersion) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="update-available-banner"
      className="border-b border-[color-mix(in_oklab,var(--color-info,#3b82f6)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-info,#3b82f6)_8%,transparent)] px-4 py-2 text-sm sm:px-6"
    >
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-2">
          <RefreshCw
            className="mt-0.5 size-4 shrink-0 text-fg-muted"
            aria-hidden
          />
          <span className="text-fg">
            A new version{" "}
            <span
              className="font-mono"
              data-testid="update-available-banner-version"
            >
              ({runningVersion})
            </span>{" "}
            is available. Refresh to update.
          </span>
        </div>
        <Button
          variant="secondary"
          size="sm"
          loading={reloading}
          onClick={handleRefresh}
          data-testid="update-available-banner-refresh"
        >
          Refresh
        </Button>
      </div>
    </div>
  );
}
