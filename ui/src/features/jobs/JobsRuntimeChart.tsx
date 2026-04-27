import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useJobs } from "./hooks";

/**
 * Per-batch runtime chart over the rolling job history. Each bar is
 * one batch, color-coded by outcome (green = all ok, amber = some
 * skipped, red = errors). X axis labels are batch start times in
 * the user's locale; Y axis is wall-clock elapsed seconds. The
 * existing JobHistoryPanel renders the same data as a row table —
 * this chart is the visual companion that surfaces "did the last
 * 5 batches get slower?" at a glance, which is exactly the
 * question a row table buries.
 */
export function JobsRuntimeChart() {
  const query = useJobs();
  const data = useMemo(() => buildSeries(query.data?.history), [query.data]);

  return (
    <Card data-testid="jobs-runtime-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity aria-hidden className="size-4" />
          Recent batch runtimes
        </CardTitle>
        <CardDescription>
          Wall-clock seconds per recent batch, colour-coded by outcome.
          Use it to spot creeping latency before a batch crosses its
          timeout.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-48 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="jobs-runtime-chart-error"
          >
            Couldn't load job history:{" "}
            {(query.error as Error).message}
          </p>
        ) : data.length === 0 ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="jobs-runtime-chart-empty"
          >
            No batch history yet. The chart will populate once any
            scheduled job (bootstrap, media-integrity, auto-heal,
            etc.) has run.
          </p>
        ) : (
          <div
            className="h-48 w-full"
            data-testid="jobs-runtime-chart-area"
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="label"
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 10 }}
                />
                <YAxis
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 10 }}
                  label={{
                    value: "seconds",
                    angle: -90,
                    position: "insideLeft",
                    style: { fontSize: 10, fill: "var(--fg-faint)" },
                  }}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--bg-2)",
                    border: "1px solid var(--border)",
                    fontSize: 12,
                  }}
                  formatter={(value) => {
                    const n = typeof value === "number" ? value : Number(value ?? 0);
                    return [`${n.toFixed(2)}s`, "elapsed"];
                  }}
                />
                <Bar dataKey="elapsed" radius={[2, 2, 0, 0]}>
                  {data.map((row, i) => (
                    <Cell key={i} fill={statusColor(row)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface BatchRow {
  ts?: number;
  elapsed?: number;
  ok?: number;
  skipped?: number;
  errors?: number;
}

interface ChartRow {
  label: string;
  elapsed: number;
  ok: number;
  skipped: number;
  errors: number;
}

function buildSeries(history: readonly unknown[] | undefined): ChartRow[] {
  if (!Array.isArray(history) || history.length === 0) return [];
  const rows: ChartRow[] = [];
  for (const raw of history.slice(-25)) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as BatchRow;
    if (typeof r.elapsed !== "number") continue;
    const tsMs =
      typeof r.ts === "number" && r.ts > 0 ? r.ts * 1000 : Date.now();
    const label = new Date(tsMs).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    rows.push({
      label,
      elapsed: r.elapsed,
      ok: typeof r.ok === "number" ? r.ok : 0,
      skipped: typeof r.skipped === "number" ? r.skipped : 0,
      errors: typeof r.errors === "number" ? r.errors : 0,
    });
  }
  return rows;
}

function statusColor(row: ChartRow): string {
  if (row.errors > 0) return "#f87171"; // rose
  if (row.skipped > 0) return "#facc15"; // yellow
  return "#4ade80"; // emerald
}
