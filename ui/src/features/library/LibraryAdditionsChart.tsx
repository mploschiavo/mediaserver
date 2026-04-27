import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { TrendingUp } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRecentLibraryAdditions, type RecentAdditionEntry } from "./hooks";

/**
 * Library additions over time — buckets the rows from
 * ``GET /api/recent`` by calendar day for the last 7 days, stacked
 * per *arr service (radarr / sonarr / lidarr / readarr).
 *
 * Why an empty card with a caption rather than ``items.length === 0
 * ? null``: per the empty-state-visibility feedback, hiding the
 * card after pod-boot makes operators think the feature is missing.
 * The card always renders; the body switches to a friendly
 * "nothing added yet" caption when the rolling buffer is empty.
 */
export function LibraryAdditionsChart() {
  const query = useRecentLibraryAdditions();
  const data = useMemo(() => bucketByDay(query.data?.recent), [query.data]);
  const services = useMemo(() => seriesKeys(data), [data]);

  return (
    <Card data-testid="library-additions-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TrendingUp aria-hidden className="size-4" />
          Additions over time
        </CardTitle>
        <CardDescription>
          New items per day for the last 7 days, stacked by *arr service.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-48 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="library-additions-chart-error"
          >
            Couldn't load library additions:{" "}
            {(query.error as Error).message}
          </p>
        ) : services.length === 0 ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="library-additions-chart-empty"
          >
            Nothing added in the last 7 days. *arr will populate this
            feed as it imports new releases.
          </p>
        ) : (
          <div
            className="h-48 w-full"
            data-testid="library-additions-chart-area"
          >
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data}>
                <defs>
                  {services.map((svc, i) => (
                    <linearGradient
                      id={`fill-${svc}`}
                      key={svc}
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="5%"
                        stopColor={SERIES_COLORS[i % SERIES_COLORS.length]}
                        stopOpacity={0.6}
                      />
                      <stop
                        offset="95%"
                        stopColor={SERIES_COLORS[i % SERIES_COLORS.length]}
                        stopOpacity={0.05}
                      />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="day"
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 11 }}
                  tickFormatter={shortDay}
                />
                <YAxis
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 11 }}
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--bg-2)",
                    border: "1px solid var(--border)",
                    fontSize: 12,
                  }}
                  labelFormatter={shortDay}
                />
                {services.map((svc, i) => (
                  <Area
                    key={svc}
                    type="monotone"
                    dataKey={svc}
                    stackId="services"
                    stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                    fill={`url(#fill-${svc})`}
                    name={svc}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const SERIES_COLORS = [
  "#4ade80", // emerald — radarr (movies)
  "#60a5fa", // sky — sonarr (tv)
  "#a78bfa", // violet — lidarr (music)
  "#f97316", // orange — readarr (books)
  "#facc15", // yellow — fallback
];

interface DayRow {
  day: string;
  [service: string]: number | string;
}

function bucketByDay(
  recent: Record<string, readonly RecentAdditionEntry[] | undefined> | undefined,
): DayRow[] {
  // Build the 7-day skeleton so the X axis is stable even when a
  // service has no entries on a given day.
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days: DayRow[] = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({ day: key });
  }

  if (!recent) return days;

  for (const [service, entries] of Object.entries(recent)) {
    const list = Array.isArray(entries)
      ? (entries as readonly RecentAdditionEntry[])
      : [];
    for (const entry of list) {
      if (typeof entry.added !== "string" || !entry.added) continue;
      const parsed = Date.parse(entry.added);
      if (!Number.isFinite(parsed)) continue;
      const d = new Date(parsed);
      d.setHours(0, 0, 0, 0);
      const key = d.toISOString().slice(0, 10);
      const row = days.find((r) => r.day === key);
      if (!row) continue;
      row[service] = ((row[service] as number) ?? 0) + 1;
    }
  }
  return days;
}

function seriesKeys(rows: DayRow[]): string[] {
  const out = new Set<string>();
  for (const row of rows) {
    for (const k of Object.keys(row)) {
      if (k !== "day" && typeof row[k] === "number") out.add(k);
    }
  }
  return Array.from(out).sort();
}

function shortDay(value: string): string {
  if (typeof value !== "string") return String(value);
  const d = new Date(value + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}
