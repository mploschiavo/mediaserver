import { useMemo } from "react";
import { GitCompareArrows } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DataTable } from "@/components/data-table";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { asArray } from "@/lib/coerce";
import { useConfigDrift, type DriftEntry } from "./hooks";

function severityVariant(
  s: string | undefined,
): "default" | "warning" | "danger" | "info" {
  switch ((s ?? "").toLowerCase()) {
    case "error":
    case "critical":
      return "danger";
    case "warn":
    case "warning":
      return "warning";
    case "info":
      return "info";
    default:
      return "default";
  }
}

function entryKey(d: DriftEntry, idx: number): string {
  return String(d.key ?? d.path ?? idx);
}

function fmt(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

interface DriftRow {
  key: string;
  entry: DriftEntry;
}

/**
 * Drift card — surfaces every key whose live value diverged from
 * the profile snapshot. The "Reconcile" button kicks over to the
 * `/ops` route which owns the actual reconcile mutation; the
 * settings surface only describes the gap.
 */
export function DriftCard() {
  const drift = useConfigDrift();
  const entries = asArray<DriftEntry>(
    drift.data?.drift ?? drift.data?.entries,
  );

  const rows = useMemo<DriftRow[]>(
    () => entries.map((d, i) => ({ key: entryKey(d, i), entry: d })),
    [entries],
  );

  const columns = useMemo<ColumnDef<DriftRow>[]>(
    () => [
      {
        id: "key",
        accessorFn: (r) => r.key,
        header: "Key",
        meta: { label: "Key" },
        cell: ({ row }) => (
          <span className="font-mono text-xs text-fg">{row.original.key}</span>
        ),
      },
      {
        id: "profile",
        accessorFn: (r) => fmt(r.entry.profile_value),
        header: "Profile",
        meta: { label: "Profile" },
        cell: ({ row }) => (
          <span className="font-mono text-xs text-fg-muted">
            {fmt(row.original.entry.profile_value)}
          </span>
        ),
      },
      {
        id: "live",
        accessorFn: (r) => fmt(r.entry.live_value),
        header: "Live",
        meta: { label: "Live" },
        cell: ({ row }) => (
          <span className="font-mono text-xs text-fg-muted">
            {fmt(row.original.entry.live_value)}
          </span>
        ),
      },
      {
        id: "severity",
        accessorFn: (r) => r.entry.severity ?? "info",
        header: "Severity",
        meta: { label: "Severity" },
        cell: ({ row }) => (
          <div className="flex items-center justify-end">
            <Badge variant={severityVariant(row.original.entry.severity)}>
              {row.original.entry.severity ?? "info"}
            </Badge>
          </div>
        ),
      },
    ],
    [],
  );

  return (
    <Card data-testid="drift-card">
      <CardHeader className="flex-row items-start justify-between gap-3 sm:flex-row sm:items-center">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <GitCompareArrows aria-hidden className="size-4 text-fg-muted" />
            Configuration drift
          </CardTitle>
          <CardDescription>
            Keys where the live state has diverged from the profile.
          </CardDescription>
        </div>
        <Button asChild variant="secondary" data-testid="drift-reconcile-link">
          <a href="/ops">Reconcile</a>
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        {drift.isLoading ? (
          <div className="space-y-2 p-6" data-testid="drift-card-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : drift.error ? (
          <div className="px-6 py-4">
            <ApiErrorTile
              error={drift.error}
              onRetry={() => void drift.refetch()}
            />
          </div>
        ) : rows.length === 0 ? (
          <p
            className="px-6 py-4 text-sm text-fg-muted"
            data-testid="drift-card-empty"
          >
            No drift — profile and live state match.
          </p>
        ) : (
          <div className="px-6 pb-6">
            <DataTable<DriftRow>
              testId="drift"
              columns={columns}
              data={rows}
              getRowId={(r) => r.key}
              caption={`${rows.length} drifted key${rows.length === 1 ? "" : "s"}`}
              emptyState="No drift — profile and live state match."
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
