import { asArray } from "@/lib/coerce";
import { useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, MapPin, ShieldCheck } from "lucide-react";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
                title="No new-location alerts"
                description="No known users have signed in from an unfamiliar IP or geo."
              />
            </div>
          ) : (
            <Table data-testid="new-locations-table">
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Prior IP / geo</TableHead>
                  <TableHead>New IP / geo</TableHead>
                  <TableHead>Observed</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow
                    key={row.id}
                    data-testid={`new-location-row-${row.id}`}
                  >
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-fg">{row.user}</span>
                        {row.provider ? (
                          <Badge variant="info">{row.provider}</Badge>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="text-fg-muted">
                      <div className="flex flex-col">
                        <span className="font-mono tabular-nums">
                          {row.priorIp}
                        </span>
                        {row.priorGeo ? (
                          <span className="text-xs text-fg-faint">
                            {row.priorGeo}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="text-fg">
                      <div className="flex flex-col">
                        <span className="font-mono tabular-nums">
                          {row.newIp}
                        </span>
                        {row.newGeo ? (
                          <span className="text-xs text-fg-muted">
                            {row.newGeo}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="tabular-nums text-fg-muted">
                      {fmt(row.observedAt)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          {/* The disabled <button> needs a wrapping span
                              so the tooltip still receives pointer events. */}
                          <span className="inline-block">
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled
                              aria-disabled
                              data-testid={`new-location-ack-${row.id}`}
                              aria-label={`Acknowledge new-location alert for ${row.user}`}
                            >
                              Acknowledge
                            </Button>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          Acknowledgement endpoint pending
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}
