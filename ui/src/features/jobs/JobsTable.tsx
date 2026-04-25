import { useMemo, useState } from "react";
import { Workflow } from "lucide-react";
import { asArray } from "@/lib/coerce";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { cn } from "@/lib/cn";
import {
  useJobs,
  type JobHistoryEntry,
  type JobMeta,
} from "./hooks";

/** Compact ISO/epoch timestamp -> "12m ago" / "just now". */
function relative(
  value: string | number | undefined,
  now: number = Date.now(),
): string {
  if (value === undefined || value === null || value === "") return "—";
  const t =
    typeof value === "number" ? value * 1000 : Date.parse(String(value));
  if (!Number.isFinite(t)) return "—";
  const delta = Math.max(0, Math.floor((now - t) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

/** Render a "1.4s" / "12s" / "3m 02s" duration. */
function fmtDurationSec(sec: number | undefined): string {
  if (typeof sec !== "number" || !Number.isFinite(sec)) return "—";
  if (sec < 1) return `${(sec * 1000).toFixed(0)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

interface JobsTableProps {
  /** Override the hook (used by tests). */
  jobs?: readonly JobMeta[];
  /** Override the history (used by tests). */
  history?: readonly JobHistoryEntry[];
  /** Override the loading flag (used by tests). */
  loading?: boolean;
  /** Override the error (used by tests). */
  error?: Error | null;
  className?: string;
}

/**
 * Two-tab Jobs surface:
 *
 *   1. **Catalog** — every registered job descriptor (`name`, `phase`,
 *      `service`, `priority`, `requires[]`). This is the controller's
 *      static inventory.
 *   2. **History** — recent batch runs (`ts`, `elapsed`, `ok`, `errors`,
 *      per-job result map). Newest-first.
 *
 * Both tabs read from the same `/api/jobs` payload — no extra fetch.
 * Tests can inject either array via props.
 */
export function JobsTable({
  jobs: jobsProp,
  history: historyProp,
  loading: loadingProp,
  error: errorProp,
  className,
}: JobsTableProps = {}) {
  const live = useJobs();
  const jobs = jobsProp ?? asArray<JobMeta>(live.data?.jobs);
  const history = historyProp ?? asArray<JobHistoryEntry>(live.data?.history);
  const loading = loadingProp ?? live.isLoading;
  const error = errorProp ?? (live.error as Error | null);

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
        data-testid="jobs-table-error"
      >
        <p className="font-medium">Failed to load jobs</p>
        <p className="mt-1 text-fg-muted">{error.message}</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div
        className={cn("space-y-2", className)}
        data-testid="jobs-table-loading"
      >
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  return (
    <Tabs defaultValue="catalog" className={cn("flex flex-col gap-3", className)}>
      <TabsList className="self-start">
        <TabsTrigger value="catalog" data-testid="jobs-tab-catalog">
          Catalog ({jobs.length})
        </TabsTrigger>
        <TabsTrigger value="history" data-testid="jobs-tab-history">
          History ({history.length})
        </TabsTrigger>
      </TabsList>
      <TabsContent value="catalog">
        <CatalogTable jobs={jobs} />
      </TabsContent>
      <TabsContent value="history">
        <HistoryList history={history} />
      </TabsContent>
    </Tabs>
  );
}

function CatalogTable({ jobs }: { jobs: readonly JobMeta[] }) {
  const sorted = useMemo<readonly JobMeta[]>(
    () =>
      [...jobs].sort((a, b) => {
        const pa = String((a as { phase?: string }).phase ?? "");
        const pb = String((b as { phase?: string }).phase ?? "");
        if (pa !== pb) return pa.localeCompare(pb);
        return a.name.localeCompare(b.name);
      }),
    [jobs],
  );

  if (sorted.length === 0) {
    return (
      <EmptyState
        icon={Workflow}
        title="No jobs registered"
        description="The controller has nothing in its catalog."
      />
    );
  }

  const columns: ResponsiveTableColumn<JobMeta>[] = [
    {
      id: "job",
      header: "Job",
      cell: (row) => (
        <div className="flex flex-col gap-0.5">
          <span className="font-medium text-fg">{row.label || row.name}</span>
          <span className="font-mono text-xs text-fg-faint">{row.name}</span>
        </div>
      ),
    },
    {
      id: "phase",
      header: "Phase",
      cell: (row) => {
        const phase = (row as { phase?: string }).phase ?? "";
        return phase ? (
          <Badge variant="outline">{phase}</Badge>
        ) : (
          <span className="text-fg-faint">—</span>
        );
      },
    },
    {
      id: "service",
      header: "Service",
      cell: (row) => (
        <span className="text-fg-muted">{row.service || "—"}</span>
      ),
    },
    {
      id: "requires",
      header: "Depends on",
      cell: (row) => {
        const requires = asArray<string>(row.requires);
        if (requires.length === 0) {
          return <span className="text-fg-faint">—</span>;
        }
        return (
          <div className="flex flex-wrap gap-1">
            {requires.map((r) => (
              <Badge key={r} variant="info">
                {r}
              </Badge>
            ))}
          </div>
        );
      },
    },
    {
      id: "non-blocking",
      header: "Non-blocking",
      cell: (row) =>
        row.non_blocking ? (
          <Badge variant="warning">non-blocking</Badge>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
  ];

  return (
    <Card className="p-0" data-testid="jobs-table">
      <ResponsiveTable
        rows={[...sorted]}
        rowKey={(r) => r.name}
        columns={columns}
        card={(row) => (
          <div
            className="flex flex-col gap-2"
            data-testid={`job-card-${row.name}`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex flex-col">
                <span className="font-medium text-fg">
                  {row.label || row.name}
                </span>
                <span className="font-mono text-xs text-fg-faint">
                  {row.name}
                </span>
              </div>
              {(row as { phase?: string }).phase ? (
                <Badge variant="outline">
                  {(row as { phase?: string }).phase}
                </Badge>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-1 text-xs">
              {row.service ? <Badge variant="info">{row.service}</Badge> : null}
              {asArray<string>(row.requires).map((r) => (
                <Badge key={r} variant="outline">
                  ⟵ {r}
                </Badge>
              ))}
              {row.non_blocking ? (
                <Badge variant="warning">non-blocking</Badge>
              ) : null}
            </div>
          </div>
        )}
      />
    </Card>
  );
}

function HistoryList({ history }: { history: readonly JobHistoryEntry[] }) {
  const [openTs, setOpenTs] = useState<number | null>(null);
  if (history.length === 0) {
    return (
      <EmptyState
        icon={Workflow}
        title="No run history yet"
        description="Recent batch runs will appear here once jobs execute."
      />
    );
  }
  return (
    <Card className="p-0" data-testid="jobs-history">
      <ul role="list" className="divide-y divide-border">
        {history.map((entry, idx) => {
          const ts = typeof entry.ts === "number" ? entry.ts : idx;
          const isOpen = openTs === ts;
          const errors = typeof entry.errors === "number" ? entry.errors : 0;
          const ok = typeof entry.ok === "number" ? entry.ok : 0;
          const skipped =
            typeof entry.skipped === "number" ? entry.skipped : 0;
          return (
            <li key={`${ts}-${idx}`} data-testid={`jobs-history-row-${idx}`}>
              <button
                type="button"
                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-bg-2/40"
                onClick={() => setOpenTs(isOpen ? null : ts)}
                aria-expanded={isOpen}
              >
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm text-fg">
                    {relative(entry.ts)}
                  </span>
                  <span className="font-mono text-xs text-fg-faint">
                    {fmtDurationSec(entry.elapsed)}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {ok > 0 ? (
                    <Badge variant="success">{ok} ok</Badge>
                  ) : null}
                  {skipped > 0 ? (
                    <Badge variant="outline">{skipped} skipped</Badge>
                  ) : null}
                  {errors > 0 ? (
                    <Badge variant="danger">{errors} error</Badge>
                  ) : null}
                </div>
              </button>
              {isOpen && entry.jobs ? (
                <ul
                  className="border-t border-border bg-bg-1 px-4 py-2"
                  data-testid={`jobs-history-detail-${idx}`}
                >
                  {Object.entries(entry.jobs).map(([name, result]) => (
                    <li
                      key={name}
                      className="flex items-center justify-between gap-3 py-1 text-xs"
                    >
                      <span className="font-mono text-fg">{name}</span>
                      <span className="flex items-center gap-2 text-fg-muted">
                        <Badge
                          variant={
                            result?.status === "ok"
                              ? "success"
                              : result?.status === "skipped"
                                ? "outline"
                                : "danger"
                          }
                        >
                          {result?.status ?? "—"}
                        </Badge>
                        {fmtDurationSec(result?.elapsed)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
