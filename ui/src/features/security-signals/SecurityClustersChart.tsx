import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ShieldAlert } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useFailedLogins,
  type FailedLoginCluster,
} from "./hooks";

/**
 * Top-N failed-login clusters as a bar chart — taller bars indicate
 * a more aggressive cluster (typically credential-stuffing from a
 * single CIDR). Caps at 10 to stay readable on a phone-width view.
 *
 * Empty-state pattern: card always renders. When the buffer is
 * unloaded or no clusters are firing, the body shows a friendly
 * caption rather than hiding (consistent with the empty-state
 * visibility rule).
 */
export function SecurityClustersChart() {
  const query = useFailedLogins();
  const data = useMemo(
    () => topClusters(query.data?.clusters),
    [query.data],
  );

  return (
    <Card data-testid="security-clusters-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldAlert aria-hidden className="size-4" />
          Top failed-login clusters
        </CardTitle>
        <CardDescription>
          The 10 most-active failed-login clusters in the rolling
          buffer. A cluster is a CIDR /24 (or username) with a
          coordinated burst of failures.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-44 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="security-clusters-chart-error"
          >
            Couldn't load failed-login clusters:{" "}
            {(query.error as Error).message}
          </p>
        ) : data.length === 0 ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="security-clusters-chart-empty"
          >
            No failed-login clusters in the rolling window. Spikes
            will populate this chart as the security tracker
            classifies them.
          </p>
        ) : (
          <div
            className="h-44 w-full"
            data-testid="security-clusters-chart-area"
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} layout="vertical">
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 10 }}
                  allowDecimals={false}
                />
                <YAxis
                  type="category"
                  dataKey="label"
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 10 }}
                  width={120}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--bg-2)",
                    border: "1px solid var(--border)",
                    fontSize: 12,
                  }}
                />
                <Bar
                  dataKey="attempts"
                  fill="#f87171"
                  radius={[0, 2, 2, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface ChartRow {
  label: string;
  attempts: number;
}

function topClusters(
  clusters: readonly FailedLoginCluster[] | undefined,
): ChartRow[] {
  if (!Array.isArray(clusters) || clusters.length === 0) return [];
  return clusters
    .map((c) => {
      const label =
        (typeof c.ip_prefix === "string" && c.ip_prefix) ||
        (typeof c.username === "string" && c.username) ||
        "unknown";
      const attempts =
        typeof c.attempt_count === "number" && Number.isFinite(c.attempt_count)
          ? c.attempt_count
          : 0;
      return { label, attempts };
    })
    .filter((r) => r.attempts > 0)
    .sort((a, b) => b.attempts - a.attempts)
    .slice(0, 10);
}
