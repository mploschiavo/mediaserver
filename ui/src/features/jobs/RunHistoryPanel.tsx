import { useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Loader2,
  SkipForward,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { useRuns, type RunRecordShape } from "./hooks";
import { formatAbsolute, formatElapsed, formatRelative } from "./format";

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
}: RunHistoryPanelProps = {}) {
  const [jobFilter, setJobFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
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
                STATUS_CONFIG[r.status] ?? STATUS_CONFIG.unknown;
              const Icon = cfg.icon;
              return (
                <li
                  key={r.run_id}
                  className="flex items-center gap-2 rounded-md border border-border bg-bg-1 px-2 py-1.5 text-xs"
                  data-testid={`run-history-row-${r.run_id}`}
                  data-status={r.status}
                  data-job={r.job_name}
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
                    className="font-mono text-fg-muted"
                    title={formatAbsolute(r.started_at)}
                  >
                    {formatRelative(epochSecToIso(r.started_at))}
                  </span>
                  <span className="font-mono tabular-nums text-fg-muted">
                    {formatElapsed(r.elapsed)}
                  </span>
                  <span className="font-mono text-[10px] text-fg-faint">
                    {r.triggered_by}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function epochSecToIso(ts: number): string {
  if (!Number.isFinite(ts)) return "";
  return new Date(ts * 1000).toISOString();
}
