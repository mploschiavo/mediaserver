import { asArray } from "@/lib/coerce";
import { useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, KeyRound, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/layout/EmptyState";
import { useFailedLogins, type FailedLoginCluster } from "./hooks";

interface FailedRow {
  id: string;
  identifier: string;
  attempts: number;
  first: string;
  last: string;
  raw: FailedLoginCluster;
}

const FAILED_THRESHOLD = 5;

function fmt(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function timespan(first?: string, last?: string): string {
  if (!first || !last) return "—";
  const a = new Date(first).getTime();
  const b = new Date(last).getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return "—";
  const ms = b - a;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

function severity(count: number): "info" | "warning" | "danger" {
  if (count >= FAILED_THRESHOLD * 2) return "danger";
  if (count >= FAILED_THRESHOLD) return "warning";
  return "info";
}

function clusterId(c: FailedLoginCluster, fallback: number): string {
  return c.ip_prefix ?? c.username ?? `cluster-${fallback}`;
}

function clusterIdentifier(c: FailedLoginCluster): string {
  return c.ip_prefix ?? c.username ?? "(unknown)";
}

function toRow(c: FailedLoginCluster, idx: number): FailedRow {
  return {
    id: clusterId(c, idx),
    identifier: clusterIdentifier(c),
    attempts: typeof c.attempt_count === "number" ? c.attempt_count : 0,
    first: c.first_seen ?? "",
    last: c.last_seen ?? "",
    raw: c,
  };
}

/**
 * The audit-log route is owned by a sibling agent — we link to it
 * defensively so this card stays useful even if the route hasn't
 * landed yet. When `auditLogAvailable` is false we fall back to a
 * raw-details dialog.
 */
export interface FailedLoginsCardProps {
  /** Set to false to disable the audit-log link and force the dialog fallback. */
  auditLogAvailable?: boolean;
}

export function FailedLoginsCard({
  auditLogAvailable = true,
}: FailedLoginsCardProps = {}) {
  const reduce = useReducedMotion();
  const query = useFailedLogins();
  const [details, setDetails] = useState<FailedRow | null>(null);

  const rows = useMemo<FailedRow[]>(() => {
    const list = asArray(query.data?.clusters);
    return list.map((c, i) => toRow(c, i));
  }, [query.data]);

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      data-testid="failed-logins-card"
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="size-4 text-fg-muted" aria-hidden />
            Failed login clusters
          </CardTitle>
          <CardDescription>
            Recent buckets of failed authentication attempts, grouped by
            IP/24 or username.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {query.isLoading ? (
            <div
              className="flex flex-col gap-2 p-6"
              data-testid="failed-logins-loading"
            >
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : query.error ? (
            <div
              role="alert"
              data-testid="failed-logins-error"
              className="flex items-center gap-2 px-6 py-6 text-sm text-danger"
            >
              <AlertTriangle className="size-4" aria-hidden />
              {query.error.message}
            </div>
          ) : rows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={ShieldAlert}
                title="No failed-login clusters"
                description="No credential-stuffing signals in the last 24 hours."
              />
            </div>
          ) : (
            <Table data-testid="failed-logins-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Identifier</TableHead>
                  <TableHead>Attempts</TableHead>
                  <TableHead>Timespan</TableHead>
                  <TableHead>Last seen</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow
                    key={row.id}
                    data-testid={`failed-login-row-${row.id}`}
                  >
                    <TableCell className="font-mono text-fg">
                      {row.identifier}
                    </TableCell>
                    <TableCell>
                      <Badge variant={severity(row.attempts)}>
                        {row.attempts}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums text-fg-muted">
                      {timespan(row.first, row.last)}
                    </TableCell>
                    <TableCell className="tabular-nums text-fg-muted">
                      {fmt(row.last)}
                    </TableCell>
                    <TableCell className="text-right">
                      {auditLogAvailable ? (
                        <Button
                          asChild
                          size="sm"
                          variant="secondary"
                          data-testid={`failed-login-investigate-${row.id}`}
                        >
                          <a
                            href={`/audit-log?action=auth.login.failed&actor=${encodeURIComponent(
                              row.identifier,
                            )}`}
                            aria-label={`Investigate failed logins for ${row.identifier}`}
                          >
                            Investigate
                          </a>
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setDetails(row)}
                          data-testid={`failed-login-investigate-${row.id}`}
                          aria-label={`Show raw details for ${row.identifier}`}
                        >
                          Investigate
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={details !== null}
        onOpenChange={(o) => !o && setDetails(null)}
      >
        <DialogContent data-testid="failed-login-details-dialog">
          <DialogHeader>
            <DialogTitle>Failed login cluster</DialogTitle>
            <DialogDescription>
              Raw details for {details?.identifier}.
            </DialogDescription>
          </DialogHeader>
          <pre
            className="max-h-80 overflow-auto rounded-md border border-border bg-bg-1 p-3 text-xs text-fg-muted"
            data-testid="failed-login-details-pre"
          >
            {details ? JSON.stringify(details.raw, null, 2) : ""}
          </pre>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="secondary" size="sm">
                Close
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </motion.div>
  );
}
