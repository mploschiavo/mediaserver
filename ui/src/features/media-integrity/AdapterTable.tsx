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
import type { MediaIntegrityStatusShape } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
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

  const columns: ResponsiveTableColumn<AdapterRow>[] = [
    {
      id: "adapter",
      header: "Adapter",
      cell: (row) => (
        <div className="flex items-center gap-2 font-medium text-fg">
          <row.icon className="size-4 text-fg-muted" aria-hidden />
          <span>{row.name}</span>
        </div>
      ),
    },
    {
      id: "resolved",
      header: "Resolved",
      cell: (row) =>
        row.ran ? (
          <Badge variant="default">{row.resolved}</Badge>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
    {
      id: "freed",
      header: "Freed",
      cell: (row) =>
        row.ran ? (
          <span className="font-mono tabular-nums">{formatBytes(row.freed)}</span>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
    {
      id: "needs-review",
      header: "Needs review",
      cell: (row) =>
        row.ran ? (
          <Badge variant={row.needsReview > 0 ? "warning" : "default"}>
            {row.needsReview}
          </Badge>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
    {
      id: "failures",
      header: "Failures",
      cell: (row) =>
        row.ran ? (
          <Badge variant={row.failures > 0 ? "danger" : "default"}>
            {row.failures}
          </Badge>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
    {
      id: "last-run",
      header: "Last run",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">
          {row.ran ? formatRelative(row.lastRun) : "never"}
        </span>
      ),
    },
  ];

  return (
    <Card className="p-0" data-testid="adapter-table">
      <ResponsiveTable
        rows={rows}
        rowKey={(r) => r.id}
        columns={columns}
        card={(row) => (
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-medium text-fg">
                <row.icon className="size-4 text-fg-muted" aria-hidden />
                <span>{row.name}</span>
              </div>
              <span className="text-xs tabular-nums text-fg-muted">
                {row.ran ? formatRelative(row.lastRun) : "never"}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <span className="text-fg-muted">Resolved</span>
              <span className="text-right font-mono tabular-nums">
                {row.ran ? row.resolved : "—"}
              </span>
              <span className="text-fg-muted">Freed</span>
              <span className="text-right font-mono tabular-nums">
                {row.ran ? formatBytes(row.freed) : "—"}
              </span>
              <span className="text-fg-muted">Needs review</span>
              <span className="text-right font-mono tabular-nums">
                {row.ran ? row.needsReview : "—"}
              </span>
              <span className="text-fg-muted">Failures</span>
              <span className="text-right font-mono tabular-nums">
                {row.ran ? row.failures : "—"}
              </span>
            </div>
          </div>
        )}
      />
    </Card>
  );
}
