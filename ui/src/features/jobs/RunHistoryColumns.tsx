import {
  Activity,
  AlertCircle,
  CheckCircle2,
  CornerDownRight,
  CornerLeftUp,
  Loader2,
  ScrollText,
  SkipForward,
  XCircle,
} from "lucide-react";
import { Link } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import type { RunRecordShape } from "./hooks";
import {
  epochToIso,
  formatAbsolute,
  formatElapsed,
  formatRelative,
} from "./format";

/**
 * Column definitions for the Recent runs DataTable. Lives in its
 * own module so ``RunHistoryPanel.tsx`` stays under the
 * ``react-components-over-300-lines`` ratchet — the column array
 * grew large enough to push the panel file over the threshold,
 * and the columns are pure presentation helpers (no React state)
 * that don't belong inside the panel component anyway.
 */

const RUN_ID_PREFIX_LEN = 8;
const ANOMALY_AMBER = 1;
const ANOMALY_RED = 2;

export type AnomalyTone = "" | "warn" | "err";

export function anomalyTone(score: number | null | undefined): AnomalyTone {
  if (score == null) return "";
  if (score >= ANOMALY_RED) return "err";
  if (score >= ANOMALY_AMBER) return "warn";
  return "";
}

export function anomalyTooltip(score: number | null | undefined): string {
  if (score == null) return "";
  return `Slower than baseline by ${score.toFixed(1)}σ`;
}

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
const STATUS_DEFAULT = { variant: "default" as const, icon: Activity };

export function buildRunHistoryColumns(): ColumnDef<RunRecordShape, unknown>[] {
  return [
    {
      id: "status",
      header: "Status",
      accessorFn: (r) => r.status,
      enableColumnFilter: true,
      cell: ({ row }) => {
        const r = row.original;
        const cfg =
          STATUS_CONFIG[r.status] ?? STATUS_CONFIG["unknown"] ?? STATUS_DEFAULT;
        const Icon = cfg.icon;
        return (
          <Badge
            variant={cfg.variant}
            className="inline-flex items-center gap-1 px-1.5 py-0 text-[10px]"
            data-testid={`run-history-status-${r.run_id}`}
          >
            <Icon aria-hidden className="size-2.5" />
            {r.status}
          </Badge>
        );
      },
    },
    {
      id: "job_name",
      header: "Job",
      accessorFn: (r) => r.job_name,
      enableColumnFilter: true,
      cell: ({ row }) => {
        const r = row.original;
        return (
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-medium text-fg">{r.job_name}</span>
            {r.parent_job_name ? (
              <span
                className="truncate text-[10px] text-fg-muted"
                data-testid={`run-history-parent-${r.run_id}`}
              >
                <CornerLeftUp
                  aria-hidden
                  className="-mt-0.5 mr-0.5 inline-block size-2.5"
                />
                under {r.parent_job_name}
              </span>
            ) : null}
          </div>
        );
      },
    },
    {
      id: "run_id",
      header: "Run",
      accessorFn: (r) => r.run_id,
      enableColumnFilter: true,
      cell: ({ row }) => {
        const r = row.original;
        return (
          <span
            className="font-mono text-[10px] text-fg-faint"
            title={r.run_id}
          >
            {r.run_id.slice(0, RUN_ID_PREFIX_LEN)}
            {r.child_run_ids.length > 0 ? (
              <span
                className="ml-1 inline-flex items-center gap-0.5"
                title={`${r.child_run_ids.length} child run${
                  r.child_run_ids.length === 1 ? "" : "s"
                }`}
              >
                <CornerDownRight aria-hidden className="size-2.5" />
                {r.child_run_ids.length}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "started_at",
      header: "Started",
      accessorFn: (r) => r.started_at,
      sortingFn: "basic",
      enableColumnFilter: false,
      cell: ({ row }) => {
        const r = row.original;
        return (
          <span
            className="font-mono tabular-nums text-fg-muted"
            title={formatAbsolute(r.started_at)}
          >
            {formatRelative(epochToIso(r.started_at))}
          </span>
        );
      },
    },
    {
      id: "elapsed",
      header: "Elapsed",
      accessorFn: (r) => r.elapsed ?? 0,
      sortingFn: "basic",
      enableColumnFilter: false,
      cell: ({ row }) => (
        <span className="font-mono tabular-nums text-fg-muted">
          {formatElapsed(row.original.elapsed)}
        </span>
      ),
    },
    {
      id: "triggered_by",
      header: "Trigger",
      accessorFn: (r) => r.triggered_by,
      enableColumnFilter: true,
      cell: ({ row }) => (
        <Badge
          variant="outline"
          className="px-1.5 py-0 text-[10px] tracking-tight"
          data-testid={`run-history-trigger-${row.original.run_id}`}
        >
          via {row.original.triggered_by}
        </Badge>
      ),
    },
    {
      id: "anomaly_score",
      header: "Anomaly",
      accessorFn: (r) => r.anomaly_score ?? null,
      enableColumnFilter: false,
      sortingFn: (a, b) => {
        const sa = a.original.anomaly_score ?? -Infinity;
        const sb = b.original.anomaly_score ?? -Infinity;
        return sa - sb;
      },
      cell: ({ row }) => {
        const r = row.original;
        if (r.anomaly_score == null) {
          return <span className="text-fg-faint">—</span>;
        }
        const tone = anomalyTone(r.anomaly_score);
        return (
          <span
            className={
              tone === "err"
                ? "font-mono tabular-nums text-danger"
                : tone === "warn"
                  ? "font-mono tabular-nums text-warning"
                  : "font-mono tabular-nums text-fg-muted"
            }
            title={anomalyTooltip(r.anomaly_score)}
          >
            {r.anomaly_score >= 0 ? "+" : ""}
            {r.anomaly_score.toFixed(1)}σ
          </span>
        );
      },
    },
    {
      id: "logs",
      header: "Logs",
      enableColumnFilter: false,
      enableSorting: false,
      cell: ({ row }) => {
        const r = row.original;
        return (
          <Link
            to="/logs"
            search={{
              service: r.log_anchor?.source ?? "controller",
              action: r.log_anchor?.action ?? r.job_name,
              since: r.log_anchor?.since_iso ?? undefined,
              limit: 5000,
            }}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex size-7 items-center justify-center rounded-md border border-border bg-bg-1 text-fg-muted [@media(hover:hover)]:hover:bg-bg-2 [@media(hover:hover)]:hover:text-fg"
            title={`View logs for ${r.job_name} run`}
            aria-label={`View logs for ${r.job_name} run ${r.run_id}`}
            data-testid={`run-history-logs-${r.run_id}`}
          >
            <ScrollText aria-hidden className="size-3.5" />
          </Link>
        );
      },
    },
  ];
}
