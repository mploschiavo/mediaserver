import { useMemo, useState } from "react";
import { Drawer as VaulDrawer } from "vaul";
import { motion, useReducedMotion } from "framer-motion";
import { History, X } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "@/components/data-table";
import { EmptyState } from "@/components/layout/EmptyState";
import { cn } from "@/lib/cn";
import { asArray } from "@/lib/coerce";
import type { JobHistoryEntry, JobHistoryJobResult, JobMeta } from "./hooks";
import {
  epochToIso,
  formatAbsolute,
  formatElapsed,
  formatRelative,
} from "./format";
import {
  JobBreakdownTable,
  type JobBreakdownRow,
} from "./JobBreakdownTable";

interface JobHistoryPanelProps {
  history: readonly JobHistoryEntry[];
  /**
   * Optional catalog map so the breakdown drawer can prepend a
   * service badge to each per-job row. Falls through to a plain
   * row when the entry isn't in the catalog (e.g. a since-removed
   * job that still lingers in history).
   */
  catalog?: ReadonlyMap<string, JobMeta>;
}

type PerJobRow = JobBreakdownRow;

/**
 * Compact name list for the table row — operators want to see WHICH
 * jobs ran without clicking into the drawer. Show up to 3 names; if
 * the batch contained more, append "+N more".
 *
 * Sort errors first, then skipped, then ok so the noisiest names
 * surface in the row preview. Names are stable per status group via
 * lexicographic sort so a refresh doesn't shuffle the preview.
 */
function summariseRunNames(entry: JobHistoryEntry): {
  visible: readonly string[];
  hiddenCount: number;
  worstStatus: "ok" | "skipped" | "error" | "unknown";
} {
  const map = entry.jobs ?? {};
  const list = Object.entries(map).map(([name, value]) => {
    const v = value as JobHistoryJobResult | undefined;
    return { name, status: typeof v?.status === "string" ? v.status : "unknown" };
  });
  const order: Record<string, number> = {
    error: 0,
    errors: 0,
    failed: 0,
    skipped: 1,
    ok: 2,
    unknown: 3,
  };
  list.sort(
    (a, b) =>
      (order[a.status] ?? 9) - (order[b.status] ?? 9) ||
      a.name.localeCompare(b.name),
  );
  const visible = list.slice(0, 3).map((r) => r.name);
  const hiddenCount = Math.max(0, list.length - visible.length);
  const worst = list.some((r) => order[r.status] === 0)
    ? "error"
    : list.some((r) => order[r.status] === 1)
      ? "skipped"
      : list.length > 0
        ? "ok"
        : "unknown";
  return { visible, hiddenCount, worstStatus: worst };
}

function rowsForBatch(
  entry: JobHistoryEntry | null,
  catalog?: ReadonlyMap<string, JobMeta>,
): PerJobRow[] {
  if (!entry) return [];
  const map = entry.jobs ?? {};
  const out: PerJobRow[] = [];
  for (const [name, value] of Object.entries(map)) {
    const v = value as JobHistoryJobResult;
    out.push({
      name,
      service: catalog?.get(name)?.service,
      status: typeof v?.status === "string" ? v.status : "—",
      elapsed: typeof v?.elapsed === "number" ? v.elapsed : undefined,
      error: typeof v?.error === "string" ? v.error : undefined,
    });
  }
  // Sort: errors first, then skipped, then ok — matches the operator
  // mental model ("what blew up?").
  const order: Record<string, number> = {
    error: 0,
    errors: 0,
    failed: 0,
    skipped: 1,
    ok: 2,
  };
  out.sort(
    (a, b) =>
      (order[a.status] ?? 9) - (order[b.status] ?? 9) ||
      a.name.localeCompare(b.name),
  );
  return out;
}

