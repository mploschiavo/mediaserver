import { useMemo, useState, type JSX } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  CornerDownRight,
  CornerLeftUp,
  Loader2,
  SkipForward,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { useRuns, type RunRecordShape } from "./hooks";
import { epochToIso, formatAbsolute, formatElapsed, formatRelative } from "./format";
import { RunDrawer } from "./RunDrawer";

const RUN_ID_PREFIX_LEN = 8;

// Anomaly tint thresholds — z-score units (number of standard
// deviations above the rolling mean). Below ANOMALY_AMBER, the row
// renders normally; between AMBER and RED, the row gets a warning
// tone; at-or-above RED, a danger tone. Only the *high* tail flags;
// runs that finish faster than baseline (negative z) are good news,
// not anomalies, so we don't tint them.
const ANOMALY_AMBER = 1;
const ANOMALY_RED = 2;

type AnomalyTone = "" | "warn" | "err";

function anomalyTone(score: number | null | undefined): AnomalyTone {
  if (score == null) return "";
  if (score >= ANOMALY_RED) return "err";
  if (score >= ANOMALY_AMBER) return "warn";
  return "";
}

function anomalyTooltip(score: number | null | undefined): string {
  if (score == null) return "";
  return `Slower than baseline by ${score.toFixed(1)}σ`;
}

/**
 * Phase-2 cross-job run history. Reads `GET /api/runs` and surfaces the
 * last N records — one row per ULID, with status, who triggered it,
 * elapsed, and the job name. Designed to live below the two-pane in
 * JobsPage so the operator can scan the last hour of activity without
 * drilling into a specific job's detail panel.
 *
 * Filters are local to the panel: the controller-side filtering is
 * already exercised in the hooks, but the operator typically wants to
 * narrow the *visible* slice of an already-fetched window. We filter
 * client-side for snappiness; the controller cap (50000) ensures the
 * working set stays bounded.
 */
export interface RunHistoryPanelProps {
  /** Default limit threaded into `useRuns`. Overridden in tests. */
  defaultLimit?: number;
}

const STATUS_OPTIONS: readonly {
  value: string;
  label: string;
}[] = [
  { value: "all", label: "All statuses" },
  { value: "running", label: "Running" },
  { value: "ok", label: "OK" },
  { value: "skipped", label: "Skipped" },
  { value: "error", label: "Error" },
  { value: "cancelled", label: "Cancelled" },
  { value: "timeout", label: "Timeout" },
];

const STATUS_DEFAULT = {
  variant: "default" as const,
  icon: Activity,
};
const STATUS_CONFIG: Record<
  string,
  {
    variant: "success" | "danger" | "warning" | "info" | "outline" | "default";
    icon: typeof CheckCircle2;
  }
> = {
  running: { variant: "info", icon: Loader2 },
  ok: { variant: "success", icon: CheckCircle2 },
  skipped: { variant: "warning", icon: SkipForward },
  error: { variant: "danger", icon: XCircle },
  cancelled: { variant: "outline", icon: AlertCircle },
  timeout: { variant: "danger", icon: AlertCircle },
  unknown: { variant: "default", icon: Activity },
};

function applyFilters(
  runs: readonly RunRecordShape[],
  jobNeedle: string,
  status: string,
): readonly RunRecordShape[] {
  const needle = jobNeedle.trim().toLowerCase();
  return runs.filter((r) => {
    if (status !== "all" && r.status !== status) return false;
    if (needle && !r.job_name.toLowerCase().includes(needle)) return false;
    return true;
  });
}

