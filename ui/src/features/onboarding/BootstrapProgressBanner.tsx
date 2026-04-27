import { useQuery } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { fetcher } from "@/api/client";

/**
 * First-run progress banner. Polls ``/status`` while the controller's
 * initial bootstrap is in flight; renders nothing once
 * ``initial_bootstrap_done`` flips to true. The point is to give a
 * fresh-deploy operator a friendly "stack is starting up" surface
 * instead of a half-broken dashboard with empty tiles + 401 noise.
 *
 * Per project memory: "the controller runs a job that uses defaults
 * to configure and wire everything for a default system. The only
 * thing onboard means is some minor tweaks to existing defaults" —
 * this banner is the visibility surface, NOT a wizard. No setup
 * choices, no input fields. Just progress.
 */
export function BootstrapProgressBanner() {
  const status = useQuery<BootstrapStatus>({
    queryKey: ["controller", "status"],
    queryFn: () => fetcher<BootstrapStatus>("status"),
    // Faster cadence than the dashboard's 30s default — the bootstrap
    // window is short, so we want the bar to feel responsive.
    refetchInterval: 2000,
    // Once bootstrap completes the banner self-trims; no need to keep
    // hitting /status forever.
    refetchIntervalInBackground: false,
    staleTime: 1000,
  });

  const data = status.data;
  if (!data) return null;
  if (data.initial_bootstrap_done && data.phase === "complete") return null;

  const phaseDisplay =
    typeof data.phase === "string" && data.phase ? data.phase : "starting";
  const elapsed = Math.max(0, computeElapsed(data));
  const stepLabel = currentStepLabel(data);
  const progress = estimateProgress(data);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="bootstrap-progress-banner"
      className="rounded-md border border-info/40 bg-info/10 p-4"
    >
      <div className="flex items-start gap-3">
        <Loader2
          aria-hidden
          className="mt-0.5 size-4 shrink-0 animate-spin text-info"
        />
        <div className="flex flex-1 flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-fg">
                <Sparkles aria-hidden className="size-3.5 text-info" />
                First-time setup in progress
              </div>
              <div className="text-xs text-fg-muted">
                {stepLabel}
              </div>
            </div>
            <div className="text-right tabular-nums">
              <div className="text-xs uppercase tracking-wide text-fg-faint">
                Phase
              </div>
              <div
                className="font-mono text-xs text-fg"
                data-testid="bootstrap-progress-banner-phase"
              >
                {phaseDisplay}
              </div>
            </div>
          </div>
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-bg-3"
            data-testid="bootstrap-progress-banner-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress * 100)}
          >
            <div
              className="h-full rounded-full bg-info transition-all"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[11px] text-fg-faint tabular-nums">
            <span>
              Bootstrap is automatic — no action required. Dashboard
              unlocks once all phases complete.
            </span>
            <span data-testid="bootstrap-progress-banner-elapsed">
              {formatElapsed(elapsed)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

interface ActionRecord {
  id?: string;
  name?: string;
  status?: string;
  started_at?: number;
  completed_at?: number | null;
  elapsed_seconds?: number | null;
}

interface BootstrapStatus {
  phase?: string;
  initial_bootstrap_done?: boolean;
  current_action?: ActionRecord | null;
  action_history?: ActionRecord[];
}

function currentStepLabel(d: BootstrapStatus): string {
  const cur = d.current_action;
  if (cur && cur.name) {
    return `Running ${cur.name}…`;
  }
  if (Array.isArray(d.action_history) && d.action_history.length > 0) {
    const last = d.action_history[d.action_history.length - 1];
    if (last && last.name) {
      return `Last finished: ${last.name}`;
    }
  }
  return "Waiting for the controller to pick up the bootstrap job…";
}

function computeElapsed(d: BootstrapStatus): number {
  const cur = d.current_action;
  if (cur && cur.started_at) {
    return Date.now() / 1000 - cur.started_at;
  }
  if (Array.isArray(d.action_history) && d.action_history.length > 0) {
    const first = d.action_history[0];
    if (first && first.started_at) {
      return Date.now() / 1000 - first.started_at;
    }
  }
  return 0;
}

/** Heuristic 0..1 progress: phase=running with current action ≈ 50 %,
 *  phase=complete = 1, phase=error = 1 (terminal), idle = 0. */
function estimateProgress(d: BootstrapStatus): number {
  if (d.phase === "complete") return 1;
  if (d.phase === "error") return 1;
  const history = Array.isArray(d.action_history) ? d.action_history : [];
  // Each completed action contributes some weight; bootstrap is
  // historically about 4-6 actions long, so saturate around 5.
  const completed = history.filter(
    (a) => a && a.status === "complete",
  ).length;
  const ratio = Math.min(0.9, completed / 5);
  // Add a small bump when a current_action is in flight.
  return d.current_action ? Math.max(ratio, 0.1) + 0.1 : ratio;
}

function formatElapsed(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m ${(s % 60).toFixed(0)}s`;
  return `${(s / 3600).toFixed(1)}h`;
}
