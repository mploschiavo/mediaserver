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
        className="flex items-center gap-2 underline-offset-2 hover:underline"
      >
        <ShieldAlert className="size-4 shrink-0" aria-hidden />
        <span>
          {triggered.length} guardrail
          {triggered.length === 1 ? "" : "s"} firing —{" "}
          <span className="font-mono">{worst.id}</span>
        </span>
      </a>
    </div>
  );
}
