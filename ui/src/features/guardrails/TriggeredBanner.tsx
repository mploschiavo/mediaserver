import { ShieldAlert } from "lucide-react";
import { cn } from "@/lib/cn";
import { useGuardrails } from "./hooks";

// We use a plain anchor (rather than Tanstack Router's <Link>) so
// this component can mount under any route in tests without the
// router's strict route-id typing at the call site. Same-origin
// navigations to a hard-coded route inside the SPA don't trigger
// a full page reload because the router intercepts pushState
// internally; if it ever did, the cost is one extra paint, not
// a correctness bug.
function buildHref(id: string): string {
  return `/guardrails?focus=${encodeURIComponent(id)}`;
}

/**
 * Persistent banner that warns when any guardrail is firing at
 * severity warning+ . Click navigates to /guardrails?focus=<id> for
 * the worst offender. Renders nothing on healthy state so the chrome
 * is invisible during normal operation.
 */
export function TriggeredBanner() {
  const query = useGuardrails();
  const rules = query.data?.guardrails ?? [];
  const triggered = rules.filter(
    (r) =>
      !r.disabled &&
      (r.last_status === "warning" || r.last_status === "critical"),
  );
  if (triggered.length === 0) return null;
  // Worst severity first (critical > warning), then earliest fire so
  // the banner targets the longest-standing problem.
  const sorted = [...triggered].sort((a, b) => {
    const sevRank = (s?: string) => (s === "critical" ? 0 : 1);
    const sa = sevRank(a.last_status);
    const sb = sevRank(b.last_status);
    if (sa !== sb) return sa - sb;
    return (a.last_triggered_at ?? 0) - (b.last_triggered_at ?? 0);
  });
  const worst = sorted[0];
  // `triggered.length > 0` was just checked, so `sorted[0]` exists; narrow
  // for the strict-TS compiler in `tsc -b` (esbuild/vitest don't catch it).
  if (!worst) return null;
  const isCritical = worst.last_status === "critical";

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "border-b px-4 py-2 text-sm sm:px-6",
        isCritical
          ? "border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] text-danger"
          : "border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_10%,transparent)] text-warning",
      )}
      data-testid="guardrails-triggered-banner"
    >
      <a
        href={buildHref(worst.id)}
        className="flex flex-wrap items-center gap-2 underline-offset-2 hover:underline"
      >
        <ShieldAlert className="size-4 shrink-0" aria-hidden />
        <span>
          {triggered.length} guardrail
          {triggered.length === 1 ? "" : "s"} firing —{" "}
          <span className="font-mono">{worst.id}</span>
        </span>
        <span className="text-xs opacity-80">
          ·{" "}
          <span title="Guardrails are continuous evaluations — they re-evaluate every interval (default 5min) and fire as long as the condition holds. They aren't 'jobs' that finish.">
            re-evaluating every{" "}
            {formatInterval(query.data?.evaluation_interval_seconds)}
          </span>
          {" · "}
          firing for {formatFiringFor(worst.last_triggered_at)}
          {worst.last_triggered_at ? (
            <>
              {" "}
              <span className="text-fg-faint">
                (since {formatStartedAt(worst.last_triggered_at)})
              </span>
            </>
          ) : null}
        </span>
      </a>
    </div>
  );
}

function formatInterval(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return "5 min";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}min`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function formatFiringFor(triggeredAt?: number | null): string {
  if (!triggeredAt) return "an unknown duration";
  const elapsed = Date.now() / 1000 - triggeredAt;
  if (elapsed < 60) return "<1min";
  if (elapsed < 3600) return `${Math.round(elapsed / 60)}min`;
  if (elapsed < 86400) return `${(elapsed / 3600).toFixed(1)}h`;
  return `${(elapsed / 86400).toFixed(1)}d`;
}

/**
 * Format a started-at epoch as a user-readable timestamp:
 *   * Same calendar day → "14:32" (operator knows it's today)
 *   * Yesterday         → "yesterday 14:32"
 *   * Older this year   → "Apr 25 14:32"
 *   * Older year        → "2025-04-26 14:32"
 *
 * Locale-aware via toLocale*String — respects the operator's
 * 12h/24h preference. Shared across long-running banners
 * (TriggeredBanner / RunningJobsBanner) so "since" formatting
 * is consistent.
 */
function formatStartedAt(epoch: number): string {
  const start = new Date(epoch * 1000);
  const now = new Date();
  const time = start.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (start.toDateString() === now.toDateString()) return time;
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (start.toDateString() === yesterday.toDateString()) {
    return `yesterday ${time}`;
  }
  const sameYear = start.getFullYear() === now.getFullYear();
  if (sameYear) {
    return (
      start.toLocaleDateString([], { month: "short", day: "numeric" }) +
      ` ${time}`
    );
  }
  return (
    start.toLocaleDateString([], {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }) + ` ${time}`
  );
}
