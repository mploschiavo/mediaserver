import { asArray } from "@/lib/coerce";
import { useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, ShieldCheck, Users } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DataTable } from "@/components/data-table";
import { EmptyState } from "@/components/layout/EmptyState";
import { useConcurrentSpikes, type ConcurrentSpikeAlert } from "./hooks";

interface SpikeRow {
  id: string;
  user: string;
  count: number;
  threshold: number;
  providers: readonly string[];
}

function severity(
  count: number,
  threshold: number,
): "info" | "warning" | "danger" {
  if (count >= threshold * 2) return "danger";
  if (count >= threshold) return "warning";
  return "info";
}

function providerVariant(
  provider: string,
): "info" | "success" | "warning" | "default" | "outline" {
  switch (provider.toLowerCase()) {
    case "authelia":
      return "info";
    case "jellyfin":
      return "success";
    case "jellyseerr":
      return "warning";
    case "native":
      return "default";
    default:
      return "outline";
  }
}

function spikeId(s: ConcurrentSpikeAlert, idx: number): string {
  return s.username ?? `spike-${idx}`;
}

function toRow(s: ConcurrentSpikeAlert, idx: number): SpikeRow {
  return {
    id: spikeId(s, idx),
    user: s.username ?? "(anonymous)",
    count: typeof s.count === "number" ? s.count : 0,
    threshold: typeof s.threshold === "number" ? s.threshold : 5,
    providers: Array.isArray(s.providers) ? s.providers : [],
  };
}

export function ConcurrentSpikesCard() {
  const reduce = useReducedMotion();
  const query = useConcurrentSpikes();

  const rows = useMemo<SpikeRow[]>(() => {
    const list = asArray(query.data?.alerts);
    return list.map((a, i) => toRow(a, i));
  }, [query.data]);

  const columns = useMemo<ColumnDef<SpikeRow>[]>(
    () => [
      {
        id: "user",
        accessorFn: (r) => r.user,
        header: "User",
        meta: { label: "User" },
        cell: ({ row }) => (
          <span className="font-medium text-fg">{row.original.user}</span>
        ),
      },
      {
        id: "count",
        accessorFn: (r) => r.count,
        header: "Count",
        meta: { label: "Count" },
        sortingFn: "basic",
        cell: ({ row }) => (
          <Badge
            variant={severity(row.original.count, row.original.threshold)}
          >
            {row.original.count}
          </Badge>
        ),
      },
      {
        id: "threshold",
        accessorFn: (r) => r.threshold,
        header: "Threshold",
        meta: { label: "Threshold" },
        sortingFn: "basic",
        cell: ({ row }) => (
          <span className="tabular-nums text-fg-muted">
            {row.original.threshold}
          </span>
        ),
      },
      {
        id: "providers",
        accessorFn: (r) => r.providers.join(" "),
        header: "Providers",
        meta: { label: "Providers" },
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-1">
            {row.original.providers.length === 0 ? (
              <span className="text-xs text-fg-faint">—</span>
            ) : (
              row.original.providers.map((p) => (
                <Badge key={p} variant={providerVariant(p)}>
                  {p}
                </Badge>
              ))
            )}
          </div>
        ),
      },
      {
        id: "actions",
        header: "Actions",
        meta: { label: "Actions" },
        enableSorting: false,
        enableColumnFilter: false,
        cell: ({ row }) => (
          <div className="flex items-center justify-end">
            {/* Plain anchor — the `/sessions` route is owned by a
                parallel agent and may not be registered with the
                router yet. Falling back to a native link keeps this
                card useful in either world; the `?user=` query param
                is honoured by the sessions page when it lands and
                ignored otherwise. */}
            <a
              href={`/sessions?user=${encodeURIComponent(row.original.user)}`}
              className="text-sm font-medium text-accent underline-offset-2 [@media(hover:hover)]:hover:underline"
              data-testid={`concurrent-spike-review-${row.original.id}`}
              aria-label={`Review sessions for ${row.original.user}`}
            >
              Review sessions
            </a>
          </div>
        ),
      },
    ],
    [],
  );

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      data-testid="concurrent-spikes-card"
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="size-4 text-fg-muted" aria-hidden />
            Concurrent-session spikes
          </CardTitle>
          <CardDescription>
            Users currently over the per-user concurrent-session threshold
            — a shared-credential / account-takeover signal.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {query.isLoading ? (
            <div
              className="flex flex-col gap-2 p-6"
              data-testid="concurrent-spikes-loading"
            >
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : query.error ? (
            <div
              role="alert"
              data-testid="concurrent-spikes-error"
              className="flex items-center gap-2 px-6 py-6 text-sm text-danger"
            >
              <AlertTriangle className="size-4" aria-hidden />
              {query.error.message}
            </div>
          ) : rows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={ShieldCheck}
                title="No concurrent-session spikes"
                description="No users are over the concurrent-session threshold right now."
              />
            </div>
          ) : (
            <div className="px-6 pb-6" data-testid="concurrent-spikes-table">
              <DataTable<SpikeRow>
                testId="concurrent-spike"
                columns={columns}
                data={rows}
                getRowId={(r) => r.id}
                caption={`${rows.length} spike${rows.length === 1 ? "" : "s"}`}
                emptyState="No concurrent-session spikes."
              />
            </div>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}
