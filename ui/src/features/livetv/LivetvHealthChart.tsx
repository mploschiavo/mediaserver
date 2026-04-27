import { useMemo } from "react";
import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { Tv } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useEpgHealth } from "./hooks";

/**
 * Donut showing pass / fail / stale split across the configured EPG
 * probes. Mirrors the design doc §1 mock — a glanceable health
 * indicator that complements the row table on the same page.
 *
 * Pulls from the existing ``GET /api/epg/health`` payload (no new
 * endpoint).
 */
export function LivetvHealthChart() {
  const query = useEpgHealth();
  const data = useMemo(() => bucketHealth(query.data), [query.data]);
  const total = data.reduce((acc, x) => acc + x.count, 0);

  return (
    <Card data-testid="livetv-health-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Tv aria-hidden className="size-4" />
          Guide-source health
        </CardTitle>
        <CardDescription>
          Pass / fail / stale split across configured EPG probes —
          drill into any failing tile from the providers table below.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-44 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="livetv-health-chart-error"
          >
            Couldn't load EPG health:{" "}
            {(query.error as Error).message}
          </p>
        ) : total === 0 ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="livetv-health-chart-empty"
          >
            No guide sources configured. Add one from the providers
            list below — its probe will populate this chart.
          </p>
        ) : (
          <div
            className="flex h-44 w-full items-center"
            data-testid="livetv-health-chart-area"
          >
            <div className="flex-1">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={data}
                    dataKey="count"
                    nameKey="status"
                    cx="50%"
                    cy="50%"
                    innerRadius={32}
                    outerRadius={64}
                    paddingAngle={2}
                  >
                    {data.map((row, i) => (
                      <Cell
                        key={i}
                        fill={STATUS_COLORS[row.status] ?? "#94a3b8"}
                      />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: "var(--bg-2)",
                      border: "1px solid var(--border)",
                      fontSize: 12,
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <ul className="flex flex-col gap-1.5 pr-2 text-xs">
              {data.map((row) => (
                <li
                  key={row.status}
                  className="flex items-center gap-2 tabular-nums"
                >
                  <span
                    aria-hidden
                    className="inline-block size-2 rounded-full"
                    style={{
                      background: STATUS_COLORS[row.status] ?? "#94a3b8",
                    }}
                  />
                  <span className="capitalize text-fg">{row.status}</span>
                  <span className="text-fg-muted">{row.count}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const STATUS_COLORS: Record<string, string> = {
  pass: "#4ade80",
  fail: "#f87171",
  stale: "#facc15",
  unknown: "#94a3b8",
};

interface ProbeShape {
  status?: string;
  ok?: boolean;
}

function bucketHealth(
  raw: unknown,
): { status: string; count: number }[] {
  if (!raw || typeof raw !== "object") return [];
  const probes =
    (raw as { probes?: ProbeShape[] }).probes ??
    (raw as { sources?: ProbeShape[] }).sources ??
    [];
  const counts = new Map<string, number>();
  for (const probe of probes) {
    if (!probe || typeof probe !== "object") continue;
    let status: string;
    if (typeof probe.status === "string" && probe.status) {
      status = probe.status;
    } else if (typeof probe.ok === "boolean") {
      status = probe.ok ? "pass" : "fail";
    } else {
      status = "unknown";
    }
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  if (counts.size === 0) return [];
  return Array.from(counts, ([status, count]) => ({ status, count }));
}
