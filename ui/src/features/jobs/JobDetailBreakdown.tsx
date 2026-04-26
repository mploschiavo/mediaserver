import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import {
  epochToIso,
  formatAbsolute,
  formatElapsed,
  formatRelative,
} from "./format";

/**
 * One row in the job-detail "Last N runs" breakdown. The shape is
 * deliberately flat (no JobHistoryEntry coupling) so this component
 * stays a pure presentational sub-tree.
 */
export interface JobDetailBreakdownRow {
  ts: number | undefined;
  status: string;
  elapsed: number | undefined;
  source?: string;
}

interface JobDetailBreakdownProps {
  rows: readonly JobDetailBreakdownRow[];
}

function statusBadge(status: string) {
  if (status === "ok") return <Badge variant="success">ok</Badge>;
  if (status === "skipped") return <Badge variant="warning">skipped</Badge>;
  if (status === "error" || status === "errors" || status === "failed")
    return <Badge variant="danger">error</Badge>;
  return <Badge variant="outline">{status || "—"}</Badge>;
}

function sourceBadge(source: string | undefined) {
  if (!source) return null;
  const tone =
    source === "cron"
      ? "border-info/40 bg-info/10 text-info"
      : source === "manual"
        ? "border-accent/40 bg-accent/10 text-accent"
        : "border-warning/40 bg-warning/10 text-warning";
  return (
    <span
      className={cn(
        "ml-1.5 inline-flex items-center rounded-md border px-1.5 py-0 text-[10px] uppercase tracking-wide",
        tone,
      )}
      data-testid={`job-detail-source-${source}`}
      title={`Triggered by ${source}`}
    >
      {source}
    </span>
  );
}

/**
 * Per-run breakdown table rendered inside the JobDetailPanel's "Last
 * N runs" card. Intentionally a raw `<table>` rather than `<DataTable>`:
 * this is a fixed-width breakdown (3 columns, capped at ~10 rows) inside
 * a side card, not a sort/filter scenario. Allowlisted in the DataTable
 * coverage ratchet for that reason.
 */
export function JobDetailBreakdown({ rows }: JobDetailBreakdownProps) {
  return (
    <div className="overflow-x-auto" data-testid="job-detail-runs-table">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-fg-muted">
            <th className="py-2 font-medium">When</th>
            <th className="py-2 font-medium">Status</th>
            <th className="py-2 text-right font-medium">Elapsed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={`${row.ts ?? "x"}-${i}`}
              className="border-b border-border/60 last:border-b-0"
            >
              <td
                className="py-1.5 tabular-nums text-fg-muted"
                title={formatAbsolute(row.ts)}
              >
                {formatRelative(epochToIso(row.ts))}
                {sourceBadge(row.source)}
              </td>
              <td className="py-1.5">{statusBadge(row.status)}</td>
              <td className="py-1.5 text-right font-mono tabular-nums text-fg-muted">
                {formatElapsed(row.elapsed)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
