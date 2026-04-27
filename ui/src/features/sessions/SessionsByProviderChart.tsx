import { useMemo } from "react";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { Network } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveSessions } from "./hooks";

/**
 * Donut of active sessions grouped by provider — answers "who's
 * holding sessions on this stack right now?" at a glance, before
 * the operator scans the full table. Stays empty-but-visible per
 * the empty-state-visibility feedback so the chart doesn't vanish
 * the moment everyone signs out.
 *
 * A "concurrent over time" line chart needs a server-side rolling
 * buffer that doesn't exist yet — this is the no-server-work
 * version that ships value today.
 */
export function SessionsByProviderChart() {
  const query = useActiveSessions();
  const data = useMemo(
    () => bucketByProvider(query.data?.sessions),
    [query.data],
  );

  return (
    <Card data-testid="sessions-by-provider-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Network aria-hidden className="size-4" />
          Sessions by provider
        </CardTitle>
        <CardDescription>
          Live session split across the controller and every linked
          identity provider. Refreshes with the same cadence as the
          row table below.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-44 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="sessions-by-provider-chart-error"
          >
            Couldn't load active sessions:{" "}
            {(query.error as Error).message}
          </p>
        ) : data.length === 0 ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="sessions-by-provider-chart-empty"
          >
            No active sessions right now. The chart will populate
            when an operator signs in to the dashboard or any linked
            service (Jellyfin, Jellyseerr, Authelia).
          </p>
        ) : (
          <div
            className="h-44 w-full"
            data-testid="sessions-by-provider-chart-area"
          >
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="count"
                  nameKey="provider"
                  cx="50%"
                  cy="50%"
                  innerRadius={36}
                  outerRadius={70}
                  paddingAngle={2}
                >
                  {data.map((_, i) => (
                    <Cell
                      key={i}
                      fill={PROVIDER_COLORS[i % PROVIDER_COLORS.length]}
                    />
                  ))}
                </Pie>
                <Legend
                  verticalAlign="bottom"
                  iconSize={9}
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
        )}
      </CardContent>
    </Card>
  );
}

const PROVIDER_COLORS = [
  "#4ade80", // emerald — controller
  "#60a5fa", // sky — authelia
  "#a78bfa", // violet — jellyfin
  "#f97316", // orange — jellyseerr
  "#facc15", // yellow — fallback
  "#94a3b8", // slate — fallback
];

interface Session {
  provider?: string;
}

function bucketByProvider(
  sessions: readonly unknown[] | undefined,
): { provider: string; count: number }[] {
  if (!Array.isArray(sessions) || sessions.length === 0) return [];
  const counts = new Map<string, number>();
  for (const raw of sessions) {
    if (!raw || typeof raw !== "object") continue;
    const s = raw as Session;
    const provider =
      (typeof s.provider === "string" && s.provider) || "unknown";
    counts.set(provider, (counts.get(provider) ?? 0) + 1);
  }
  return Array.from(counts, ([provider, count]) => ({ provider, count })).sort(
    (a, b) => b.count - a.count,
  );
}
