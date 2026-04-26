// Edge-gateway summary panel — operator-facing aggregate of Envoy's
// admin API state. Surfaces the data points operators ask for when
// triaging from the dashboard:
//
//   * cluster member health (per-app reachability, hosts & healthy
//     counts)
//   * upstream request totals (which apps are hot, top-N volume)
//   * upstream request-time percentiles (p50/p95/p99 latency per
//     cluster — slow upstream detection without leaving the page)
//   * active connection counts (live websockets / streaming sessions)
//   * downstream request breakdown (gateway-level total / 2xx / 4xx
//     / 5xx, with badge tones)
//   * SSL/TLS handshake error counters (cert-expiry early warning)
//
// Pulls from `GET /api/envoy/admin-summary` which round-trips the
// controller's connection to Envoy's admin port (9901). 30s cache
// because most of the values (p99 latency in particular) move on a
// minute timescale anyway and a noisier poll just burns cluster CPU.

import { useMemo } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cloud,
  Gauge,
  Network,
  ShieldCheck,
} from "lucide-react";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

interface ClusterRow {
  name: string;
  hosts: number;
  healthy: number;
  added_via_api: boolean;
}

interface LatencyPercentiles {
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

interface DownstreamBreakdown {
  total: number;
  rq_2xx: number;
  rq_4xx: number;
  rq_5xx: number;
}

export interface EnvoyAdminSummary {
  clusters: readonly ClusterRow[];
  request_totals: Record<string, number>;
  request_p_latency_ms: Record<string, LatencyPercentiles>;
  active_connections: Record<string, number>;
  downstream_breakdown: DownstreamBreakdown;
  tls_handshake_errors: number;
  clusters_error?: string;
  stats_error?: string;
}

const QUERY_KEY = ["routing", "envoy", "admin-summary"] as const;

function useEnvoyAdminSummary(): UseQueryResult<EnvoyAdminSummary> {
  return useQuery<EnvoyAdminSummary>({
    queryKey: QUERY_KEY,
    queryFn: () => fetcher<EnvoyAdminSummary>("api/envoy/admin-summary"),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/**
 * Pretty cluster id for display: ``service_jellyfin`` →
 * ``jellyfin`` (drop the ``service_`` prefix Envoy synth-prepends).
 * ``ext_authz_authelia`` keeps the ``ext_authz_`` prefix because it's
 * a different cluster type that the operator should be able to
 * distinguish at a glance.
 */
function prettyCluster(name: string): string {
  if (name.startsWith("service_")) return name.slice("service_".length);
  return name;
}

export function EnvoyAdminSummaryCard() {
  const query = useEnvoyAdminSummary();
  const data = query.data;

  const topRoutes = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.request_totals)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 6);
  }, [data]);

