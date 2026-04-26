import { useMemo } from "react";
import {
  BookOpen,
  Disc3,
  Film,
  Settings,
  Subtitles,
  Tv,
  type LucideIcon,
} from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import type { MediaIntegrityStatusShape } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/data-table";
import { EmptyState } from "@/components/layout/EmptyState";
import { formatBytes, formatRelative } from "./format";

interface AdapterTableProps {
  status?: MediaIntegrityStatusShape;
  loading?: boolean;
}

interface AdapterRow {
  id: string;
  name: string;
  icon: LucideIcon;
  resolved: number;
  freed: number;
  needsReview: number;
  failures: number;
  lastRun: string;
  ran: boolean;
}

const ICONS: Record<string, LucideIcon> = {
  radarr: Film,
  sonarr: Tv,
  lidarr: Disc3,
  readarr: BookOpen,
  bazarr: Subtitles,
};

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/** Read a per-adapter result row out of the opaque reconcile report. */
function readAdapterResult(
  detail: Record<string, unknown> | undefined,
  app: string,
): { found: boolean; row: Omit<AdapterRow, "id" | "name" | "icon" | "ran"> } {
  const empty = { resolved: 0, freed: 0, needsReview: 0, failures: 0, lastRun: "" };
  if (!detail) return { found: false, row: empty };

  // Servarr block: detail.servarr.results[app]
  const servarr = detail.servarr;
  if (
    servarr &&
    typeof servarr === "object" &&
    "results" in servarr &&
    typeof (servarr as Record<string, unknown>).results === "object"
  ) {
    const results = (servarr as { results: Record<string, unknown> }).results;
    const r = results[app];
    if (r && typeof r === "object") {
      const rec = r as Record<string, unknown>;
      return {
        found: true,
        row: {
          resolved: num(rec.total_resolved),
          freed: num(rec.bytes_freed),
          needsReview: num(rec.total_needs_review),
          failures: num(rec.total_failures),
          lastRun: typeof rec.ts === "string" ? rec.ts : "",
        },
      };
    }
  }

  // Bazarr sibling block: detail.bazarr
  if (app === "bazarr" && detail.bazarr && typeof detail.bazarr === "object") {
    const rec = detail.bazarr as Record<string, unknown>;
    return {
      found: true,
      row: {
        resolved: num(rec.total_resolved),
        freed: num(rec.bytes_freed),
        needsReview: num(rec.total_needs_review),
        failures: num(rec.total_failures),
        lastRun: typeof rec.ts === "string" ? rec.ts : "",
      },
    };
  }

  return { found: false, row: empty };
}

function buildRows(status: MediaIntegrityStatusShape | undefined): AdapterRow[] {
  if (!status) return [];
  const detail = status.last_reconcile?.detail as
    | Record<string, unknown>
    | undefined;
  const apps = [...status.servarr_adapters];
  if (status.bazarr_present) apps.push("bazarr");
  return apps.map((app) => {
    const { found, row } = readAdapterResult(detail, app);
    return {
      id: app,
      name: app.charAt(0).toUpperCase() + app.slice(1),
      icon: ICONS[app] ?? Settings,
      ran: found,
      ...row,
    };
  });
}

export function AdapterTable({ status, loading }: AdapterTableProps) {
  const rows = useMemo(() => buildRows(status), [status]);

  // Memoised columns. Numeric counts use `sortingFn: "basic"` so
  // the operator can sort by failures-desc to find the noisiest
  // adapter. The lastRun column carries an accessorFn returning the
  // raw ISO string — lex sort on ISO == chronological sort.
  const columns = useMemo<ColumnDef<AdapterRow>[]>(
    () => [
      {
        id: "adapter",
        accessorFn: (row) => row.name,
        header: "Adapter",
        meta: { label: "Adapter" },
        cell: ({ row }) => {
          const r = row.original;
          const Icon = r.icon;
          return (
            <div className="flex items-center gap-2 font-medium text-fg">
              <Icon className="size-4 text-fg-muted" aria-hidden />
              <span>{r.name}</span>
            </div>
          );
        },
      },
      {
        id: "resolved",
        accessorFn: (row) => (row.ran ? row.resolved : -1),
        header: "Resolved",
        meta: { label: "Resolved" },
        sortingFn: "basic",
        enableColumnFilter: false,
        cell: ({ row }) =>
          row.original.ran ? (
            <Badge variant="default">{row.original.resolved}</Badge>
          ) : (
            <span className="text-fg-faint">—</span>
          ),
      },
      {
        id: "freed",
        accessorFn: (row) => (row.ran ? row.freed : -1),
        header: "Freed",
        meta: { label: "Freed" },
        sortingFn: "basic",
        enableColumnFilter: false,
        cell: ({ row }) =>
          row.original.ran ? (
            <span className="font-mono tabular-nums">
              {formatBytes(row.original.freed)}
            </span>
          ) : (
            <span className="text-fg-faint">—</span>
          ),
      },
      {
        id: "needs-review",
        accessorFn: (row) => (row.ran ? row.needsReview : -1),
        header: "Needs review",
        meta: { label: "Needs review" },
        sortingFn: "basic",
        enableColumnFilter: false,
        cell: ({ row }) =>
          row.original.ran ? (
            <Badge variant={row.original.needsReview > 0 ? "warning" : "default"}>
              {row.original.needsReview}
            </Badge>
          ) : (
            <span className="text-fg-faint">—</span>
          ),
      },
      {
        id: "failures",
        accessorFn: (row) => (row.ran ? row.failures : -1),
        header: "Failures",
        meta: { label: "Failures" },
        sortingFn: "basic",
        enableColumnFilter: false,
        cell: ({ row }) =>
          row.original.ran ? (
            <Badge variant={row.original.failures > 0 ? "danger" : "default"}>
              {row.original.failures}
            </Badge>
          ) : (
            <span className="text-fg-faint">—</span>
          ),
      },
      {
        id: "last-run",
        accessorFn: (row) => row.lastRun,
        header: "Last run",
        meta: { label: "Last run" },
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="tabular-nums text-fg-muted">
            {row.original.ran ? formatRelative(row.original.lastRun) : "never"}
          </span>
        ),
      },
    ],
    [],
  );

  if (loading) {
    return (
      <div className="space-y-2" data-testid="adapter-table-loading">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Settings}
        title="No adapters configured"
        description="Configure your Radarr/Sonarr/Lidarr/Readarr/Bazarr in Routing to get started."
        action={
          <Button variant="secondary" asChild>
            <a href="/routing">Open routing</a>
          </Button>
        }
      />
    );
  }

  return (
    <Card className="p-3" data-testid="adapter-table">
      <DataTable<AdapterRow>
        testId="media-integrity-adapters"
        columns={columns}
        data={rows}
        getRowId={(row) => row.id}
        caption={`${rows.length} adapter${rows.length === 1 ? "" : "s"}`}
        emptyState="No adapters configured."
      />
    </Card>
  );
}
