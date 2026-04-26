import { useMemo } from "react";
import { Drawer as VaulDrawer } from "vaul";
import {
  Activity,
  AlertTriangle,
  Gauge,
  Network,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Badge } from "@/components/ui/badge";
import { useEnvoyAdminSummary } from "./useEnvoyAdminSummary";
import { fetcher } from "@/api/client";
import { useQuery } from "@tanstack/react-query";

interface TimeseriesSample {
  ts: number;
  rq_total: number;
  rq_per_cluster?: Record<string, number>;
  active_per_cluster?: Record<string, number>;
  latency_per_cluster?: Record<
    string,
    { p50: number | null; p95: number | null; p99: number | null }
  >;
}

interface TimeseriesDelta {
  ts: number;
  rq_per_cluster_per_s?: Record<string, number>;
}

interface EnvoyTimeseries {
  samples: readonly TimeseriesSample[];
  deltas: readonly TimeseriesDelta[];
}

interface ClusterDetailDrawerProps {
  open: boolean;
  cluster: string | null;
  onClose: () => void;
}

/**
 * Click any cluster row in the per-cluster traffic chart, the
 * latency heatmap, the active-connections list, or the host edit
 * surfaces → opens this drawer with the cluster's full per-bucket
 * series from the rolling buffer.
 *
 * Three sections, top to bottom:
 *   1. Header KPIs (current p99, current rq/s, current active cx,
 *      hosts/healthy)
 *   2. Request rate over time (single-line chart)
 *   3. Latency over time (p50/p95/p99 lines)
 *
 * No backend changes required — the rolling buffer already captures
 * every per-cluster dimension. The drawer just slices the buffer to
 * one cluster and re-renders the existing chart primitives.
 */
