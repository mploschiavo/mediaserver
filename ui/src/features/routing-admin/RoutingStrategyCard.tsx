import { useState } from "react";
import { Pencil } from "lucide-react";
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
import { RoutingEditor } from "./RoutingEditor";
import type { RoutingResponse } from "./hooks";

interface RoutingStrategyCardProps {
  loading?: boolean;
  data?: RoutingResponse;
  error?: Error | null;
  onRetry?: () => void;
}

function StrategyField({
  label,
  value,
  testid,
}: {
  label: string;
  value: string;
  testid?: string;
}) {
  return (
    <div>
      <div className="text-fg-muted">{label}</div>
      <div className="mt-1 font-mono text-fg" data-testid={testid}>
        {value}
      </div>
    </div>
  );
}

/**
 * Read-only display of the current routing strategy. The wire shape
 * is the flat `RoutingConfig` (`base_domain`, `gateway_host`,
 * `gateway_port`, `app_path_prefix`, `strategy`, `internet_exposed`,
 * `direct_hosts`) — there is no per-app list under this endpoint;
 * reachability/health belongs to the separate `/api/routing-probe`
 * matrix surfaced by `ReachabilityMatrix`.
 */
export function RoutingStrategyCard({
  loading = false,
  data,
  error = null,
  onRetry,
}: RoutingStrategyCardProps) {
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <RoutingEditor
        initial={
          data
            ? {
                strategy:
                  (data.strategy as "hybrid" | "subdomain" | "path") ??
                  "hybrid",
                base_domain: data.base_domain ?? "",
                external_hostname: data.gateway_host ?? "",
              }
            : undefined
        }
        onCancel={() => setEditing(false)}
        onSaved={() => setEditing(false)}
      />
    );
  }

  if (error) {
    return (
      <Card
        role="alert"
        data-testid="routing-strategy-error"
        className="border-[color-mix(in_oklab,var(--color-danger)_40%,transparent)]"
      >
        <CardContent className="flex flex-col gap-3 p-6">
          <div className="text-sm font-medium text-danger">
            Failed to load routing
          </div>
          <p className="text-sm text-fg-muted">{error.message}</p>
          {onRetry ? (
            <div>
              <Button
                variant="secondary"
                size="sm"
                onClick={onRetry}
                data-testid="routing-strategy-retry"
              >
                Retry
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    );
  }

  if (loading) {
    return (
      <Card data-testid="routing-strategy-loading">
        <CardHeader>
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-3 w-48" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-6 w-48" />
        </CardContent>
      </Card>
    );
  }

  const strategy = data?.strategy ?? "unknown";
  const baseDomain = data?.base_domain ?? "—";
  const gatewayHost = data?.gateway_host ?? "—";
  const gatewayPort =
    typeof data?.gateway_port === "number" ? data.gateway_port : null;
  const gateway =
    gatewayPort !== null ? `${gatewayHost}:${gatewayPort}` : gatewayHost;
  const appPathPrefix = data?.app_path_prefix || "—";
  const internetExposed = data?.internet_exposed === true;

  return (
    <Card data-testid="routing-strategy-card">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle>Strategy</CardTitle>
          <CardDescription>How public traffic is dispatched.</CardDescription>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setEditing(true)}
          data-testid="routing-strategy-edit"
        >
          <Pencil className="size-4" aria-hidden />
          Edit
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <div className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <StrategyField
            label="Mode"
            value={strategy}
            testid="routing-strategy-mode"
          />
          <StrategyField
            label="Base domain"
            value={baseDomain}
            testid="routing-strategy-base-domain"
          />
          <StrategyField
            label="Gateway"
            value={gateway}
            testid="routing-strategy-gateway"
          />
          <StrategyField
            label="App path prefix"
            value={appPathPrefix}
            testid="routing-strategy-app-path-prefix"
          />
        </div>

        <div className="flex items-center gap-2 text-sm">
          <span className="text-fg-muted">Internet exposed</span>
          <Badge variant={internetExposed ? "warning" : "outline"}>
            {internetExposed ? "yes" : "no"}
          </Badge>
        </div>
      </CardContent>
    </Card>
  );
}
