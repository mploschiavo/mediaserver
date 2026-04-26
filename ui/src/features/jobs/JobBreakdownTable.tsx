import { Badge } from "@/components/ui/badge";
import { formatElapsed } from "./format";

/**
 * One row in the per-batch breakdown shown by the JobHistoryPanel
 * drawer. Mirrors what `rowsForBatch` in JobHistoryPanel emits.
 */
export interface JobBreakdownRow {
  name: string;
  service: string | undefined;
  status: string;
  elapsed: number | undefined;
  error: string | undefined;
}

interface JobBreakdownTableProps {
  rows: readonly JobBreakdownRow[];
}

function statusBadge(status: string) {
  if (status === "ok") return <Badge variant="success">ok</Badge>;
  if (status === "skipped") return <Badge variant="warning">skipped</Badge>;
  if (status === "error" || status === "errors" || status === "failed")
    return <Badge variant="danger">error</Badge>;
  return <Badge variant="outline">{status || "—"}</Badge>;
}

/**
 * Per-job breakdown of a single batch, rendered inside the
 * JobHistoryPanel's Vaul drawer. Intentionally a raw `<table>` rather
 * than `<DataTable>`: this is a drawer-internal breakdown that opens
 * on row-click, not a sort/filter scenario. Allowlisted in the
 * DataTable coverage ratchet for that reason.
 */
export function JobBreakdownTable({ rows }: JobBreakdownTableProps) {
  return (
    <table
      className="w-full text-sm"
      data-testid="job-history-breakdown"
    >
      <thead>
        <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-fg-muted">
          <th className="py-2 font-medium">Job</th>
          <th className="py-2 font-medium">Status</th>
          <th className="py-2 text-right font-medium">Elapsed</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={row.name}
            className="border-b border-border/60 last:border-b-0 align-top"
            data-testid={`job-history-breakdown-row-${row.name}`}
          >
            <td className="py-1.5 pr-2">
              <div className="flex flex-wrap items-center gap-1.5">
                {row.service ? (
                  <span
                    className="inline-flex items-center rounded-md border border-border bg-bg-2 px-1.5 py-0 text-[10px] uppercase tracking-wide text-fg-muted"
                    data-testid={`job-history-breakdown-service-${row.name}`}
                    title={`Service: ${row.service}`}
                  >
                    {row.service}
                  </span>
                ) : null}
                <span className="font-mono text-xs">{row.name}</span>
              </div>
              {row.error ? (
                <div className="mt-0.5 text-xs text-danger">
                  {row.error}
                </div>
              ) : null}
            </td>
            <td className="py-1.5">{statusBadge(row.status)}</td>
            <td className="py-1.5 text-right font-mono tabular-nums text-fg-muted">
              {formatElapsed(row.elapsed)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
