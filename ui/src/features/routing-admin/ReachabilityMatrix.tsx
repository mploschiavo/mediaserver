import { useMemo } from "react";
import { RefreshCw, RouteOff } from "lucide-react";
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
import { DataTable } from "@/components/data-table";
import { EmptyState } from "@/components/layout/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { useRoutingProbe, type RoutingProbeRow } from "./hooks";

interface MatrixRow {
  id: string;
  app: string;
  internalUrl: string;
  externalUrl: string;
  ok: boolean;
  status: number | undefined;
  latency: number | undefined;
  probedAt: string;
  error: string;
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function buildRow(raw: RoutingProbeRow, idx: number): MatrixRow {
  const ok = raw.ok === true;
  const status = num(raw.status_code) ?? num(raw.status);
  const latency = num(raw.latency_ms);
  const app = str(raw.app);
  return {
    id: app || `row-${idx}`,
    app: app || "(unknown)",
    internalUrl: str(raw.internal_url),
    externalUrl: str(raw.external_url),
    ok,
    status,
    latency,
    probedAt: str(raw.probed_at),
    error: str(raw.error),
  };
}

function rowsFromResult(result: unknown): MatrixRow[] {
  if (!result || typeof result !== "object") return [];
  const obj = result as Record<string, unknown>;
  const candidates = [obj.rows, obj.results, obj.matrix];
  for (const c of candidates) {
    if (Array.isArray(c)) {
      return c.map((r, i) => buildRow((r ?? {}) as RoutingProbeRow, i));
    }
  }
  // Some controller shapes nest rows under a per-app object map.
  // Walk one level deep so the operator still sees something useful.
  const out: MatrixRow[] = [];
  for (const [key, value] of Object.entries(obj)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const row = value as RoutingProbeRow;
      if (row.app === undefined) row.app = key;
      out.push(buildRow(row, out.length));
    }
  }
  return out;
}

function formatRelative(iso: string): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const delta = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function StatusBadge({ row }: { row: MatrixRow }) {
  if (row.ok) {
    return (
      <Badge variant="success">
        ok{row.status !== undefined ? ` (${row.status})` : ""}
      </Badge>
    );
  }
  return (
    <Badge variant="danger" title={row.error || undefined}>
      fail{row.status !== undefined ? ` (${row.status})` : ""}
    </Badge>
  );
}

/**
 * Reachability matrix — shows per-app probe results from the
 * controller's server-side probe (`GET /api/routing-probe`). The
 * "Re-probe" button refetches the query, which causes React Query
 * to re-issue the GET so the controller refreshes the matrix.
 */
export function ReachabilityMatrix() {
  const probe = useRoutingProbe();
  const rows = useMemo(() => rowsFromResult(probe.data), [probe.data]);

  // Memoised columns. Status is a derived enum (ok/fail) sortable
  // via accessorFn; latency is numeric (`sortingFn: "basic"`); date
  // columns drop the per-column filter input — operators filter by
  // app name + URL, not relative timestamps.
  const columns = useMemo<ColumnDef<MatrixRow>[]>(
    () => [
      {
        id: "app",
        accessorFn: (row) => row.app,
        header: "App",
        meta: { label: "App" },
        cell: ({ row }) => (
          <span className="font-medium text-fg">{row.original.app}</span>
        ),
      },
      {
        id: "internal",
        accessorFn: (row) => row.internalUrl,
        header: "Internal URL",
        meta: { label: "Internal URL" },
        cell: ({ row }) =>
          row.original.internalUrl ? (
            <span className="font-mono text-xs text-fg-muted">
              {row.original.internalUrl}
            </span>
          ) : (
            <span className="text-fg-faint">—</span>
          ),
      },
      {
        id: "external",
        accessorFn: (row) => row.externalUrl,
        header: "External URL",
        meta: { label: "External URL" },
        cell: ({ row }) =>
          row.original.externalUrl ? (
            <a
              href={row.original.externalUrl}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-xs text-accent hover:underline"
            >
              {row.original.externalUrl}
            </a>
          ) : (
            <span className="text-fg-faint">—</span>
          ),
      },
      {
        id: "status",
        // Status as a derived enum string ("ok"/"fail") — short
        // strings let the per-column filter act like an enum
        // selector ("o" → ok rows, "f" → fail rows).
        accessorFn: (row) => (row.ok ? "ok" : "fail"),
        header: "Status",
        meta: { label: "Status" },
        cell: ({ row }) => <StatusBadge row={row.original} />,
      },
      {
        id: "latency",
        accessorFn: (row) =>
          row.latency !== undefined ? row.latency : Number.POSITIVE_INFINITY,
        header: "Latency",
        meta: { label: "Latency" },
        sortingFn: "basic",
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="font-mono tabular-nums text-fg-muted">
            {row.original.latency !== undefined
              ? `${Math.round(row.original.latency)} ms`
              : "—"}
          </span>
        ),
      },
      {
        id: "probed",
        accessorFn: (row) => row.probedAt,
        header: "Last probed",
        meta: { label: "Last probed" },
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="tabular-nums text-fg-muted">
            {formatRelative(row.original.probedAt)}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <Card data-testid="reachability-matrix">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle>Reachability</CardTitle>
          <CardDescription>
            Server-side probe across each app's internal + external URL.
          </CardDescription>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void probe.refetch()}
          disabled={probe.isFetching}
          data-testid="reachability-refresh"
        >
          <RefreshCw
            className={probe.isFetching ? "size-4 animate-spin" : "size-4"}
            aria-hidden
          />
          Re-probe
        </Button>
      </CardHeader>
      <CardContent>
        {probe.error ? (
          <div
            role="alert"
            data-testid="reachability-error"
            className="rounded-md border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-3 text-sm text-danger"
          >
            <p className="font-medium">Probe failed</p>
            <p className="mt-1 text-fg-muted">{probe.error.message}</p>
          </div>
        ) : probe.isLoading ? (
          <div className="space-y-2" data-testid="reachability-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={RouteOff}
            title="No probe data"
            description="The controller has not produced a routing-probe matrix yet."
          />
        ) : (
          <DataTable<MatrixRow>
            testId="reachability-rows"
            columns={columns}
            data={rows}
            getRowId={(row) => row.id}
            caption={`${rows.length} app${rows.length === 1 ? "" : "s"}`}
            emptyState="No probe data."
          />
        )}
      </CardContent>
    </Card>
  );
}
