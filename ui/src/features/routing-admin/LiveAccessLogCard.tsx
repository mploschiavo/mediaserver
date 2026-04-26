import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { fetcher } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

interface AccessLogRow {
  ts?: string;
  method?: string;
  path?: string;
  status?: number;
  upstream?: string;
  upstream_host?: string;
  duration_ms?: number;
  client_ip?: string;
  /**
   * Full XFF chain as received. Useful audit trail when the deploy
   * has more proxy hops than xff_num_trusted_hops trims (e.g. CDN
   * added without a controller config update).
   */
  x_forwarded_for?: string;
  /** Cloudflare's authoritative client IP (CF strips inbound CF-* headers). */
  cf_connecting_ip?: string;
  x_real_ip?: string;
  host?: string;
  user_agent?: string;
  raw?: string;
}

interface AccessLogResponse {
  rows: AccessLogRow[];
  limit: number;
}

/**
 * Live request tail with full HTTP context — source IP, method,
 * path, status, upstream cluster, latency. Polls
 * ``/api/envoy/access-log?limit=50`` every 5s. The controller reads
 * Envoy's access log via either a file path (`ENVOY_ACCESS_LOG_PATH`),
 * `kubectl logs` (K8s mode), or `docker compose logs` (Compose mode);
 * each path is best-effort and falls through silently to the next.
 *
 * Each row is colour-tinted by status class (green 2xx · amber 4xx
 * · red 5xx). Rows whose JSON couldn't be parsed render the raw text
 * dimmed so operators can still see something for those weird
 * health-probe formats Envoy occasionally emits.
 */