  const slowestClusters = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.request_p_latency_ms)
      .map(([name, p]) => ({ name, ...p }))
      .filter((row) => typeof row.p99 === "number")
      .sort((a, b) => (b.p99 ?? 0) - (a.p99 ?? 0))
      .slice(0, 5);
  }, [data]);

  const totalActiveConnections = useMemo(() => {
    if (!data) return 0;
    return Object.values(data.active_connections).reduce((a, b) => a + b, 0);
  }, [data]);

  const breakdown = data?.downstream_breakdown;
  const errorRate = breakdown && breakdown.total > 0
    ? ((breakdown.rq_5xx + breakdown.rq_4xx) / breakdown.total) * 100
    : 0;

  const healthSummary = useMemo(() => {
    if (!data) return { total: 0, healthy: 0, unhealthy: 0 };
    let total = 0;
    let healthy = 0;
    for (const c of data.clusters) {
      total += c.hosts;
      healthy += c.healthy;
    }
    return { total, healthy, unhealthy: total - healthy };
  }, [data]);

  if (query.isLoading) {
    return (
      <Card data-testid="envoy-admin-summary-loading">
        <CardHeader>
          <CardTitle>Edge gateway summary</CardTitle>
          <CardDescription>Live cluster health from Envoy.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (query.error) {
    return (
      <Card
        role="alert"
        data-testid="envoy-admin-summary-error"
        className="border-[color-mix(in_oklab,var(--color-danger)_40%,transparent)]"
      >
        <CardHeader>
          <CardTitle>Edge gateway summary</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't reach Envoy admin: {(query.error as Error).message}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (!data) return null;

  return (
    <Card data-testid="envoy-admin-summary">
      <CardHeader>
        <CardTitle>Edge gateway summary</CardTitle>
        <CardDescription>
          Live data from Envoy's admin API — cluster health, traffic,
          latency, and TLS state. Refreshes every 30 seconds.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {/* KPI row */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            icon={<Network className="size-4" />}
            label="Healthy hosts"
            value={`${healthSummary.healthy}/${healthSummary.total}`}
            tone={
              healthSummary.unhealthy === 0
                ? "success"
                : healthSummary.unhealthy < healthSummary.total / 4
                  ? "warning"
                  : "danger"
            }
            testid="envoy-summary-healthy-hosts"
          />
          <Stat
            icon={<Activity className="size-4" />}
            label="Active connections"
            value={formatNumber(totalActiveConnections)}
            tone="info"
            testid="envoy-summary-active-cx"
          />
          <Stat
            icon={<Gauge className="size-4" />}
            label="4xx + 5xx rate"
            value={`${errorRate.toFixed(1)}%`}
            tone={
              errorRate < 1 ? "success" : errorRate < 5 ? "warning" : "danger"
            }
            testid="envoy-summary-error-rate"
          />
          <Stat
            icon={<ShieldCheck className="size-4" />}
            label="TLS handshake errors"
            value={String(data.tls_handshake_errors)}
            tone={data.tls_handshake_errors === 0 ? "success" : "danger"}
            testid="envoy-summary-tls-errors"
          />
        </div>

        {/* Downstream breakdown */}
        {breakdown && (
          <div
            className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-bg-1/40 p-3 text-sm"
            data-testid="envoy-summary-downstream"
          >
            <Cloud className="size-4 text-fg-muted" aria-hidden />
            <span className="font-medium text-fg">Downstream:</span>
            <Badge variant="outline" className="tabular-nums">
              {formatNumber(breakdown.total)} total
            </Badge>
            <Badge variant="success" className="tabular-nums">
              {formatNumber(breakdown.rq_2xx)} 2xx
            </Badge>
            <Badge variant="warning" className="tabular-nums">
              {formatNumber(breakdown.rq_4xx)} 4xx
            </Badge>
            <Badge
              variant={breakdown.rq_5xx > 0 ? "danger" : "outline"}
              className="tabular-nums"
            >
              {formatNumber(breakdown.rq_5xx)} 5xx
            </Badge>
          </div>
        )}

        {/* Top routes + slowest clusters */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Section
            title="Top traffic"
            description="Highest-volume upstream clusters by request total."
            testid="envoy-summary-top-routes"
          >
            {topRoutes.length === 0 ? (
              <span className="text-sm text-fg-muted">No traffic recorded.</span>
            ) : (
              <ul className="flex flex-col gap-1">
                {topRoutes.map(([cluster, count]) => (
                  <li
                    key={cluster}
                    className="flex items-center justify-between gap-2 text-sm"
                    data-testid={`envoy-summary-top-route-${cluster}`}
                  >
                    <span className="font-mono text-xs text-fg">
                      {prettyCluster(cluster)}
                    </span>
                    <span className="tabular-nums text-fg-muted">
                      {formatNumber(count)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section
            title="Slowest p99"
            description="Clusters with highest 99th-percentile request time."
            testid="envoy-summary-slowest"
          >
            {slowestClusters.length === 0 ? (
              <span className="text-sm text-fg-muted">
                No latency histograms yet.
              </span>
            ) : (
              <ul className="flex flex-col gap-1">
                {slowestClusters.map((row) => (
                  <li
                    key={row.name}
                    className="flex items-center justify-between gap-2 text-sm"
                    data-testid={`envoy-summary-slow-${row.name}`}
                  >
                    <span className="font-mono text-xs text-fg">
                      {prettyCluster(row.name)}
                    </span>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="tabular-nums text-fg-muted">
                          p99 {row.p99 ?? "—"} ms
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        p50 {row.p50 ?? "—"} ms · p95 {row.p95 ?? "—"} ms · p99{" "}
                        {row.p99 ?? "—"} ms
                      </TooltipContent>
                    </Tooltip>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>

        {/* Soft-fail banners */}
        {(data.clusters_error || data.stats_error) && (
          <div
            className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-fg"
            data-testid="envoy-summary-partial-warning"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <div>
              <div className="font-medium">Partial data</div>
              {data.clusters_error ? (
                <div>Clusters: {data.clusters_error}</div>
              ) : null}
              {data.stats_error ? <div>Stats: {data.stats_error}</div> : null}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface StatProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: "success" | "warning" | "danger" | "info";
  testid: string;
}

function Stat({ icon, label, value, tone, testid }: StatProps) {
  const toneClass = {
    success: "border-success/40 bg-success/10 text-success",
    warning: "border-warning/40 bg-warning/10 text-warning",
    danger: "border-danger/40 bg-danger/10 text-danger",
    info: "border-info/40 bg-info/10 text-info",
  }[tone];
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-md border bg-bg-1/40 p-3",
        toneClass,
      )}
      data-testid={testid}
    >
      <div className="flex items-center gap-1.5">
        {icon}
        <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">
          {label}
        </span>
      </div>
      <div className="text-xl font-semibold tabular-nums text-fg">{value}</div>
    </div>
  );
}

interface SectionProps {
  title: string;
  description: string;
  children: React.ReactNode;
  testid: string;
}

function Section({ title, description, children, testid }: SectionProps) {
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
      data-testid={testid}
    >
      <div>
        <div className="flex items-center gap-1.5">
          <CheckCircle2
            className="size-3.5 text-fg-faint opacity-0"
            aria-hidden
          />
          <span className="text-sm font-medium text-fg">{title}</span>
        </div>
        <p className="text-xs text-fg-muted">{description}</p>
      </div>
      {children}
    </div>
  );
}