const historyColumns: ColumnDef<JobHistoryEntry>[] = [
  {
    id: "when",
    header: "When",
    accessorFn: (e) => e.ts ?? 0,
    sortingFn: "basic",
    enableColumnFilter: false,
    cell: ({ row }) => {
      const entry = row.original;
      const idx = row.index;
      return (
        <div
          className="flex flex-wrap items-center gap-1.5 tabular-nums text-fg-muted"
          title={formatAbsolute(entry.ts)}
        >
          <span>{formatRelative(epochToIso(entry.ts))}</span>
          {typeof entry.source === "string" && entry.source ? (
            <span
              className="inline-flex items-center rounded-md border border-info/40 bg-info/10 px-1.5 py-0 text-[10px] uppercase tracking-wide text-info"
              data-testid={`job-history-source-${idx}`}
              title={`Triggered by ${entry.source}`}
            >
              {entry.source}
            </span>
          ) : null}
        </div>
      );
    },
  },
  {
    id: "names",
    header: "Jobs run",
    accessorFn: (e) => Object.keys(e.jobs ?? {}).join(" "),
    enableSorting: false,
    cell: ({ row }) => {
      const entry = row.original;
      const idx = row.index;
      const summary = summariseRunNames(entry);
      const dotClass =
        summary.worstStatus === "error"
          ? "bg-danger"
          : summary.worstStatus === "skipped"
            ? "bg-warning"
            : summary.worstStatus === "ok"
              ? "bg-success"
              : "bg-fg-faint";
      return (
        <div
          className="flex flex-wrap items-center gap-1.5"
          data-testid={`job-history-row-${idx}-names`}
        >
          <span
            className={cn("inline-block size-1.5 shrink-0 rounded-full", dotClass)}
            aria-hidden
          />
          {summary.visible.length === 0 ? (
            <span className="text-fg-faint">—</span>
          ) : (
            summary.visible.map((n) => (
              <span
                key={n}
                className="truncate font-mono text-xs text-fg"
                title={n}
              >
                {n}
              </span>
            ))
          )}
          {summary.hiddenCount > 0 ? (
            <span className="text-xs text-fg-muted">
              +{summary.hiddenCount} more
            </span>
          ) : null}
        </div>
      );
    },
  },
  {
    id: "elapsed",
    header: "Elapsed",
    accessorFn: (e) => e.elapsed ?? 0,
    sortingFn: "basic",
    enableColumnFilter: false,
    cell: ({ row }) => (
      <span className="font-mono tabular-nums text-fg-muted">
        {formatElapsed(row.original.elapsed)}
      </span>
    ),
  },
  {
    id: "ok",
    header: "Ok",
    accessorFn: (e) => e.ok ?? 0,
    sortingFn: "basic",
    enableColumnFilter: false,
    cell: ({ row }) => (
      <Badge variant="success" className="tabular-nums">
        {row.original.ok ?? 0}
      </Badge>
    ),
  },
  {
    id: "skipped",
    header: "Skipped",
    accessorFn: (e) => e.skipped ?? 0,
    sortingFn: "basic",
    enableColumnFilter: false,
    cell: ({ row }) => (
      <Badge variant="warning" className="tabular-nums">
        {row.original.skipped ?? 0}
      </Badge>
    ),
  },
  {
    id: "errors",
    header: "Errors",
    accessorFn: (e) => e.errors ?? 0,
    sortingFn: "basic",
    enableColumnFilter: false,
    cell: ({ row }) => (
      <Badge
        variant={(row.original.errors ?? 0) > 0 ? "danger" : "outline"}
        className="tabular-nums"
      >
        {row.original.errors ?? 0}
      </Badge>
    ),
  },
];

/**
 * Default panel rendered when no job is selected. Shows a compact
 * table of recent batch runs from the controller's history feed,
 * with a Vaul drawer that pops a per-job breakdown for the row the
 * operator clicked.
 */
export function JobHistoryPanel({ history, catalog }: JobHistoryPanelProps) {
  const reduce = useReducedMotion();
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  const entries = useMemo(() => asArray<JobHistoryEntry>(history), [history]);
  const selected = openIndex !== null ? (entries[openIndex] ?? null) : null;
  const breakdown = useMemo(
    () => rowsForBatch(selected, catalog),
    [selected, catalog],
  );

  if (entries.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No batch history yet"
        description="The controller hasn't recorded any batch runs. Pick a job from the tree to trigger one."
      />
    );
  }

  return (
    <motion.section
      className="flex flex-col gap-3"
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      data-testid="job-history-panel"
    >
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Recent batches</CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable<JobHistoryEntry>
            data={entries}
            columns={historyColumns}
            testId="job-history"
            getRowId={(_entry, idx) => String(idx)}
            onRowClick={(row) => setOpenIndex(entries.indexOf(row))}
          />
        </CardContent>
      </Card>

      <VaulDrawer.Root
        direction="right"
        open={openIndex !== null}
        onOpenChange={(next) => {
          if (!next) setOpenIndex(null);
        }}
      >
        <VaulDrawer.Portal>
          <VaulDrawer.Overlay className="fixed inset-0 z-50 bg-[color-mix(in_oklab,var(--color-bg)_70%,transparent)] backdrop-blur-sm" />
          <VaulDrawer.Content
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-border bg-bg-1 outline-none"
            data-testid="job-history-drawer"
          >
            <header className="flex items-start justify-between gap-3 border-b border-border p-4">
              <div className="flex flex-col gap-1">
                <VaulDrawer.Title className="text-base font-semibold leading-none tracking-tight">
                  Batch detail
                </VaulDrawer.Title>
                <VaulDrawer.Description className="text-xs text-fg-muted">
                  {selected
                    ? formatRelative(epochToIso(selected.ts))
                    : ""}
                </VaulDrawer.Description>
              </div>
              <button
                type="button"
                onClick={() => setOpenIndex(null)}
                className="rounded-sm p-1 text-fg-muted [@media(hover:hover)]:hover:text-fg"
                aria-label="Close drawer"
                data-testid="job-history-drawer-close"
              >
                <X className="size-4" aria-hidden />
              </button>
            </header>
            <div className="flex-1 overflow-y-auto p-4">
              {breakdown.length === 0 ? (
                <p className="text-sm text-fg-faint">
                  No per-job results recorded for this batch.
                </p>
              ) : (
                <JobBreakdownTable rows={breakdown} />
              )}
            </div>
          </VaulDrawer.Content>
        </VaulDrawer.Portal>
      </VaulDrawer.Root>
    </motion.section>
  );
}
