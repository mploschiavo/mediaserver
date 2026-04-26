import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Lock, ShieldCheck, Unlock } from "lucide-react";
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
import { useRoutingV2, type RoutingV2HostEntry } from "./hooks";

/**
 * Read-only matrix of every host the controller has registered:
 * canonical hostname, aliases, the upstream service it forwards to,
 * TLS cert binding, and auth gate. PR-5 adds inline edit + the
 * advanced drawer; PR-4 ships the read-only baseline so operators
 * can see the v2 shape immediately and we can ship a working
 * end-to-end story without the full editor.
 */
export function HostnamesMatrix() {
  const q = useRoutingV2();

  const rows = useMemo<readonly RoutingV2HostEntry[]>(
    () => (q.data?.config.hosts ?? []) as readonly RoutingV2HostEntry[],
    [q.data],
  );

  const columns = useMemo<ColumnDef<RoutingV2HostEntry>[]>(
    () => [
      {
        id: "canonical",
        accessorKey: "canonical",
        header: "Canonical",
        cell: ({ row }) => (
          <code
            className="font-mono text-xs text-fg"
            data-testid={`hostnames-canonical-${row.index}`}
          >
            {row.original.canonical || "—"}
          </code>
        ),
      },
      {
        id: "aliases",
        accessorKey: "aliases",
        header: "Aliases",
        enableSorting: false,
        cell: ({ row }) => {
          const aliases = row.original.aliases ?? [];
          if (aliases.length === 0) {
            return <span className="text-fg-faint">—</span>;
          }
          return (
            <div className="flex flex-wrap gap-1">
              {aliases.map((a) => (
                <code
                  key={a}
                  className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg-muted"
                >
                  {a}
                </code>
              ))}
            </div>
          );
        },
      },
      {
        id: "service_id",
        accessorKey: "service_id",
        header: "Service",
        cell: ({ row }) => (
          <span className="text-sm text-fg" data-testid={`hostnames-service-${row.index}`}>
            {row.original.service_id || "—"}
          </span>
        ),
      },
      {
        id: "path_prefix",
        accessorKey: "path_prefix",
        header: "Path",
        cell: ({ row }) => {
          const p = row.original.path_prefix;
          return p ? (
            <code className="text-xs text-fg-muted">{p}</code>
          ) : (
            <span className="text-fg-faint">/</span>
          );
        },
      },
      {
        id: "tls",
        header: "TLS",
        accessorFn: (h) => h.tls?.cert_id ?? "",
        cell: ({ row }) => {
          const tls = row.original.tls;
          if (!tls?.cert_id) {
            return (
              <span
                className="text-fg-faint"
                title="No cert binding"
                data-testid={`hostnames-tls-${row.index}-none`}
              >
                —
              </span>
            );
          }
          return (
            <Badge
              variant="outline"
              className="gap-1"
              data-testid={`hostnames-tls-${row.index}`}
            >
              <ShieldCheck className="size-3" aria-hidden />
              {tls.cert_id}
            </Badge>
          );
        },
      },
      {
        id: "auth",
        header: "Auth",
        accessorFn: (h) => h.auth?.gate ?? "",
        cell: ({ row }) => {
          const gate = row.original.auth?.gate ?? "none";
          if (gate === "required") {
            return (
              <Badge
                variant="outline"
                data-tone="warning"
                className="gap-1"
                data-testid={`hostnames-auth-${row.index}-required`}
              >
                <Lock className="size-3" aria-hidden /> required
              </Badge>
            );
          }
          if (gate === "optional") {
            return (
              <Badge variant="outline" data-tone="muted">
                optional
              </Badge>
            );
          }
          return (
            <Badge
              variant="outline"
              className="gap-1 text-fg-muted"
              data-testid={`hostnames-auth-${row.index}-none`}
            >
              <Unlock className="size-3" aria-hidden /> none
            </Badge>
          );
        },
      },
    ],
    [],
  );

  if (q.isLoading) {
    return (
      <Card data-testid="hostnames-matrix-loading">
        <CardHeader>
          <CardTitle>Hostnames</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error || !q.data) {
    return (
      <Card data-testid="hostnames-matrix-error" role="alert">
        <CardHeader>
          <CardTitle>Hostnames</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't load v2 routing config:{" "}
            {q.error ? (q.error as Error).message : "no data"}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="hostnames-matrix">
      <CardHeader>
        <CardTitle>Hostnames</CardTitle>
        <CardDescription>
          Every host the controller routes to a backend service.
          Canonical = primary URL, aliases redirect to canonical.
          Edit comes in v1.0.244 (PR-5).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <div
            className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
            data-testid="hostnames-matrix-empty"
          >
            No hostnames configured yet. Add them in <code>routing.yaml</code> or
            via the editor in PR-5.
          </div>
        ) : (
          <DataTable<RoutingV2HostEntry>
            data={[...rows]}
            columns={columns}
            testId="hostnames"
            getRowId={(row, idx) => `${row.canonical || ""}-${idx}`}
          />
        )}
      </CardContent>
    </Card>
  );
}
