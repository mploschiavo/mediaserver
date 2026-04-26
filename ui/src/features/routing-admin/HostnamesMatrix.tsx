import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Lock, ShieldCheck, Unlock, Pencil, Plus } from "lucide-react";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useRoutingV2, type RoutingV2HostEntry } from "./hooks";
import { HostEditDrawer } from "./HostEditDrawer";

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
  const [editIndex, setEditIndex] = useState<number | null>(null);

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
          const isAuthProvider = row.original.service_id === "authelia";
          if (gate === "required") {
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    data-tone="warning"
                    className="gap-1"
                    data-testid={`hostnames-auth-${row.index}-required`}
                  >
                    <Lock className="size-3" aria-hidden /> required
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  Traffic to this hostname must be authenticated by
                  Authelia (ext_authz). Click the row to edit.
                </TooltipContent>
              </Tooltip>
            );
          }
          if (gate === "optional") {
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="outline" data-tone="muted">
                    optional
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  Authelia checks happen but failures don't block —
                  the upstream sees the auth headers and decides.
                </TooltipContent>
              </Tooltip>
            );
          }
          return (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="outline"
                  className="gap-1 text-fg-muted"
                  data-testid={`hostnames-auth-${row.index}-none`}
                >
                  <Unlock className="size-3" aria-hidden /> none
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                {isAuthProvider
                  ? "The auth provider can't gate itself with itself — would lock you out."
                  : "Open: requests reach the upstream without an auth check. Use only for public services."}
              </TooltipContent>
            </Tooltip>
          );
        },
      },
      {
        id: "edit",
        header: "",
        enableSorting: false,
        cell: ({ row }) => (
          <button
            type="button"
            className="rounded p-1 text-fg-muted hover:bg-bg-2 hover:text-fg"
            onClick={(e) => {
              e.stopPropagation();
              setEditIndex(row.index);
            }}
            aria-label={`Edit ${row.original.canonical}`}
            data-testid={`hostnames-edit-${row.index}`}
          >
            <Pencil className="size-3.5" aria-hidden />
          </button>
        ),
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

  const handleAddNew = () => {
    if (!q.data) return;
    const newHosts = [
      ...q.data.config.hosts,
      {
        role: "",
        service_id: "",
        canonical: "",
        aliases: [],
      } as RoutingV2HostEntry,
    ];
    // Open the drawer for the new (empty) row at the appended index.
    // The mutation only fires on Save, so leaving the drawer cancels
    // the addition.
    setEditIndex(newHosts.length - 1);
    // Pre-write the host into the cached query data so the drawer
    // sees the empty row to edit. The next Save sends `hosts: newHosts`.
    q.data.config.hosts = newHosts;
  };

  return (
    <>
    <Card data-testid="hostnames-matrix">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle>Hostnames</CardTitle>
          <CardDescription>
            Every host the controller routes to a backend service.
            Canonical = primary URL, aliases redirect to canonical.
            Click the pencil to edit auth, TLS, path prefix, websocket,
            or maintenance mode.
          </CardDescription>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleAddNew}
          data-testid="hostnames-add"
        >
          <Plus className="size-3.5" /> Add host
        </Button>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <div
            className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
            data-testid="hostnames-matrix-empty"
          >
            No hostnames configured yet. Click <strong>Add host</strong>
            {" "}to wire one up.
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
    <HostEditDrawer
      open={editIndex !== null}
      hostIndex={editIndex}
      config={q.data?.config ?? null}
      onClose={() => setEditIndex(null)}
    />
    </>
  );
}
