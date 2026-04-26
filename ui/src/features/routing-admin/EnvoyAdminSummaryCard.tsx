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

import { Fragment, useMemo, useState } from "react";
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
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetcher } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { ClusterDetailDrawer } from "./ClusterDetailDrawer";
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

interface PieDatum {
  name: string;
  value: number;
  color: string;
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

interface TimeseriesSample {
  ts: number;
  rq_total: number;
  rq_2xx: number;
  rq_4xx: number;
  rq_5xx: number;
  healthy: number;
  total_hosts: number;
  active_cx: number;
  tls_errors: number;
  rq_per_cluster?: Record<string, number>;
  active_per_cluster?: Record<string, number>;
  latency_per_cluster?: Record<
    string,
    { p50: number | null; p95: number | null; p99: number | null }
  >;
}

interface TimeseriesDelta {
  ts: number;
  rq_per_s: number;
  err_per_s: number;
  active_cx: number;
  healthy: number;
  total_hosts: number;
  rq_per_cluster_per_s?: Record<string, number>;
}

interface EnvoyTimeseries {
  samples: readonly TimeseriesSample[];
  deltas: readonly TimeseriesDelta[];
  window_seconds: number;
  now: number;
}

const QUERY_KEY = ["routing", "envoy", "admin-summary"] as const;
const TS_QUERY_KEY = ["routing", "envoy", "timeseries"] as const;

// Grafana-style refresh cadences. Picked to cover the operator's
// usual rhythms: "watching a deploy" (2-5s, hot polling), "background
// dashboard" (15-30s), and "left it open as a sanity check" (60s-5m).
// Anything below 2s tends to overrun the controller's poll-of-Envoy
// round-trip and just stacks queued requests.
export interface RefreshOption {
  label: string;
  intervalMs: number;
}

export const REFRESH_OPTIONS: readonly RefreshOption[] = [
  { label: "2s", intervalMs: 2_000 },
  { label: "5s", intervalMs: 5_000 },
  { label: "10s", intervalMs: 10_000 },
  { label: "15s", intervalMs: 15_000 },
  { label: "30s", intervalMs: 30_000 },
  { label: "60s", intervalMs: 60_000 },
  { label: "5m", intervalMs: 300_000 },
] as const;

const DEFAULT_INTERVAL_MS = 30_000;

function useEnvoyAdminSummary(intervalMs: number): UseQueryResult<EnvoyAdminSummary> {
  return useQuery<EnvoyAdminSummary>({
    queryKey: QUERY_KEY,
    queryFn: () => fetcher<EnvoyAdminSummary>("api/envoy/admin-summary"),
    refetchInterval: intervalMs,
    // Stale time half the refetch interval — keeps tooltips/legends
    // from re-fetching on every hover when the cadence is slow.
    staleTime: Math.max(1_000, Math.floor(intervalMs / 2)),
  });
}

function useEnvoyTimeseries(intervalMs: number): UseQueryResult<EnvoyTimeseries> {
  return useQuery<EnvoyTimeseries>({
    queryKey: TS_QUERY_KEY,
    queryFn: () => fetcher<EnvoyTimeseries>("api/envoy/timeseries?window=1800"),
    // Match the admin-summary cadence so we re-poll at the same beats
    // — the buffer fills as a side-effect of the admin-summary call,
    // so an out-of-phase poll would miss the freshest sample.
    refetchInterval: intervalMs,
    staleTime: Math.max(1_000, Math.floor(intervalMs / 2)),
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
  const [intervalMs, setIntervalMs] = useState<number>(DEFAULT_INTERVAL_MS);
  const [drillCluster, setDrillCluster] = useState<string | null>(null);
  const query = useEnvoyAdminSummary(intervalMs);
  const tsQuery = useEnvoyTimeseries(intervalMs);
  const data = query.data;
  const ts = tsQuery.data;

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

  const breakdown = data?.downstream_breakdown;
  const errorRate = breakdown && breakdown.total > 0
    ? ((breakdown.rq_5xx + breakdown.rq_4xx) / breakdown.total) * 100
    : 0;

  const responseCodeData = useMemo<readonly PieDatum[]>(() => {
    if (!breakdown || breakdown.total === 0) return [];
    // Envoy's /stats doesn't expose `rq_3xx` directly; derive it as
    // the residual so redirects show up as a real slice instead of
    // disappearing into rounding error.
    const rq_3xx = Math.max(
      0,
      breakdown.total - breakdown.rq_2xx - breakdown.rq_4xx - breakdown.rq_5xx,
    );
    return [
      { name: "2xx", value: breakdown.rq_2xx, color: "var(--color-success)" },
      { name: "3xx", value: rq_3xx, color: "var(--color-info)" },
      { name: "4xx", value: breakdown.rq_4xx, color: "var(--color-warning)" },
      { name: "5xx", value: breakdown.rq_5xx, color: "var(--color-danger)" },
    ].filter((r) => r.value > 0);
  }, [breakdown]);

  const clusterTrafficData = useMemo<readonly PieDatum[]>(() => {
    if (!data) return [];
    const sorted = Object.entries(data.request_totals).sort(
      ([, a], [, b]) => b - a,
    );
    const top = sorted.slice(0, 5);
    const restSum = sorted.slice(5).reduce((acc, [, v]) => acc + v, 0);
    const palette = [
      "var(--color-accent)",
      "var(--color-info)",
      "var(--color-success)",
      "var(--color-warning)",
      "var(--color-danger)",
    ];
    const rows: PieDatum[] = top
      .filter(([, v]) => v > 0)
      .map(([name, value], i) => ({
        name: prettyCluster(name),
        value,
        color: palette[i % palette.length] ?? "var(--color-accent)",
      }));
    if (restSum > 0) {
      rows.push({
        name: "other",
        value: restSum,
        color: "var(--color-fg-faint)",
      });
    }
    return rows;
  }, [data]);

  const clusterHealthData = useMemo<readonly PieDatum[]>(() => {
    if (healthSummary.total === 0) return [];
    const rows: PieDatum[] = [];
    if (healthSummary.healthy > 0) {
      rows.push({
        name: "Healthy",
        value: healthSummary.healthy,
        color: "var(--color-success)",
      });
    }
    if (healthSummary.unhealthy > 0) {
      rows.push({
        name: "Unhealthy",
        value: healthSummary.unhealthy,
        color: "var(--color-danger)",
      });
    }
    return rows;
  }, [healthSummary]);

  // Sparkline data extracted from the rolling buffer. Each series
  // surfaces alongside its KPI Stat so the operator sees direction
  // ("trending up" / "spiking" / "flat") instead of a single number.
  // Need ≥2 samples to render a polyline; single-sample fall-throughs
  // show no sparkline rather than a degenerate dot.
  const sparkSamples = ts?.samples ?? [];
  const sparkDeltas = ts?.deltas ?? [];
  const healthSpark = useMemo(
    () => sparkSamples.map((s) => s.healthy),
    [sparkSamples],
  );
  const activeCxSpark = useMemo(
    () => sparkSamples.map((s) => s.active_cx),
    [sparkSamples],
  );
  const errorRateSpark = useMemo(
    () =>
      sparkSamples.map((s) => {
        const t = s.rq_total;
        return t > 0 ? ((s.rq_4xx + s.rq_5xx) / t) * 100 : 0;
      }),
    [sparkSamples],
  );
  const tlsErrorsSpark = useMemo(
    () => sparkSamples.map((s) => s.tls_errors),
    [sparkSamples],
  );

  // Live request-rate chart — uses the per-bucket deltas (Δcount/Δt)
  // because the underlying Envoy counters are monotonic; raw counts
  // would just plot a staircase up-and-to-the-right.
  const rateChartData = useMemo(
    () =>
      sparkDeltas.map((d) => ({
        ts: d.ts,
        rq_per_s: Number(d.rq_per_s.toFixed(2)),
        err_per_s: Number(d.err_per_s.toFixed(2)),
      })),
    [sparkDeltas],
  );

  // Per-cluster traffic series — picks the top-5 clusters by mean
  // request rate over the buffered window and emits a flattened
  // [{ts, <cluster>: rate, <cluster>: rate, …}] shape that
  // recharts' multi-Line chart can read directly. Anything outside
  // the top-5 collapses into "other" so a 50-service deploy doesn't
  // produce 50 illegible lines.
  const perClusterTraffic = useMemo(() => {
    if (sparkDeltas.length < 2) {
      return { rows: [] as Record<string, number | string>[], topClusters: [] as string[] };
    }
    const sums = new Map<string, number>();
    for (const d of sparkDeltas) {
      for (const [cluster, rate] of Object.entries(
        d.rq_per_cluster_per_s ?? {},
      )) {
        sums.set(cluster, (sums.get(cluster) ?? 0) + rate);
      }
    }
    const top = [...sums.entries()]
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5)
      .map(([n]) => n);
    const topSet = new Set(top);
    const rows = sparkDeltas.map((d) => {
      const row: Record<string, number | string> = { ts: d.ts };
      let other = 0;
      for (const [cluster, rate] of Object.entries(
        d.rq_per_cluster_per_s ?? {},
      )) {
        if (topSet.has(cluster)) {
          row[cluster] = rate;
        } else {
          other += rate;
        }
      }
      if (other > 0) row.other = Number(other.toFixed(3));
      return row;
    });
    return { rows, topClusters: top };
  }, [sparkDeltas]);

  // Latency-over-time heatmap data — cluster × bucket grid of p99
  // values. Picks the top-6 clusters by max p99 so the worst tails
  // surface first; anything quieter is hidden to keep the grid
  // readable. Each cell carries (p50, p95, p99) so the tooltip can
  // show the full quantile triple on hover.
  const latencyHeatmap = useMemo(() => {
    if (sparkSamples.length < 2) {
      return { clusters: [] as string[], buckets: [] as number[],
               cells: {} as Record<string, Record<number, { p50: number | null; p95: number | null; p99: number | null }>> };
    }
    // Aggregate the worst p99 per cluster across the window so we
    // can pick the top-6.
    const worstP99 = new Map<string, number>();
    for (const s of sparkSamples) {
      for (const [cluster, q] of Object.entries(s.latency_per_cluster ?? {})) {
        const p99 = q.p99;
        if (typeof p99 !== "number") continue;
        const cur = worstP99.get(cluster) ?? 0;
        if (p99 > cur) worstP99.set(cluster, p99);
      }
    }
    const topClusters = [...worstP99.entries()]
      .sort(([, a], [, b]) => b - a)
      .slice(0, 6)
      .map(([n]) => n);
    const buckets = sparkSamples.map((s) => s.ts);
    const cells: Record<string, Record<number, { p50: number | null; p95: number | null; p99: number | null }>> = {};
    for (const cluster of topClusters) {
      cells[cluster] = {};
      for (const s of sparkSamples) {
        const q = s.latency_per_cluster?.[cluster];
        if (q) cells[cluster][s.ts] = q;
      }
    }
    return { clusters: topClusters, buckets, cells };
  }, [sparkSamples]);

  // Per-cluster active connections — current snapshot, not a series.
  // Sorted desc; capped at 8 rows for visual hygiene. ``raw``
  // preserves the unprefixed cluster name so the drill-down handler
  // can match against the timeseries buffer (which keys on
  // ``service_<id>``).
  const activeCxBreakdown = useMemo(() => {
    if (!data) return [];
    const entries = Object.entries(data.active_connections);
    return entries
      .filter(([, n]) => n > 0)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 8)
      .map(([name, n]) => ({
        name: prettyCluster(name),
        value: n,
        raw: name,
      }));
  }, [data]);

  // Latency heatmap — every cluster with histogram data, sorted by
  // p99 desc so the worst tail-latency surfaces first. The "Slowest
  // p99" section above is a top-5 summary; the heatmap is the full
  // per-cluster breakdown for deep triage.
  const latencyHeatRows = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.request_p_latency_ms)
      .map(([name, p]) => ({ name, ...p }))
      .filter((r) => r.p50 !== null || r.p95 !== null || r.p99 !== null)
      .sort((a, b) => (b.p99 ?? 0) - (a.p99 ?? 0));
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
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <CardTitle>Edge gateway summary</CardTitle>
            <CardDescription>
              Live data from Envoy's admin API — cluster health,
              traffic, latency, and TLS state.
            </CardDescription>
          </div>
          <RefreshSelector
            value={intervalMs}
            onChange={setIntervalMs}
          />
        </div>
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
            spark={healthSpark}
          />
          <Stat
            icon={<Activity className="size-4" />}
            label="Active connections"
            value={formatNumber(totalActiveConnections)}
            tone="info"
            testid="envoy-summary-active-cx"
            spark={activeCxSpark}
          />
          <Stat
            icon={<Gauge className="size-4" />}
            label="4xx + 5xx rate"
            value={`${errorRate.toFixed(1)}%`}
            tone={
              errorRate < 1 ? "success" : errorRate < 5 ? "warning" : "danger"
            }
            testid="envoy-summary-error-rate"
            spark={errorRateSpark}
          />
          <Stat
            icon={<ShieldCheck className="size-4" />}
            label="TLS handshake errors"
            value={String(data.tls_handshake_errors)}
            tone={data.tls_handshake_errors === 0 ? "success" : "danger"}
            testid="envoy-summary-tls-errors"
            spark={tlsErrorsSpark}
          />
        </div>

