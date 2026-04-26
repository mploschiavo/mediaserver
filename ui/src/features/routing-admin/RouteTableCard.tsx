import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowRight, Globe, Lock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DataTable } from "@/components/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useRouteTable, type RouteRow } from "./hooks";

/**
 * The "what URL goes where" answer the operator was missing. Renders
 * every emitted route — auto-derived path-prefix routes (the
 * /app/jellyfin → service_jellyfin family), explicit hostname
 * subdomains (jf.iomio.io → service_jellyfin), hostname aliases that
 * 301 to the canonical, path-alias redirects, apex, and catch-all.
 *
 * Sorted by (host, match) so the gateway-host rows cluster together
 * and within a host the longest-prefix matches sort up. Each row
 * carries a `source` field that explains *why* the route exists
 * ("strategy=hybrid + app_path_prefix=/app per-service" vs
 * "hosts[] entry, role=media_server" vs "path_aliases[] (301)").
 */
export function RouteTableCard() {
  const q = useRouteTable();

  const rows = useMemo(() => {
    const data = q.data?.rows ?? [];
    // Stable sort: host first, then match length (longer = more
    // specific) descending so /app/jellyfin/ sorts before /app/.
    return [...data].sort((a, b) => {
      if (a.host !== b.host) return a.host.localeCompare(b.host);
      return b.match.length - a.match.length;
    });
  }, [q.data]);

  const columns = useMemo<ColumnDef<RouteRow>[]>(() => [
    {
      id: "host",
      accessorKey: "host",
      header: "Host",
      cell: ({ row }) => (
        <code className="font-mono text-xs text-fg">
          {row.original.host || "—"}
        </code>
      ),
    },
    {
      id: "match",
      accessorKey: "match",
      header: "URL match",
      cell: ({ row }) => (
        <code className="rounded bg-bg-2 px-1.5 py-0.5 font-mono text-xs text-fg">
          {row.original.match}
        </code>
      ),
    },
    {
      id: "target",
      header: "Target",
      accessorKey: "target",
      cell: ({ row }) => {
        const r = row.original;
        const tone = targetTone(r.target_kind);
        return (
          <span className="flex items-center gap-1.5 text-xs">
            <ArrowRight className="size-3 text-fg-faint" aria-hidden />
            <Badge variant="outline" data-tone={tone}>
              {r.target_kind}
            </Badge>
            <code className="font-mono text-fg">{r.target}</code>
          </span>
        );
      },
    },
    {
      id: "kind",
      header: "Kind",
      accessorKey: "kind",
      cell: ({ row }) => (
        <Badge variant="outline" data-testid={`route-kind-${row.index}`}>
          {kindLabel(row.original.kind)}
        </Badge>
      ),
    },
    {
      id: "source",
      header: "Why",
      accessorKey: "source",
      enableSorting: false,
      cell: ({ row }) => (
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="block max-w-[220px] truncate text-xs text-fg-muted"
              title={row.original.source}
            >
              {row.original.source}
            </span>
          </TooltipTrigger>
          <TooltipContent>{row.original.source}</TooltipContent>
        </Tooltip>
      ),
    },
  ], []);

  if (q.isLoading) {
    return (
      <Card data-testid="route-table-card-loading">
        <CardHeader>
          <CardTitle>Route table</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error || !q.data) {
    return (
      <Card data-testid="route-table-card-error" role="alert">
        <CardHeader>
          <CardTitle>Route table</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't load the route table:{" "}
            {q.error ? (q.error as Error).message : "no data"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const summary = q.data.summary;

  return (
    <Card data-testid="route-table-card">
      <CardHeader>
        <CardTitle>Route table</CardTitle>
        <CardDescription>
          Every URL the gateway answers. Auto-generated path routes
          (e.g. <code className="text-fg">/app/jellyfin</code>) come
          from your strategy + service registry; explicit subdomains
          + path aliases come from the hostnames editor above.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div
          className="flex flex-wrap gap-3 rounded-md border border-border bg-bg-1/40 p-3 text-xs text-fg-muted"
          data-testid="route-table-summary"
        >
          <SummaryItem
            icon={<Globe className="size-3.5" />}
            label="Strategy"
            value={summary.strategy}
          />
          <SummaryItem
            icon={<Lock className="size-3.5" />}
            label="Gateway"
            value={summary.gateway_host || "—"}
          />
          <SummaryItem
            label="Path prefix"
            value={
              <code className="rounded bg-bg-2 px-1 py-0.5 text-fg">
                {summary.app_path_prefix}
              </code>
            }
          />
          <SummaryItem
            label="Active services"
            value={String(summary.active_service_count)}
          />
        </div>

        {rows.length === 0 ? (
          <div
            className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
            data-testid="route-table-empty"
          >
            No routes emitted. Check your strategy + hostnames.
          </div>
        ) : (
          <DataTable<RouteRow>
            data={rows}
            columns={columns}
            testId="route-table"
            getRowId={(row, idx) => `${row.host}-${row.match}-${idx}`}
          />
        )}
      </CardContent>
    </Card>
  );
}

function targetTone(kind: RouteRow["target_kind"]): string {
  switch (kind) {
    case "service":
      return "info";
    case "redirect":
      return "warning";
    case "static":
      return "muted";
    case "404":
      return "muted";
    case "block":
      return "danger";
    default:
      return "muted";
  }
}

function kindLabel(kind: RouteRow["kind"]): string {
  switch (kind) {
    case "auto_path":
      return "auto · path";
    case "subdomain":
      return "subdomain";
    case "explicit_path":
      return "path · explicit";
    case "host_alias":
      return "host alias";
    case "path_alias":
      return "path alias";
    case "apex":
      return "apex";
    case "catch_all":
      return "catch-all";
  }
}

function SummaryItem({
  icon,
  label,
  value,
}: {
  icon?: React.ReactNode;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5">
      {icon}
      <span className="text-fg-faint">{label}:</span>
      <span className="text-fg">{value}</span>
    </div>
  );
}