export function ClusterDetailDrawer({
  open,
  cluster,
  onClose,
}: ClusterDetailDrawerProps) {
  const summary = useEnvoyAdminSummary();
  const ts = useQuery<EnvoyTimeseries>({
    queryKey: ["routing", "envoy", "timeseries"],
    queryFn: () =>
      fetcher<EnvoyTimeseries>("api/envoy/timeseries?window=1800"),
    enabled: open,
    staleTime: 15_000,
  });

  // Per-bucket request rate for this cluster.
  const rateRows = useMemo(() => {
    if (!cluster || !ts.data) return [];
    return ts.data.deltas
      .filter((d) => d.rq_per_cluster_per_s?.[cluster] !== undefined)
      .map((d) => ({
        ts: d.ts,
        rq_per_s: Number(
          (d.rq_per_cluster_per_s?.[cluster] ?? 0).toFixed(3),
        ),
      }));
  }, [cluster, ts.data]);

  // Per-bucket latency for this cluster.
  const latencyRows = useMemo(() => {
    if (!cluster || !ts.data) return [];
    return ts.data.samples
      .filter(
        (s) =>
          s.latency_per_cluster?.[cluster] &&
          (s.latency_per_cluster?.[cluster]?.p99 ?? null) !== null,
      )
      .map((s) => {
        const q = s.latency_per_cluster?.[cluster];
        return {
          ts: s.ts,
          p50: q?.p50 ?? null,
          p95: q?.p95 ?? null,
          p99: q?.p99 ?? null,
        };
      });
  }, [cluster, ts.data]);

  const currentSample = ts.data?.samples.at(-1);
  const currentRq = cluster
    ? currentSample?.rq_per_cluster?.[cluster] ?? 0
    : 0;
  const currentActive = cluster
    ? currentSample?.active_per_cluster?.[cluster] ?? 0
    : 0;
  const currentP99 = cluster
    ? currentSample?.latency_per_cluster?.[cluster]?.p99 ?? null
    : null;

  // Cluster member health from the admin summary.
  const clusterRow = useMemo(() => {
    if (!cluster || !summary.data) return null;
    return summary.data.clusters.find((c) => c.name === cluster) ?? null;
  }, [cluster, summary.data]);

  return (
    <VaulDrawer.Root
      direction="right"
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <VaulDrawer.Portal>
        <VaulDrawer.Overlay className="fixed inset-0 z-50 bg-[color-mix(in_oklab,var(--color-bg)_70%,transparent)] backdrop-blur-sm" />
        <VaulDrawer.Content
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l border-border bg-bg-1 outline-none"
          data-testid="cluster-detail-drawer"
        >
          <header className="flex items-start justify-between gap-3 border-b border-border p-4">
            <div className="flex flex-col gap-1">
              <VaulDrawer.Title className="font-mono text-base font-semibold leading-none tracking-tight">
                {cluster ?? "—"}
              </VaulDrawer.Title>
              <VaulDrawer.Description className="text-xs text-fg-muted">
                Per-bucket history from the rolling buffer.{" "}
                {ts.data ? `${ts.data.samples.length} samples buffered.` : ""}
              </VaulDrawer.Description>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-sm p-1 text-fg-muted [@media(hover:hover)]:hover:text-fg"
              aria-label="Close drawer"
              data-testid="cluster-detail-drawer-close"
            >
              <X className="size-4" aria-hidden />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto p-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Kpi
                icon={<Gauge className="size-3.5" />}
                label="p99 latency"
                value={
                  currentP99 !== null ? `${currentP99}ms` : "—"
                }
                tone={
                  currentP99 === null
                    ? "muted"
                    : currentP99 < 100
                      ? "success"
                      : currentP99 < 500
                        ? "warning"
                        : "danger"
                }
              />
              <Kpi
                icon={<Activity className="size-3.5" />}
                label="Active cx"
                value={currentActive.toLocaleString()}
                tone="info"
              />
              <Kpi
                icon={<Network className="size-3.5" />}
                label="Healthy hosts"
                value={
                  clusterRow
                    ? `${clusterRow.healthy}/${clusterRow.hosts}`
                    : "—"
                }
                tone={
                  !clusterRow
                    ? "muted"
                    : clusterRow.healthy === clusterRow.hosts
                      ? "success"
                      : "warning"
                }
              />
              <Kpi
                label="Cumulative req"
                value={currentRq.toLocaleString()}
                tone="muted"
              />
            </div>

            <Section title="Request rate over time">
              {rateRows.length < 2 ? (
                <EmptyChart>
                  Need ≥2 buckets — the chart appears once the
                  rolling buffer fills.
                </EmptyChart>
              ) : (
                <div className="h-44 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart
                      data={[...rateRows]}
                      margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
                    >
                      <defs>
                        <linearGradient id="cdrRateFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--color-info)" stopOpacity={0.4} />
                          <stop offset="100%" stopColor="var(--color-info)" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="2 4" stroke="var(--color-border)" vertical={false} />
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
                      <Area
                        type="monotone"
                        dataKey="rq_per_s"
                        name="rq/s"
                        stroke="var(--color-info)"
                        fill="url(#cdrRateFill)"
                        strokeWidth={1.5}
                        isAnimationActive={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Section>

            <Section title="Latency over time (p50 / p95 / p99)">
              {latencyRows.length < 2 ? (
                <EmptyChart>
                  No histogram data yet for this cluster.
                </EmptyChart>
              ) : (
                <div className="h-44 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={[...latencyRows]}
                      margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
                    >
                      <CartesianGrid strokeDasharray="2 4" stroke="var(--color-border)" vertical={false} />
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
                        width={36}
                        label={{
                          value: "ms",
                          position: "insideLeft",
                          fill: "var(--color-fg-faint)",
                          fontSize: 10,
                        }}
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
                      <Line type="monotone" dataKey="p50" stroke="var(--color-success)" strokeWidth={1.4} dot={false} isAnimationActive={false} />
                      <Line type="monotone" dataKey="p95" stroke="var(--color-warning)" strokeWidth={1.4} dot={false} isAnimationActive={false} />
                      <Line type="monotone" dataKey="p99" stroke="var(--color-danger)" strokeWidth={1.6} dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Section>

            {clusterRow && clusterRow.healthy < clusterRow.hosts ? (
              <div
                className="mt-4 flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-2 text-xs text-fg"
                role="alert"
                data-testid="cluster-detail-unhealthy-warning"
              >
                <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  {clusterRow.hosts - clusterRow.healthy} of {clusterRow.hosts}
                  {" "}upstream host(s) are unhealthy. Check the service's
                  pod readiness and the controller's reachability matrix.
                </span>
              </div>
            ) : null}
          </div>
        </VaulDrawer.Content>
      </VaulDrawer.Portal>
    </VaulDrawer.Root>
  );
}

function Kpi({
  icon,
  label,
  value,
  tone,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string;
  tone: "success" | "warning" | "danger" | "info" | "muted";
}) {
  const toneClass = {
    success: "border-success/40 bg-success/10 text-success",
    warning: "border-warning/40 bg-warning/10 text-warning",
    danger: "border-danger/40 bg-danger/10 text-danger",
    info: "border-info/40 bg-info/10 text-info",
    muted: "border-border bg-bg-2/40 text-fg-muted",
  }[tone];
  return (
    <div className={`flex flex-col gap-1 rounded-md border bg-bg-1/40 p-2 ${toneClass}`}>
      <div className="flex items-center gap-1 text-xs uppercase tracking-wide text-fg-muted">
        {icon}
        <span>{label}</span>
      </div>
      <Badge variant="outline" data-tone={tone} className="self-start text-sm tabular-nums">
        {value}
      </Badge>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 flex flex-col gap-2">
      <h3 className="text-sm font-medium text-fg">{title}</h3>
      {children}
    </div>
  );
}

function EmptyChart({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-32 w-full items-center justify-center rounded border border-dashed border-border/60 bg-bg-1/40 text-xs text-fg-muted">
      {children}
    </div>
  );
}
