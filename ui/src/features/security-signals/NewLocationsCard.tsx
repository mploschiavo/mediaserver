import { asArray } from "@/lib/coerce";
import { useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, MapPin, ShieldCheck } from "lucide-react";
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { EmptyState } from "@/components/layout/EmptyState";
import { useNewLocations, type NewLocationAlert } from "./hooks";

interface AlertRow {
  id: string;
  user: string;
  priorIp: string;
  priorGeo: string;
  newIp: string;
  newGeo: string;
  observedAt: string;
  provider: string;
}

function fmt(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function alertId(a: NewLocationAlert, idx: number): string {
  return (
    `${a.username ?? "anon"}-${a.observed_at ?? a.ip_prefix ?? idx}`.replace(
      /\s+/g,
      "-",
    ) || `alert-${idx}`
  );
}

function toRow(a: NewLocationAlert, idx: number): AlertRow {
  return {
    id: alertId(a, idx),
    user: a.username ?? "(anonymous)",
    priorIp: a.prior_ip ?? "—",
    priorGeo: a.prior_geo ?? "",
    newIp: a.ip ?? a.ip_prefix ?? "—",
    newGeo: a.geo ?? "",
    observedAt: a.observed_at ?? "",
    provider: a.provider ?? "",
  };
}

export function NewLocationsCard() {
  const reduce = useReducedMotion();
  const query = useNewLocations();

  const rows = useMemo<AlertRow[]>(() => {
    const list = asArray(query.data?.alerts);
    return list.map((a, i) => toRow(a, i));
  }, [query.data]);

  const columns = useMemo<ColumnDef<AlertRow>[]>(
    () => [
      {
        id: "user",
        accessorFn: (r) => r.user,
        header: "User",
        meta: { label: "User" },
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-medium text-fg">{row.original.user}</span>
            {row.original.provider ? (
              <Badge variant="info">{row.original.provider}</Badge>
            ) : null}
          </div>
        ),
      },
      {
        id: "prior",
        accessorFn: (r) => `${r.priorIp} ${r.priorGeo}`,
        header: "Prior IP / geo",
        meta: { label: "Prior IP / geo" },
        cell: ({ row }) => (
          <div className="flex flex-col text-fg-muted">
            <span className="font-mono tabular-nums">
              {row.original.priorIp}
            </span>
            {row.original.priorGeo ? (
              <span className="text-xs text-fg-faint">
                {row.original.priorGeo}
              </span>
            ) : null}
          </div>
        ),
      },
      {
        id: "new",
        accessorFn: (r) => `${r.newIp} ${r.newGeo}`,
        header: "New IP / geo",
        meta: { label: "New IP / geo" },
        cell: ({ row }) => (
          <div className="flex flex-col text-fg">
            <span className="font-mono tabular-nums">{row.original.newIp}</span>
            {row.original.newGeo ? (
              <span className="text-xs text-fg-muted">
                {row.original.newGeo}
              </span>
            ) : null}
          </div>
        ),
      },
      {
        id: "observed_at",
        accessorFn: (r) => r.observedAt,
        header: "Observed",
        meta: { label: "Observed" },
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="tabular-nums text-fg-muted">
            {fmt(row.original.observedAt)}
          </span>
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
            <Tooltip>
              <TooltipTrigger asChild>
                {/* The disabled <button> needs a wrapping span so the
                    tooltip still receives pointer events. */}
                <span className="inline-block">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled
                    aria-disabled
                    data-testid={`new-location-ack-${row.original.id}`}
                    aria-label={`Acknowledge new-location alert for ${row.original.user}`}
                  >
                    Acknowledge
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                Acknowledgement endpoint pending
              </TooltipContent>
            </Tooltip>
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
      data-testid="new-locations-card"
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MapPin className="size-4 text-fg-muted" aria-hidden />
            New-location alerts
          </CardTitle>
          <CardDescription>
            Known users who signed in from a previously-unseen IP or
            geolocation.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {query.isLoading ? (
            <div
              className="flex flex-col gap-2 p-6"
              data-testid="new-locations-loading"
            >
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : query.error ? (
            <div
              role="alert"
              data-testid="new-locations-error"
              className="flex items-center gap-2 px-6 py-6 text-sm text-danger"
            >
              <AlertTriangle className="size-4" aria-hidden />
              {query.error.message}
            </div>
          ) : rows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={ShieldCheck}
                title="✓ All clear — no new-location alerts"
                description="No known user has signed in from an IP or geo we haven't seen them at before."
              />
            </div>
          ) : (
            <div className="px-6 pb-6" data-testid="new-locations-table">
              <DataTable<AlertRow>
                testId="new-location"
                columns={columns}
                data={rows}
                getRowId={(r) => r.id}
                caption={`${rows.length} alert${rows.length === 1 ? "" : "s"}`}
                emptyState="No new-location alerts."
              />
            </div>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}
