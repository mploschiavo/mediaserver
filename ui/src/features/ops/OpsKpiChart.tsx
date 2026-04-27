import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { Activity, Database, HardDrive } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOpsHealth } from "@/api/hooks";

/**
 * KPI strip for the /ops surface — gauge-style donuts that
 * surface the three ``GET /api/ops/health`` numbers (uptime,
 * containers running, disk used %) at a glance, before the
 * operator scrolls into the per-card detail underneath. Pure
 * read of the existing endpoint — no new server work.
 *
 * Renders a placeholder donut at 0 % when the buffer is unloaded,
 * per the empty-state-visibility feedback (never blank).
 */
export function OpsKpiChart() {
  const query = useOpsHealth();
  const data = query.data;

  return (
    <Card data-testid="ops-kpi-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity aria-hidden className="size-4" />
          Stack health
        </CardTitle>
        <CardDescription>
          Live snapshot from <code>/api/ops/health</code>: uptime,
          containers running, disk used. Refreshes every 30s.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-44 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="ops-kpi-chart-error"
          >
            Couldn't load ops health:{" "}
            {(query.error as Error).message}
          </p>
        ) : (
          <div
            className="grid gap-4 md:grid-cols-3"
            data-testid="ops-kpi-chart-area"
          >
            <Gauge
              icon={Activity}
              label="Uptime"
              value={uptimePercent(data?.uptime_seconds)}
              caption={uptimeCaption(data?.uptime_seconds)}
              tone="info"
            />
            <Gauge
              icon={Database}
              label="Containers"
              value={containerPercent(
                data?.containers,
                data?.containers_total,
              )}
              caption={containerCaption(
                data?.containers,
                data?.containers_total,
              )}
              tone={containerTone(
                data?.containers,
                data?.containers_total,
              )}
            />
            <Gauge
              icon={HardDrive}
              label="Disk used"
              value={Number(data?.disk_used_pct ?? 0)}
              caption={`${(data?.disk_used_pct ?? 0).toFixed(1)} %`}
              tone={diskTone(data?.disk_used_pct)}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Gauge({
  icon: Icon,
  label,
  value,
  caption,
  tone,
}: {
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  label: string;
  value: number;
  caption: string;
  tone: "success" | "warning" | "danger" | "info";
}) {
  const clipped = Math.max(0, Math.min(100, value));
  const segments = [
    { name: "filled", value: clipped },
    { name: "rest", value: 100 - clipped },
  ];
  const color = TONE[tone];
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-fg-faint">
        <Icon aria-hidden className="size-3" />
        {label}
      </div>
      <div className="h-24 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={segments}
              dataKey="value"
              startAngle={90}
              endAngle={-270}
              cx="50%"
              cy="50%"
              innerRadius="65%"
              outerRadius="90%"
              stroke="none"
            >
              <Cell fill={color} />
              <Cell fill="var(--bg-3)" />
            </Pie>
            <Tooltip
              contentStyle={{
                background: "var(--bg-2)",
                border: "1px solid var(--border)",
                fontSize: 12,
              }}
              formatter={(value) => {
                const v = typeof value === "number" ? value : Number(value ?? 0);
                return `${v.toFixed(0)}%`;
              }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <span className="text-sm font-medium text-fg" data-testid={`ops-gauge-${label.toLowerCase()}`}>
        {caption}
      </span>
    </div>
  );
}

const TONE: Record<"success" | "warning" | "danger" | "info", string> = {
  success: "#4ade80",
  warning: "#facc15",
  danger: "#f87171",
  info: "#60a5fa",
};

function diskTone(pct: number | undefined): "success" | "warning" | "danger" {
  const v = Number(pct ?? 0);
  if (v >= 90) return "danger";
  if (v >= 70) return "warning";
  return "success";
}

function containerPercent(
  running: number | undefined,
  total: number | undefined,
): number {
  const r = Number(running ?? 0);
  const t = Number(total ?? 0);
  // Total of 0 means the controller couldn't enumerate (older
  // backend, or platform-specific failure). Treat as 100 % so the
  // gauge doesn't lie when only the running count is known.
  if (t <= 0) return r > 0 ? 100 : 0;
  return Math.max(0, Math.min(100, (r / t) * 100));
}

function containerCaption(
  running: number | undefined,
  total: number | undefined,
): string {
  const r = Number(running ?? 0);
  const t = Number(total ?? 0);
  if (t <= 0 || t === r) return `${r} running`;
  return `${r} / ${t} running`;
}

function containerTone(
  running: number | undefined,
  total: number | undefined,
): "success" | "warning" | "danger" {
  const r = Number(running ?? 0);
  const t = Number(total ?? 0);
  if (t <= 0) return "success";
  const pct = (r / t) * 100;
  if (pct < 60) return "danger";
  if (pct < 90) return "warning";
  return "success";
}

function uptimePercent(seconds: number | undefined): number {
  // Anchor uptime against a 24h "fresh boot" reference so the donut
  // saturates at full ring after a day. Anything beyond renders 100 %.
  const s = Number(seconds ?? 0);
  if (s <= 0) return 0;
  return Math.min(100, (s / 86400) * 100);
}

function uptimeCaption(seconds: number | undefined): string {
  const s = Number(seconds ?? 0);
  if (s <= 0) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}
