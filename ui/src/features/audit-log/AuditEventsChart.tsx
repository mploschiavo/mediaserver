import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity, PieChart as PieIcon } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuditLog } from "./hooks";

/**
 * Two charts on the audit-log page:
 *
 *   1. Events-per-hour bar chart over the last 24h (via the
 *      ``/api/audit-log?limit=500`` payload bucketed by hour).
 *   2. Actor split donut — count of events grouped by ``actor``
 *      so operators can see who/what is generating the most
 *      activity (typically ``system`` >> ``admin``).
 *
 * Both charts share the same query payload so we don't double-
 * fetch. The card is always rendered with an explicit empty
 * caption when the buffer is fresh, per the empty-state-visibility
 * feedback.
 */
export function AuditEventsChart() {
  const query = useAuditLog(500);
  const { hourly, actors } = useMemo(
    () => bucketAuditEntries(query.data?.entries),
    [query.data],
  );

  return (
    <Card data-testid="audit-events-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity aria-hidden className="size-4" />
          Activity
        </CardTitle>
        <CardDescription>
          Events per hour for the last 24h, plus a per-actor split of
          the same window.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-48 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="audit-events-chart-error"
          >
            Couldn't load audit-log activity:{" "}
            {(query.error as Error).message}
          </p>
        ) : hourly.length === 0 ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="audit-events-chart-empty"
          >
            No events in the last 24 hours. Operator actions will
            populate this chart as they happen.
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            <div data-testid="audit-events-hourly">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-fg-faint">
                <Activity className="size-3" aria-hidden />
                Events / hour
              </div>
              <div className="h-40 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={hourly}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                    <XAxis
                      dataKey="hour"
                      stroke="var(--fg-faint)"
                      tick={{ fontSize: 10 }}
                    />
                    <YAxis
                      stroke="var(--fg-faint)"
                      tick={{ fontSize: 10 }}
                      allowDecimals={false}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "var(--bg-2)",
                        border: "1px solid var(--border)",
                        fontSize: 12,
                      }}
                    />
                    <Bar
                      dataKey="count"
                      fill="#60a5fa"
                      radius={[2, 2, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div data-testid="audit-events-actors">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-fg-faint">
                <PieIcon className="size-3" aria-hidden />
                Events by actor
              </div>
              <div className="h-40 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={actors}
                      dataKey="count"
                      nameKey="actor"
                      cx="50%"
                      cy="50%"
                      innerRadius={32}
                      outerRadius={64}
                      paddingAngle={2}
                    >
                      {actors.map((_, i) => (
                        <Cell
                          key={i}
                          fill={ACTOR_COLORS[i % ACTOR_COLORS.length]}
                        />
                      ))}
                    </Pie>
                    <Legend
                      verticalAlign="bottom"
                      iconSize={8}
                      wrapperStyle={{ fontSize: 11 }}
                    />
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
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const ACTOR_COLORS = [
  "#4ade80", // emerald
  "#60a5fa", // sky
  "#a78bfa", // violet
  "#facc15", // yellow
  "#f97316", // orange
  "#f87171", // rose
  "#94a3b8", // slate (fallback)
];

interface AuditEntry {
  timestamp?: string;
  actor?: string;
}

function bucketAuditEntries(
  entries: readonly unknown[] | undefined,
): {
  hourly: { hour: string; count: number }[];
  actors: { actor: string; count: number }[];
} {
  const list: AuditEntry[] = Array.isArray(entries)
    ? (entries as AuditEntry[])
    : [];

  // 24-hour skeleton so the X-axis is stable when buckets are empty.
  const now = new Date();
  const hourBuckets = new Map<string, number>();
  for (let i = 23; i >= 0; i--) {
    const d = new Date(now);
    d.setMinutes(0, 0, 0);
    d.setHours(d.getHours() - i);
    hourBuckets.set(d.toISOString().slice(0, 13), 0);
  }

  const actorCounts = new Map<string, number>();
  let anyInWindow = false;
  for (const entry of list) {
    const ts = typeof entry.timestamp === "string" ? entry.timestamp : "";
    if (!ts) continue;
    const parsed = Date.parse(ts);
    if (!Number.isFinite(parsed)) continue;
    const ageMs = now.getTime() - parsed;
    if (ageMs < 0 || ageMs > 24 * 3600 * 1000) continue;
    anyInWindow = true;
    const key = new Date(parsed).toISOString().slice(0, 13);
    if (hourBuckets.has(key)) {
      hourBuckets.set(key, (hourBuckets.get(key) ?? 0) + 1);
    }
    const actor = (typeof entry.actor === "string" && entry.actor) || "unknown";
    actorCounts.set(actor, (actorCounts.get(actor) ?? 0) + 1);
  }

  if (!anyInWindow) {
    return { hourly: [], actors: [] };
  }

  const hourly = Array.from(hourBuckets, ([hour, count]) => ({
    hour: hour.slice(11, 13) + ":00",
    count,
  }));

  const actors = Array.from(actorCounts, ([actor, count]) => ({
    actor,
    count,
  })).sort((a, b) => b.count - a.count);

  return { hourly, actors };
}
