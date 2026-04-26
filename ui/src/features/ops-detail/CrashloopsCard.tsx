import { useMemo } from "react";
import { CheckCircle2, RefreshCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { useCrashloops, type CrashloopEntry } from "./hooks";

interface CrashloopRow {
  id: string;
  service: string;
  restartCount: number;
  lastExitCode: string;
  lastSeen: string;
  description: string;
  cause: string;
}

function formatRelativeFromEpoch(seconds?: number): string {
  if (!seconds || !Number.isFinite(seconds)) return "—";
  const t = seconds * 1000;
  const delta = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function toRow(serviceId: string, entry: CrashloopEntry): CrashloopRow {
  const exit =
    entry.last_terminated_reason && entry.last_terminated_reason !== ""
      ? entry.last_terminated_reason
      : "—";
  return {
    id: serviceId,
    service: entry.service_id ?? serviceId,
    restartCount: entry.restart_count ?? 0,
    lastExitCode: exit,
    lastSeen: formatRelativeFromEpoch(entry.checked_at),
    description: entry.description ?? "",
    cause: entry.cause ?? "",
  };
}

/** Filter to "actively crashlooping" — anything where cause is not
 * "healthy" / empty and restart_count > 0. */
function isCrashlooping(entry: CrashloopEntry): boolean {
  const cause = (entry.cause ?? "").toLowerCase();
  if (!cause || cause === "healthy") return false;
  return (entry.restart_count ?? 0) > 0;
}

export function CrashloopsCard() {
  const query = useCrashloops();

  const rows = useMemo<CrashloopRow[]>(() => {
    // Defensive: payload is `additionalProperties: true`. If a
    // re-fetch returns `services: []` or anything non-object,
    // Object.entries crashes — coerce first.
    const raw = query.data?.services;
    const services: Record<string, unknown> =
      raw && typeof raw === "object" && !Array.isArray(raw)
        ? (raw as Record<string, unknown>)
        : {};
    return Object.entries(services)
      .filter(([, e]) => isCrashlooping(e as Parameters<typeof isCrashlooping>[0]))
      .map(([id, e]) => toRow(id, e as Parameters<typeof toRow>[1]));
  }, [query.data]);

  const columns: ResponsiveTableColumn<CrashloopRow>[] = [
    {
      id: "service",
      header: "Service",
      cell: (row) => (
        <span className="font-medium text-fg">{row.service}</span>
      ),
    },
    {
      id: "restarts",
      header: "Restarts",
      cell: (row) => (
        <Badge variant={row.restartCount >= 5 ? "danger" : "warning"}>
          {row.restartCount}
        </Badge>
      ),
    },
    {
      id: "exit",
      header: "Last exit",
      cell: (row) => (
        <span className="font-mono text-xs text-fg-muted">
          {row.lastExitCode}
        </span>
      ),
    },
    {
      id: "last-seen",
      header: "Last seen",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">{row.lastSeen}</span>
      ),
    },
  ];

  return (
    <Card data-testid="crashloops-card">
      <CardHeader>
        <CardTitle>Crashloops</CardTitle>
        <CardDescription>
          Containers in restart loops with their last terminated reason
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0 sm:p-0">
        {query.isLoading ? (
          <div
            className="flex flex-col gap-2 p-6 pt-0"
            data-testid="crashloops-loading"
          >
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="crashloops-error"
            className="px-6 pb-6 text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : rows.length === 0 ? (
          <div className="px-6 pb-6">
            <EmptyState
              icon={CheckCircle2}
              title="✓ All clear — no services crashlooping"
              description={
                "Probed every registry-tracked service; every container's "
                + "restart count is below the threshold. CronJob pods + non-"
                + "registry workloads are tracked separately in Jobs / kubectl."
              }
            />
          </div>
        ) : (
          <div className="px-6 pb-6">
            <ResponsiveTable
              rows={rows}
              rowKey={(r) => r.id}
              columns={columns}
              card={(row) => (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-fg">{row.service}</span>
                    <Badge
                      variant={row.restartCount >= 5 ? "danger" : "warning"}
                    >
                      <RefreshCcw aria-hidden className="size-3" />
                      {row.restartCount}
                    </Badge>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    <span className="text-fg-muted">Last exit</span>
                    <span className="text-right font-mono">
                      {row.lastExitCode}
                    </span>
                    <span className="text-fg-muted">Last seen</span>
                    <span className="text-right tabular-nums">
                      {row.lastSeen}
                    </span>
                  </div>
                  {row.description ? (
                    <p className="text-xs text-fg-muted">{row.description}</p>
                  ) : null}
                </div>
              )}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