export function RunHistoryPanel({
  defaultLimit = 100,
}: RunHistoryPanelProps = {}): JSX.Element {
  const [jobFilter, setJobFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const runsQuery = useRuns({ limit: defaultLimit });

  const filtered = useMemo(
    () => applyFilters(runsQuery.data ?? [], jobFilter, statusFilter),
    [runsQuery.data, jobFilter, statusFilter],
  );

  return (
    <Card data-testid="run-history-panel">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm">Recent runs</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              placeholder="Filter by job name…"
              value={jobFilter}
              onChange={(e) => setJobFilter(e.target.value)}
              data-testid="run-history-job-filter"
              className="rounded-md border border-border bg-bg-1 px-2 py-1 font-mono text-xs text-fg outline-none focus:border-accent"
            />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              data-testid="run-history-status-filter"
              className="rounded-md border border-border bg-bg-1 px-2 py-1 text-xs text-fg outline-none focus:border-accent"
            >
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {runsQuery.isLoading ? (
          <div data-testid="run-history-loading" className="flex flex-col gap-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : runsQuery.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="run-history-error"
          >
            Couldn't load run history: {(runsQuery.error as Error).message}
          </p>
        ) : filtered.length === 0 ? (
          <p
            className="text-sm text-fg-faint"
            data-testid="run-history-empty"
          >
            {(runsQuery.data?.length ?? 0) === 0
              ? "No recorded runs yet."
              : "No runs match the current filter."}
          </p>
        ) : (
          <ul
            className="flex flex-col gap-1"
            data-testid="run-history-list"
          >
            {filtered.map((r) => {
              const cfg =
                STATUS_CONFIG[r.status] ??
                STATUS_CONFIG.unknown ??
                STATUS_DEFAULT;
              const Icon = cfg.icon;
              const childCount = r.child_run_ids.length;
              const hasParent = Boolean(r.parent_run_id);
              const tone = anomalyTone(r.anomaly_score);
              return (
                <li
                  key={r.run_id}
                  data-testid={`run-history-row-${r.run_id}`}
                  data-status={r.status}
                  data-job={r.job_name}
                  data-has-parent={hasParent ? "true" : "false"}
                  data-child-count={childCount}
                  data-tone={tone || undefined}
                  title={anomalyTooltip(r.anomaly_score)}
                >
                  <button
                    type="button"
                    onClick={() => setSelectedRunId(r.run_id)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md border bg-bg-1 px-2 py-1.5 text-left text-xs [@media(hover:hover)]:hover:bg-bg-2",
                      tone === "err" &&
                        "border-danger/40 bg-danger/5",
                      tone === "warn" &&
                        "border-warning/40 bg-warning/5",
                      tone === "" && "border-border",
                    )}
                    data-testid={`run-history-row-button-${r.run_id}`}
                  >
                    <Badge
                      variant={cfg.variant}
                      className={cn(
                        "inline-flex items-center gap-1 px-1.5 py-0 text-[10px]",
                      )}
                    >
                      <Icon aria-hidden className="size-2.5" />
                      {r.status}
                    </Badge>
                    <span className="flex-1 truncate font-medium text-fg">
                      {r.job_name}
                    </span>
                    <span
                      className="hidden font-mono text-[10px] text-fg-faint sm:inline"
                      title={r.run_id}
                    >
                      {r.run_id.slice(0, RUN_ID_PREFIX_LEN)}
                    </span>
                    {hasParent ? (
                      <CornerLeftUp
                        aria-label="has parent run"
                        className="size-3 text-fg-faint"
                      />
                    ) : null}
                    {childCount > 0 ? (
                      <span
                        className="inline-flex items-center gap-0.5 font-mono text-[10px] text-fg-faint"
                        title={`${childCount} child run${childCount === 1 ? "" : "s"}`}
                      >
                        <CornerDownRight aria-hidden className="size-3" />
                        {childCount}
                      </span>
                    ) : null}
                    <span
                      className="font-mono text-fg-muted"
                      title={formatAbsolute(r.started_at)}
                    >
                      {formatRelative(epochToIso(r.started_at))}
                    </span>
                    <span className="font-mono tabular-nums text-fg-muted">
                      {formatElapsed(r.elapsed)}
                    </span>
                    <span className="font-mono text-[10px] text-fg-faint">
                      {r.triggered_by}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
      <RunDrawer
        runId={selectedRunId}
        onClose={() => setSelectedRunId(null)}
        onSelectRunId={(id) => setSelectedRunId(id)}
      />
    </Card>
  );
}

