import { AlertTriangle } from "lucide-react";
import type { MediaIntegrityStatusShape } from "@/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { formatBytes, formatRelative } from "./format";
import { useBytesCounter } from "./use-bytes-counter";

interface StatusOverviewProps {
  status?: MediaIntegrityStatusShape;
  loading?: boolean;
  error?: Error | null;
}

const GRID = "grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3";

/** Pull a numeric `bytes_freed` out of the opaque report detail. */
function readBytesFreed(detail: Record<string, unknown> | undefined): number {
  if (!detail) return 0;
  const raw = detail.bytes_freed;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

export function StatusOverview({ status, loading, error }: StatusOverviewProps) {
  const bytes = readBytesFreed(status?.last_reconcile?.detail);
  const display = useBytesCounter(bytes, formatBytes);

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
        data-testid="status-overview-error"
      >
        <p className="font-medium">Failed to load Media Integrity status</p>
        <p className="mt-1 text-fg-muted">{error.message}</p>
      </div>
    );
  }

  if (loading || !status) {
    return (
      <div className={GRID} data-testid="status-overview-loading">
        {[0, 1, 2].map((i) => (
          <Card key={i}>
            <CardHeader>
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-40" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-8 w-32" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  const reconcileTs = status.last_reconcile?.ts ?? "";
  const enforceTs = status.last_enforce?.ts ?? "";
  const adapterCount = status.servarr_adapters.length;
  const missing = status.missing_api_keys;

  return (
    <div className={GRID} data-testid="status-overview">
      <Card>
        <CardHeader>
          <CardTitle>Bytes freed</CardTitle>
          <CardDescription>since last reconcile</CardDescription>
        </CardHeader>
        <CardContent>
          <div
            className="font-mono text-3xl font-semibold tabular-nums text-fg"
            data-testid="bytes-freed"
          >
            {display}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Last pass</CardTitle>
          <CardDescription>most recent agent activity</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-fg-muted">Reconcile</span>
            <span className="tabular-nums">{formatRelative(reconcileTs)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-fg-muted">Enforce</span>
            <span className="tabular-nums">{formatRelative(enforceTs)}</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
          <CardDescription>policy v{status.policy_version}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info">{adapterCount} servarr</Badge>
            <Badge variant={status.bazarr_present ? "success" : "outline"}>
              {status.bazarr_present ? "bazarr on" : "bazarr off"}
            </Badge>
          </div>
          {missing.length > 0 ? (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_12%,transparent)] p-2 text-warning"
              data-testid="missing-api-keys"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <div className="text-xs">
                Missing API keys: {missing.join(", ")}
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