export function LiveAccessLogCard() {
  const q = useQuery<AccessLogResponse>({
    queryKey: ["routing", "envoy", "access-log"],
    queryFn: () =>
      fetcher<AccessLogResponse>("api/envoy/access-log?limit=50"),
    refetchInterval: 5_000,
    staleTime: 2_500,
  });

  if (q.isLoading) {
    return (
      <Card data-testid="live-access-log-loading">
        <CardHeader>
          <CardTitle>Live request tail</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error) {
    return (
      <Card data-testid="live-access-log-error" role="alert">
        <CardHeader>
          <CardTitle>Live request tail</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't read the access log:{" "}
            {(q.error as Error).message}
          </p>
        </CardContent>
      </Card>
    );
  }

  // Server returns chronological order; reverse so newest is on top.
  const rows = [...(q.data?.rows ?? [])].reverse();

  return (
    <Card data-testid="live-access-log">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle className="flex items-center gap-2">
            <Activity className="size-4 text-success" aria-hidden />
            Live request tail
            <span
              className="inline-block size-1.5 animate-pulse rounded-full bg-success"
              aria-hidden
            />
          </CardTitle>
          <CardDescription>
            Last {q.data?.limit ?? 50} HTTP requests from Envoy's
            access log. Source IP, method, path, status, upstream
            cluster, and latency. Refreshes every 5 seconds.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <div
            className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
            data-testid="live-access-log-empty"
          >
            No access-log entries available. The controller looked at
            <code className="mx-1 rounded bg-bg-2 px-1 py-0.5 text-xs">
              ENVOY_ACCESS_LOG_PATH
            </code>
            , <code className="mx-1 rounded bg-bg-2 px-1 py-0.5 text-xs">kubectl logs</code>,
            and <code className="mx-1 rounded bg-bg-2 px-1 py-0.5 text-xs">docker compose logs</code>
            and got nothing back. Either the log isn't enabled or
            access is denied.
          </div>
        ) : (
          <ul
            className="flex flex-col divide-y divide-border/40"
            data-testid="live-access-log-rows"
          >
            {rows.map((r, idx) => (
              <li
                key={idx}
                className={cn(
                  "flex flex-wrap items-center gap-2 py-1 font-mono text-[11px] leading-tight",
                  idx === 0 && "bg-success/5",
                )}
                data-testid={`live-access-log-row-${idx}`}
              >
                {r.raw && r.method === undefined ? (
                  <span className="truncate text-fg-faint">{r.raw}</span>
                ) : (
                  <>
                    <ClientIpCell row={r} />
                    <Badge variant="outline" className="w-12 justify-center text-[10px]">
                      {r.method ?? "?"}
                    </Badge>
                    <span
                      className="flex-1 min-w-[140px] truncate text-fg"
                      title={hostAndPath(r)}
                    >
                      {r.path ?? "—"}
                    </span>
                    <StatusBadge status={r.status} />
                    <span className="w-32 truncate text-fg-muted" title={r.upstream}>
                      → {prettyUpstream(r.upstream)}
                    </span>
                    <span className="w-16 text-right tabular-nums text-fg-muted">
                      {r.duration_ms !== undefined && r.duration_ms !== null
                        ? `${r.duration_ms}ms`
                        : "—"}
                    </span>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Client IP cell with the full XFF chain on hover. The displayed
 * value is the resolved real client IP that Envoy chose after
 * applying ``xff_num_trusted_hops``; the tooltip surfaces:
 *
 *   * Cloudflare's CF-Connecting-IP (when Cloudflare is in front)
 *   * The full XFF chain as received
 *   * X-Real-Ip when the ingress controller set it
 *
 * Privacy ⚠: client IPs may be PII for some compliance regimes —
 * the panel is operator-only (auth-gated) so this is fine in the
 * media-stack default deployment.
 */
function ClientIpCell({ row }: { row: AccessLogRow }) {
  const display =
    row.client_ip ?? row.cf_connecting_ip ?? row.x_real_ip ?? "—";
  const hasChain =
    row.x_forwarded_for ||
    row.cf_connecting_ip ||
    row.x_real_ip ||
    row.client_ip;
  if (!hasChain) {
    return (
      <span className="w-24 shrink-0 truncate text-fg-faint">
        {display}
      </span>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="w-24 shrink-0 truncate text-fg-faint hover:text-fg-muted"
          data-testid="live-access-log-client-ip"
        >
          {display}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <div className="flex flex-col gap-0.5 font-mono text-[11px]">
          {row.cf_connecting_ip ? (
            <div>
              <span className="text-fg-faint">cf:</span>{" "}
              <span className="text-fg">{row.cf_connecting_ip}</span>
            </div>
          ) : null}
          {row.x_real_ip ? (
            <div>
              <span className="text-fg-faint">x-real-ip:</span>{" "}
              <span className="text-fg">{row.x_real_ip}</span>
            </div>
          ) : null}
          {row.x_forwarded_for ? (
            <div>
              <span className="text-fg-faint">x-forwarded-for:</span>{" "}
              <span className="text-fg">{row.x_forwarded_for}</span>
            </div>
          ) : null}
          {row.host ? (
            <div className="mt-1 border-t border-border pt-1">
              <span className="text-fg-faint">host:</span>{" "}
              <span className="text-fg">{row.host}</span>
            </div>
          ) : null}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function hostAndPath(row: AccessLogRow): string {
  const host = row.host ? `${row.host} ` : "";
  return `${host}${row.path ?? ""}`;
}

function StatusBadge({ status }: { status?: number }) {
  if (status === undefined || status === null) {
    return (
      <Badge variant="outline" className="w-10 justify-center text-[10px]">
        —
      </Badge>
    );
  }
  const tone =
    status >= 500
      ? "danger"
      : status >= 400
        ? "warning"
        : status >= 300
          ? "info"
          : "success";
  return (
    <Badge
      variant="outline"
      data-tone={tone}
      className="w-10 justify-center tabular-nums text-[10px]"
      data-testid={`live-access-log-status-${status}`}
    >
      {status}
    </Badge>
  );
}

function prettyUpstream(name?: string): string {
  if (!name) return "—";
  if (name.startsWith("service_")) return name.slice("service_".length);
  return name;
}