        {/* Live request-rate chart — shows derived rate (Δrequests/Δt)
            since admin polling started. Not a long-term graph; for
            durable history graph the Prometheus /metrics feed in
            Grafana. Always rendered with an explicit empty state so
            the operator sees the feature even before the rolling
            buffer has filled (it needs ≥2 polls = 60s after a fresh
            pod boot to draw the first line). */}
        <div
          className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
          data-testid="envoy-summary-rate-chart"
        >
          <div>
            <span className="text-sm font-medium text-fg">
              Request rate (live)
            </span>
            <p className="text-xs text-fg-muted">
              Requests/sec downstream and 4xx+5xx error rate, derived
              from the rolling buffer.{" "}
              {rateChartData.length >= 2
                ? `${rateChartData.length} bucket${
                    rateChartData.length === 1 ? "" : "s"
                  } since the panel opened.`
                : tsQuery.isLoading
                  ? "Loading first sample…"
                  : (ts?.samples.length ?? 0) === 0
                    ? "Waiting for first sample (polls every 30s)."
                    : `1 sample collected — need 2 to draw a trend (next poll in <30s).`}
            </p>
          </div>
          {rateChartData.length >= 2 ? (
            <div className="h-44 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={[...rateChartData]}
                  margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
                >
                  <defs>
                    <linearGradient
                      id="rqRateFill"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="0%"
                        stopColor="var(--color-info)"
                        stopOpacity={0.4}
                      />
                      <stop
                        offset="100%"
                        stopColor="var(--color-info)"
                        stopOpacity={0}
                      />
                    </linearGradient>
                    <linearGradient
                      id="errRateFill"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="0%"
                        stopColor="var(--color-danger)"
                        stopOpacity={0.4}
                      />
                      <stop
                        offset="100%"
                        stopColor="var(--color-danger)"
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="2 4"
                    stroke="var(--color-border)"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="ts"
                    tickFormatter={(t) =>
                      new Date(Number(t) * 1000).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    }
                    tick={{ fill: "var(--color-fg-muted)", fontSize: 10 }}
                    axisLine={{ stroke: "var(--color-border)" }}
                    tickLine={false}
                    minTickGap={24}
                  />
                  <YAxis
                    tick={{ fill: "var(--color-fg-muted)", fontSize: 10 }}
                    axisLine={{ stroke: "var(--color-border)" }}
                    tickLine={false}
                    width={32}
                  />
                  <ChartTooltip
                    contentStyle={{
                      background: "var(--color-bg-2)",
                      border: "1px solid var(--color-border)",
                      borderRadius: 6,
                      fontSize: 12,
                      color: "var(--color-fg)",
                    }}
                    labelFormatter={(t) =>
                      new Date(Number(t) * 1000).toLocaleTimeString()
                    }
                    formatter={(v, name) => {
                      const key = String(name ?? "");
                      const num = typeof v === "number" ? v : Number(v ?? 0);
                      return [
                        `${num} ${key === "rq_per_s" ? "rq/s" : "err/s"}`,
                        key === "rq_per_s" ? "Requests" : "Errors",
                      ];
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="rq_per_s"
                    stroke="var(--color-info)"
                    fill="url(#rqRateFill)"
                    strokeWidth={1.5}
                    isAnimationActive={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="err_per_s"
                    stroke="var(--color-danger)"
                    fill="url(#errRateFill)"
                    strokeWidth={1.5}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div
              className="flex h-44 w-full items-center justify-center rounded border border-dashed border-border/60 bg-bg-1/40 text-xs text-fg-muted"
              data-testid="envoy-summary-rate-chart-empty"
            >
              {tsQuery.isLoading
                ? "Loading…"
                : "Trend appears once two polls have completed."}
            </div>
          )}
        </div>

        {/* Per-cluster traffic — top 5 + 'other'. Answers "which
            cluster is hot right now" over time. Lines are tone-tinted
            from a fixed palette so a single cluster keeps its colour
            across re-renders. Falls through to an empty-state caption
            when fewer than two buckets exist (same gate as the
            request-rate chart above). */}
        <PerClusterTrafficCard
          rows={perClusterTraffic.rows}
          topClusters={perClusterTraffic.topClusters}
          loading={tsQuery.isLoading}
          onClusterClick={setDrillCluster}
        />

        {/* Active connections by cluster — point-in-time snapshot
            (not a series). Useful for "who's holding open WebSockets
            / streaming sessions right now". Hidden when no cluster
            has any active connections (the common case for a quiet
            stack). */}
        {activeCxBreakdown.length > 0 ? (
          <ActiveConnectionsCard
            data={activeCxBreakdown}
            onClusterClick={setDrillCluster}
          />
        ) : null}

        {/* Latency-over-time heatmap — top-6 clusters by worst p99
            across the buffer. Cells are colour-graded
            (green <100 / amber <500 / red ≥500); hover shows the
            full p50/p95/p99 triple. Sparse cells (cluster had no
            histogram data in that bucket) render as faint
            placeholders so the grid stays aligned. Click a row
            label to open the drill-down drawer. */}
        {latencyHeatmap.clusters.length > 0 ? (
          <LatencyHeatmapCard
            clusters={latencyHeatmap.clusters}
            buckets={latencyHeatmap.buckets}
            cells={latencyHeatmap.cells}
            onClusterClick={setDrillCluster}
          />
        ) : null}

        {/* Live request tail — most-recent-first table of the last
            buffered samples. Each row: timestamp, total rq/s, err/s,
            and a tiny per-bucket sparkline of total req. Hidden when
            <2 samples (same gate as the rate chart). */}
        {sparkDeltas.length >= 2 ? (
          <RequestTailCard deltas={sparkDeltas} />
        ) : null}

        {/* Pie charts — visual at-a-glance for the three questions
            operators ask first: "what response codes are we serving",
            "where is traffic going", "are upstreams healthy". Each
            falls back to a no-data caption rather than rendering an
            empty donut so the panel stays calm when Envoy hasn't seen
            traffic yet (e.g. immediately post-restart). */}
        {(responseCodeData.length > 0 ||
          clusterTrafficData.length > 0 ||
          clusterHealthData.length > 0) && (
          <div
            className="grid grid-cols-1 gap-3 md:grid-cols-3"
            data-testid="envoy-summary-pies"
          >
            <PieCard
              title="Response codes"
              description="Downstream response distribution."
              data={responseCodeData}
              testid="envoy-summary-pie-response"
            />
            <PieCard
              title="Cluster traffic"
              description="Top 5 clusters by request volume."
              data={clusterTrafficData}
              testid="envoy-summary-pie-traffic"
            />
            <PieCard
              title="Host health"
              description="Healthy vs unhealthy upstream hosts."
              data={clusterHealthData}
              testid="envoy-summary-pie-health"
            />
          </div>
        )}

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
                    <button
                      type="button"
                      className="font-mono text-xs text-fg hover:underline focus-visible:underline"
                      onClick={() => setDrillCluster(cluster)}
                      data-testid={`envoy-summary-top-route-${cluster}-drill`}
                      aria-label={`Drill into ${cluster}`}
                    >
                      {prettyCluster(cluster)}
                    </button>
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
                    <button
                      type="button"
                      className="font-mono text-xs text-fg hover:underline focus-visible:underline"
                      onClick={() => setDrillCluster(row.name)}
                      aria-label={`Drill into ${row.name}`}
                    >
                      {prettyCluster(row.name)}
                    </button>
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

        {/* Latency heatmap — full per-cluster p50/p95/p99 grid sorted
            by p99 desc. The "Slowest p99" section above is a top-5
            preview; this is the deep-triage view. Cell tone is driven
            by latency severity (success <100ms / warning <500ms /
            danger ≥500ms) so the bad actors stand out without reading
            the numbers. */}
        {latencyHeatRows.length > 0 && (
          <div
            className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
            data-testid="envoy-summary-latency-heatmap"
          >
            <div>
              <span className="text-sm font-medium text-fg">
                Cluster latency heatmap
              </span>
              <p className="text-xs text-fg-muted">
                p50 / p95 / p99 per upstream, sorted by tail. Cell tint
                shows severity: green &lt;100ms · amber &lt;500ms · red ≥500ms.
              </p>
            </div>
            <div
              className="grid items-center gap-x-2 gap-y-1 text-sm"
              style={{ gridTemplateColumns: "minmax(0,1fr) auto auto auto" }}
            >
              <div className="text-xs uppercase tracking-wide text-fg-faint">
                Cluster
              </div>
              <div className="text-center text-xs uppercase tracking-wide text-fg-faint">
                p50
              </div>
              <div className="text-center text-xs uppercase tracking-wide text-fg-faint">
                p95
              </div>
              <div className="text-center text-xs uppercase tracking-wide text-fg-faint">
                p99
              </div>
              {latencyHeatRows.map((r) => (
                <FragmentRow
                  key={r.name}
                  name={prettyCluster(r.name)}
                  p50={r.p50}
                  p95={r.p95}
                  p99={r.p99}
                />
              ))}
            </div>
          </div>
        )}

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
      <ClusterDetailDrawer
        open={drillCluster !== null}
        cluster={drillCluster}
        onClose={() => setDrillCluster(null)}
      />
    </Card>
  );
}

interface StatProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: "success" | "warning" | "danger" | "info";
  testid: string;
  /**
   * Optional sparkline series — at least 2 numeric samples renders a
   * compact trend line under the value. Below 2 samples we render a
   * placeholder gap so card heights stay aligned across the row.
   */
  spark?: readonly number[];
}

const TONE_COLOR: Record<StatProps["tone"], string> = {
  success: "var(--color-success)",
  warning: "var(--color-warning)",
  danger: "var(--color-danger)",
  info: "var(--color-info)",
};

function Stat({ icon, label, value, tone, testid, spark }: StatProps) {
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
      <Sparkline data={spark ?? []} color={TONE_COLOR[tone]} />
    </div>
  );
}

