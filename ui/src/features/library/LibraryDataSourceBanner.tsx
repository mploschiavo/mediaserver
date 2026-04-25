import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, X } from "lucide-react";
import { useHealth } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { asObjectMap } from "@/lib/coerce";
import type { HealthShape } from "@/api";
import { useLibraries, type LibrariesResponse } from "./hooks";

const DISMISS_KEY = "media-stack:library-defaults-banner-dismissed";

/**
 * Read the per-tab dismissal flag. Stored in `localStorage` keyed
 * by `media-stack:library-defaults-banner-dismissed`, but cleared
 * on tab close (the spec calls this "valid for the current
 * session"). To approximate that with the localStorage key, we
 * stamp the value with the page's start timestamp; on remount
 * after a tab close, the start timestamp is fresh and we ignore
 * the stale flag.
 */
function readDismissed(sessionStart: number): boolean {
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY);
    if (!raw) return false;
    const ts = Number.parseInt(raw, 10);
    if (!Number.isFinite(ts)) return false;
    // Same session if the stored ts is within the same `sessionStart`.
    return ts === sessionStart;
  } catch {
    return false;
  }
}

function persistDismissed(sessionStart: number): void {
  try {
    window.localStorage.setItem(DISMISS_KEY, String(sessionStart));
  } catch {
    // Storage failures are non-fatal — the banner just re-renders next mount.
  }
}

/**
 * Per-page session marker. Computed once at module load so all
 * banner instances on the same tab share the same value; the value
 * is gone after a tab close, satisfying the "current session only"
 * requirement.
 */
const SESSION_START = Date.now();

/**
 * Pick out Jellyfin's status from the `/api/health` payload. The
 * documented shape is `{ services: { jellyfin: { status: "ok" } } }`;
 * older builds used `{ jellyfin: "up" }` flat fields. We accept
 * either via `asObjectMap`.
 */
export function isJellyfinReachable(
  health: HealthShape | undefined,
): boolean {
  if (!health) return false;
  // `HealthShape` declares `[key: string]: unknown`, so `health.services`
  // is statically `unknown`. `asObjectMap` defends against any payload
  // that comes back as a string / null / array.
  const services = asObjectMap(health.services);
  const jf = asObjectMap(services.jellyfin);
  const status = typeof jf.status === "string" ? jf.status.toLowerCase() : "";
  if (status === "ok" || status === "up" || status === "reachable") return true;
  // Flat-field fallback for older controller builds.
  const flat = health.jellyfin;
  if (typeof flat === "string") {
    const s = flat.toLowerCase();
    return s === "ok" || s === "up" || s === "reachable";
  }
  return false;
}

/**
 * Trigger predicate: the banner only renders when the libraries
 * response was sourced from `defaults` AND Jellyfin is up. Both
 * sides are required — falling back to defaults while Jellyfin is
 * down is expected (and covered elsewhere).
 */
export function shouldShowBanner(
  libraries: LibrariesResponse | undefined,
  health: HealthShape | undefined,
): boolean {
  if (!libraries) return false;
  if (libraries.source !== "defaults") return false;
  return isJellyfinReachable(health);
}

/**
 * Yellow banner mounted above the Library tab content when the
 * `/api/libraries` payload is sourced from defaults but Jellyfin is
 * reachable. Surfaces the operator's most-likely-cause (missing
 * `JELLYFIN_API_KEY`) and links to the discover-api-keys job.
 *
 * Per-session dismissible: the dismissal flag is keyed against the
 * tab's session start timestamp, so a tab close clears it.
 */
export function LibraryDataSourceBanner() {
  const libraries = useLibraries();
  const health = useHealth();
  const [dismissed, setDismissed] = useState<boolean>(() =>
    readDismissed(SESSION_START),
  );

  // Keep the dismissed flag fresh if storage changes (e.g. another
  // tab dismissed the banner). Storage events fire cross-tab, not
  // same-tab, so this is purely best-effort.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === DISMISS_KEY) {
        setDismissed(readDismissed(SESSION_START));
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  if (dismissed) return null;
  if (!shouldShowBanner(libraries.data, health.data)) return null;

  const handleDismiss = () => {
    persistDismissed(SESSION_START);
    setDismissed(true);
  };

  return (
    <div
      role="alert"
      data-testid="library-defaults-banner"
      className="flex flex-col gap-3 rounded-md border border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_12%,transparent)] p-4 text-sm text-warning sm:flex-row sm:items-start"
    >
      <AlertTriangle
        aria-hidden
        className="mt-0.5 size-4 shrink-0"
      />
      <div className="flex-1 space-y-2">
        <p className="font-medium">
          Library counts are showing the bootstrap-profile defaults.
        </p>
        <p className="text-xs text-fg-muted">
          Jellyfin is reachable but no API key is configured for the
          controller — populate{" "}
          <code className="rounded bg-bg-2 px-1 font-mono text-fg">
            JELLYFIN_API_KEY
          </code>{" "}
          in{" "}
          <code className="rounded bg-bg-2 px-1 font-mono text-fg">
            media-stack-secrets
          </code>{" "}
          (or wait for the next{" "}
          <code className="rounded bg-bg-2 px-1 font-mono text-fg">
            discover-api-keys
          </code>{" "}
          job). Counts won't reflect reality until then.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {/* Deep-link to /jobs?filter=discover-api-keys; the jobs UX
              agent is adding the same query-param pattern to /logs
              and /audit-log. We use a plain anchor here so we don't
              touch routeTree or Sidebar. */}
          <a
            href="/jobs?filter=discover-api-keys"
            data-testid="library-defaults-banner-diagnose"
            className="inline-flex items-center gap-1 rounded-md border border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-bg-1 px-2.5 py-1 text-xs font-medium text-fg outline-none transition-colors hover:bg-bg-2 focus-visible:ring-2 focus-visible:ring-ring"
          >
            Diagnose
            <ExternalLink aria-hidden className="size-3" />
          </a>
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={handleDismiss}
        data-testid="library-defaults-banner-dismiss"
        aria-label="Dismiss banner for this session"
        className="self-start"
      >
        <X aria-hidden className="size-3.5" />
      </Button>
    </div>
  );
}

// Exported for tests so they can clear the per-session flag without
// reaching into localStorage internals.
export const __INTERNAL__ = { DISMISS_KEY, SESSION_START };