interface SparklineProps {
  data: readonly number[];
  color: string;
  height?: number;
}

/**
 * Inline-SVG sparkline. Lightweight on purpose — recharts is too
 * heavy for a 24px-tall trend line and its ResponsiveContainer
 * collides with the parent flex layout. The polyline normalises to a
 * 100×height viewBox so the line scales with the card width.
 *
 * <2 samples renders a fixed-height empty span so the Stat card heights
 * stay aligned even before the rolling buffer has filled.
 */
function Sparkline({ data, color, height = 24 }: SparklineProps) {
  if (data.length < 2) {
    // Render a faint dotted baseline so the trend's eventual home is
    // visible even before the rolling buffer fills (≥2 polls). Without
    // this the Stat card looks like the spark prop did nothing.
    return (
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height }}
        role="img"
        aria-label="trend pending"
      >
        <line
          x1="0"
          y1={height / 2}
          x2="100"
          y2={height / 2}
          stroke="var(--color-fg-faint)"
          strokeWidth={1}
          strokeDasharray="2 3"
          vectorEffect="non-scaling-stroke"
          opacity={0.5}
        />
      </svg>
    );
  }
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * 100;
      const y = ((max - v) / range) * (height - 2) + 1;
      return `${x},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height }}
      role="img"
      aria-label="trend"
    >
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.4}
        vectorEffect="non-scaling-stroke"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
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

interface RefreshSelectorProps {
  value: number;
  onChange: (intervalMs: number) => void;
}

/**
 * Compact Grafana-style refresh-rate selector. Rendered in the
 * CardHeader so the operator can drop into hot-poll mode (2-5s) when
 * watching a deploy and back off to background cadence (60s-5m) when
 * leaving the panel open as a sanity check. Uses a native <select>
 * for keyboard-accessibility without dragging in a combobox lib.
 */
function RefreshSelector({ value, onChange }: RefreshSelectorProps) {
  return (
    <label
      className="flex items-center gap-2 text-xs text-fg-muted"
      data-testid="envoy-summary-refresh-selector"
    >
      <span className="hidden sm:inline">Refresh</span>
      <select
        className={cn(
          "rounded-md border border-border bg-bg-1 px-2 py-1 text-xs text-fg",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
        value={value}
        onChange={(e) => onChange(Number(e.currentTarget.value))}
        aria-label="Refresh interval"
      >
        {REFRESH_OPTIONS.map((opt) => (
          <option key={opt.intervalMs} value={opt.intervalMs}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}

interface FragmentRowProps {
  name: string;
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

/**
 * Heatmap row — name + three colour-tinted latency cells. Returns a
 * fragment so the parent grid layouts can place the four cells onto
 * the same logical row via auto-flow.
 */
function FragmentRow({ name, p50, p95, p99 }: FragmentRowProps) {
  return (
    <>
      <div
        className="font-mono text-xs text-fg truncate"
        title={name}
        data-testid={`envoy-summary-heat-${name}`}
      >
        {name}
      </div>
      <HeatCell ms={p50} />
      <HeatCell ms={p95} />
      <HeatCell ms={p99} />
    </>
  );
}

interface HeatCellProps {
  ms: number | null;
}

function HeatCell({ ms }: HeatCellProps) {
  if (ms === null || ms === undefined) {
    return (
      <div className="rounded px-2 py-0.5 text-center text-xs tabular-nums text-fg-faint">
        —
      </div>
    );
  }
  const tone =
    ms < 100 ? "success" : ms < 500 ? "warning" : "danger";
  const toneClass = {
    success: "border-success/40 bg-success/10 text-success",
    warning: "border-warning/40 bg-warning/10 text-warning",
    danger: "border-danger/40 bg-danger/10 text-danger",
  }[tone];
  return (
    <div
      className={cn(
        "rounded border px-2 py-0.5 text-center text-xs font-medium tabular-nums",
        toneClass,
      )}
      data-tone={tone}
    >
      {ms}ms
    </div>
  );
}

// ---- Phase E ---------------------------------------------------------------

const CLUSTER_PALETTE = [
  "var(--color-info)",
  "var(--color-success)",
  "var(--color-warning)",
  "var(--color-accent)",
  "var(--color-danger)",
  "var(--color-fg-muted)",
];

interface PerClusterTrafficCardProps {
  rows: Record<string, number | string>[];
  topClusters: string[];
  loading: boolean;
  onClusterClick?: (cluster: string) => void;
}

/**
 * Multi-line chart of per-cluster request rate over the rolling
 * window. Top-5 clusters by mean rate get their own line; the rest
 * collapse into an "other" series so a deploy with 50 services
 * doesn't render 50 illegible lines. Empty-state caption when the
 * buffer has <2 deltas — same UX gate as the aggregate rate chart
 * above so the panel feels uniform during the first 60s after a
 * pod restart.
 */
function PerClusterTrafficCard({
  rows,
  topClusters,
  loading,
  onClusterClick,
}: PerClusterTrafficCardProps) {
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
      data-testid="envoy-summary-per-cluster-traffic"
    >
      <div>
        <span className="text-sm font-medium text-fg">
          Per-cluster traffic (live)
        </span>
        <p className="text-xs text-fg-muted">
          Requests/sec for the top 5 upstream clusters. Anything below
          the top 5 collapses into an "other" line.{" "}
          {rows.length >= 2
            ? `${rows.length} buckets buffered.`
            : null}{" "}
          {onClusterClick
            ? "Click a cluster name in the legend to drill in."
            : null}
        </p>
      </div>
      {onClusterClick && rows.length >= 2 ? (
        <div
          className="flex flex-wrap gap-1.5 pb-1"
          data-testid="envoy-summary-per-cluster-legend"
        >
          {topClusters.map((cluster, i) => (
            <button
              key={cluster}
              type="button"
              onClick={() => onClusterClick(cluster)}
              className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] hover:bg-bg-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              data-testid={`envoy-summary-per-cluster-legend-${cluster}`}
              aria-label={`Drill into ${cluster}`}
            >
              <span
                className="inline-block size-2 rounded-sm"
                style={{
                  background: CLUSTER_PALETTE[i % CLUSTER_PALETTE.length],
                }}
                aria-hidden
              />
              <span className="font-mono text-fg-muted">
                {prettyCluster(cluster)}
              </span>
            </button>
          ))}
        </div>
      ) : null}
      {rows.length < 2 ? (
        <div
          className="flex h-44 w-full items-center justify-center rounded border border-dashed border-border/60 bg-bg-1/40 text-xs text-fg-muted"
          data-testid="envoy-summary-per-cluster-empty"
        >
          {loading
            ? "Loading…"
            : "Per-cluster series appears once two polls have completed."}
        </div>
      ) : (
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={[...rows]}
              margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
            >
              <CartesianGrid
                strokeDasharray="2 4"
                stroke="var(--color-border)"
                vertical={false}
              />
              <XAxis
                dataKey="ts"
                tickFormatter={(t) =>
                  new Date(Number(t) * 1000).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                }
                tick={{ fill: "var(--color-fg-muted)", fontSize: 10 }}
                axisLine={{ stroke: "var(--color-border)" }}
                tickLine={false}
                minTickGap={24}
              />
              <YAxis
                tick={{ fill: "var(--color-fg-muted)", fontSize: 10 }}
                axisLine={{ stroke: "var(--color-border)" }}
                tickLine={false}
                width={32}
              />
              <ChartTooltip
                contentStyle={{
                  background: "var(--color-bg-2)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  fontSize: 12,
                  color: "var(--color-fg)",
                }}
                labelFormatter={(t) =>
                  new Date(Number(t) * 1000).toLocaleTimeString()
                }
              />
              <Legend
                verticalAlign="bottom"
                height={24}
                iconSize={8}
                wrapperStyle={{ fontSize: 11, color: "var(--color-fg-muted)" }}
              />
              {topClusters.map((cluster, i) => (
                <Line
                  key={cluster}
                  type="monotone"
                  dataKey={cluster}
                  name={prettyCluster(cluster)}
                  stroke={CLUSTER_PALETTE[i % CLUSTER_PALETTE.length]}
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
              <Line
                type="monotone"
                dataKey="other"
                name="other"
                stroke="var(--color-fg-faint)"
                strokeDasharray="3 3"
                strokeWidth={1.2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

interface ActiveConnectionsCardProps {
  data: readonly { name: string; value: number; raw: string }[];
  onClusterClick?: (cluster: string) => void;
}

/**
 * Horizontal bar list of clusters with active connections. Used for
 * the "who's holding open WebSockets / streaming sessions right
 * now?" question. Sized as a max-bar-width fraction of the card.
 */
function ActiveConnectionsCard({ data, onClusterClick }: ActiveConnectionsCardProps) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
      data-testid="envoy-summary-active-cx-breakdown"
    >
      <div>
        <span className="text-sm font-medium text-fg">
          Active connections by cluster
        </span>
        <p className="text-xs text-fg-muted">
          Currently-open upstream connections per cluster. Streaming
          services + WebSocket consumers usually dominate.
        </p>
      </div>
      <ul className="flex flex-col gap-1">
        {data.map((d) => (
          <li
            key={d.raw}
            className="flex items-center gap-2 text-xs"
            data-testid={`envoy-summary-active-row-${d.name}`}
          >
            {onClusterClick ? (
              <button
                type="button"
                className="w-32 truncate text-left font-mono text-fg hover:underline focus-visible:underline focus-visible:outline-none"
                onClick={() => onClusterClick(d.raw)}
                aria-label={`Drill into ${d.raw}`}
              >
                {d.name}
              </button>
            ) : (
              <span className="w-32 truncate font-mono text-fg">{d.name}</span>
            )}
            <div className="relative h-4 flex-1 overflow-hidden rounded bg-bg-2">
              <div
                className="absolute inset-y-0 left-0 rounded bg-info/60"
                style={{ width: `${(d.value / max) * 100}%` }}
                aria-hidden
              />
            </div>
            <span className="w-10 text-right tabular-nums text-fg-muted">
              {d.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface LatencyHeatmapCardProps {
  clusters: readonly string[];
  buckets: readonly number[];
  cells: Record<
    string,
    Record<number, { p50: number | null; p95: number | null; p99: number | null }>
  >;
  onClusterClick?: (cluster: string) => void;
}

/**
 * Cluster × time grid of p99 latency. Each cell is tinted by
 * severity (green <100ms · amber <500ms · red ≥500ms · faint when
 * the cluster had no histogram in that bucket). Top-6 clusters by
 * worst p99 surface; anything quieter is hidden.
 *
 * Reading the grid: rows = upstream clusters, columns = sample
 * buckets (left = oldest, right = newest). A horizontal red streak
 * means a cluster has been slow throughout; a vertical streak across
 * multiple clusters means everything-spiked-at-once (likely a
 * cluster-wide event, not an individual upstream issue).
 */
function LatencyHeatmapCard({
  clusters,
  buckets,
  cells,
  onClusterClick,
}: LatencyHeatmapCardProps) {
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
      data-testid="envoy-summary-latency-over-time"
    >
      <div>
        <span className="text-sm font-medium text-fg">
          Latency over time (p99)
        </span>
        <p className="text-xs text-fg-muted">
          Top {clusters.length} clusters by worst tail. Cell tint =
          green &lt;100ms · amber &lt;500ms · red ≥500ms · faint when no
          histogram data for that bucket.
        </p>
      </div>
      <div
        className="grid items-center gap-x-2 gap-y-1 text-xs"
        style={{
          gridTemplateColumns: `minmax(120px, max-content) repeat(${buckets.length}, minmax(8px, 1fr))`,
        }}
        data-testid="envoy-summary-latency-grid"
      >
        {/* Header row — empty corner + bucket times (every 4th
            label so a 240-bucket window doesn't overflow). */}
        <div aria-hidden />
        {buckets.map((b, i) => (
          <div
            key={b}
            className="text-center text-[9px] tabular-nums text-fg-faint"
          >
            {i % 4 === 0
              ? new Date(b * 1000).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : ""}
          </div>
        ))}
        {clusters.map((cluster) => (
          <Fragment key={cluster}>
            {onClusterClick ? (
              <button
                type="button"
                className="truncate text-left font-mono text-fg hover:underline focus-visible:underline"
                title={cluster}
                onClick={() => onClusterClick(cluster)}
                data-testid={`envoy-summary-latency-row-${cluster}`}
              >
                {prettyCluster(cluster)}
              </button>
            ) : (
              <div
                className="truncate font-mono text-fg"
                title={cluster}
                data-testid={`envoy-summary-latency-row-${cluster}`}
              >
                {prettyCluster(cluster)}
              </div>
            )}
            {buckets.map((b) => {
              const q = cells[cluster]?.[b];
              const p99 = q?.p99 ?? null;
              const tone = latencyTone(p99);
              return (
                <Tooltip key={b}>
                  <TooltipTrigger asChild>
                    <div
                      className={cn(
                        "h-4 rounded-sm border",
                        latencyToneClass(tone),
                      )}
                      data-tone={tone}
                      aria-label={
                        p99 !== null
                          ? `${cluster} p99 ${p99}ms at ${new Date(
                              b * 1000,
                            ).toLocaleTimeString()}`
                          : `${cluster} no data`
                      }
                    />
                  </TooltipTrigger>
                  <TooltipContent>
                    {q ? (
                      <div className="text-xs">
                        <div className="font-mono">{cluster}</div>
                        <div className="tabular-nums text-fg-muted">
                          p50 {q.p50 ?? "—"}ms · p95 {q.p95 ?? "—"}ms · p99{" "}
                          {q.p99 ?? "—"}ms
                        </div>
                        <div className="text-fg-faint">
                          {new Date(b * 1000).toLocaleTimeString()}
                        </div>
                      </div>
                    ) : (
                      <div className="text-xs text-fg-muted">No data</div>
                    )}
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function latencyTone(p99: number | null): "muted" | "success" | "warning" | "danger" {
  if (p99 === null || p99 === undefined) return "muted";
  if (p99 < 100) return "success";
  if (p99 < 500) return "warning";
  return "danger";
}

function latencyToneClass(tone: "muted" | "success" | "warning" | "danger"): string {
  switch (tone) {
    case "success":
      return "border-success/40 bg-success/30";
    case "warning":
      return "border-warning/40 bg-warning/30";
    case "danger":
      return "border-danger/40 bg-danger/40";
    case "muted":
      return "border-border/40 bg-bg-2/40";
  }
}

interface RequestTailCardProps {
  deltas: readonly TimeseriesDelta[];
}

/**
 * Most-recent-first list of the last buffered samples. Each row is
 * a timestamp, the aggregate request rate, error rate, and a small
 * sparkline showing recent request-rate trend up to that bucket.
 * Useful for "what just happened" troubleshooting — operators can
 * see the spike that caused them to open the panel.
 *
 * Capped at 20 rows for visual hygiene; the full buffer is still
 * driving the line/area charts above.
 */
function RequestTailCard({ deltas }: RequestTailCardProps) {
  const recent = useMemo(
    () => [...deltas].reverse().slice(0, 20),
    [deltas],
  );
  const maxRq = useMemo(
    () => Math.max(1, ...deltas.map((d) => d.rq_per_s)),
    [deltas],
  );

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
      data-testid="envoy-summary-request-tail"
    >
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-fg">
          Recent buckets
        </span>
        <span
          className="inline-block size-1.5 animate-pulse rounded-full bg-success"
          aria-hidden
        />
        <span className="text-xs text-fg-muted">live</span>
      </div>
      <p className="text-xs text-fg-muted">
        Last 20 polling buckets, newest first. Each row is an aggregate
        rate snapshot — use the per-cluster traffic legend above to
        drill into a specific upstream and see which one owns the
        spike.
      </p>
      <ul
        className="flex flex-col divide-y divide-border/50"
        data-testid="envoy-summary-request-tail-list"
      >
        {recent.map((d, idx) => {
          const ratePct = (d.rq_per_s / maxRq) * 100;
          const errPct =
            d.rq_per_s > 0
              ? Math.min(100, (d.err_per_s / d.rq_per_s) * 100)
              : 0;
          return (
            <li
              key={d.ts}
              className={cn(
                "flex items-center gap-3 py-1 text-xs",
                idx === 0 && "bg-success/5",
              )}
              data-testid={`envoy-summary-tail-row-${idx}`}
            >
              <span className="w-16 tabular-nums text-fg-muted">
                {new Date(d.ts * 1000).toLocaleTimeString()}
              </span>
              <div className="relative h-2 flex-1 overflow-hidden rounded bg-bg-2">
                <div
                  className="absolute inset-y-0 left-0 bg-info/60"
                  style={{ width: `${ratePct}%` }}
                  aria-hidden
                />
                {errPct > 0 ? (
                  <div
                    className="absolute inset-y-0 right-0 bg-danger/70"
                    style={{ width: `${(errPct * ratePct) / 100}%` }}
                    aria-hidden
                  />
                ) : null}
              </div>
              <span className="w-16 tabular-nums text-fg">
                {d.rq_per_s.toFixed(1)} rq/s
              </span>
              <span
                className={cn(
                  "w-14 tabular-nums",
                  d.err_per_s > 0 ? "text-danger" : "text-fg-faint",
                )}
              >
                {d.err_per_s.toFixed(1)} err/s
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

interface PieCardProps {
  title: string;
  description: string;
  data: readonly PieDatum[];
  testid: string;
}

/**
 * Donut chart wrapper for the three operator-facing rollups (response
 * codes, cluster traffic share, host health). We use ResponsiveContainer
 * so the SVG fills the grid cell and recalculates on viewport change;
 * the `h-44` floor keeps the legend from collapsing the chart on
 * narrow widths.
 *
 * No-data falls through to a muted caption so the panel doesn't render
 * a degenerate single-slice donut on a fresh Envoy boot.
 */
function PieCard({ title, description, data, testid }: PieCardProps) {
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/30 p-3"
      data-testid={testid}
    >
      <div>
        <span className="text-sm font-medium text-fg">{title}</span>
        <p className="text-xs text-fg-muted">{description}</p>
      </div>
      {data.length === 0 ? (
        <span className="text-sm text-fg-muted">No data yet.</span>
      ) : (
        <div className="h-44 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={[...data]}
                dataKey="value"
                nameKey="name"
                innerRadius={32}
                outerRadius={56}
                paddingAngle={2}
                strokeWidth={1}
                stroke="var(--color-bg-1)"
              >
                {data.map((d) => (
                  <Cell key={d.name} fill={d.color} />
                ))}
              </Pie>
              <ChartTooltip
                contentStyle={{
                  background: "var(--color-bg-2)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  fontSize: 12,
                  color: "var(--color-fg)",
                }}
                itemStyle={{ color: "var(--color-fg)" }}
              />
              <Legend
                verticalAlign="bottom"
                height={24}
                iconSize={8}
                wrapperStyle={{ fontSize: 11, color: "var(--color-fg-muted)" }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
