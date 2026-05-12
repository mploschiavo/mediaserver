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
            className="flex h-44 w-full items-stretch gap-4"
            data-testid="livetv-health-chart-area"
          >
            {/*
              The flex-1 wrapper needs ``min-w-0`` so it can shrink
              (without it, the legend's intrinsic size keeps it at
              its content width and the chart wrapper resolves to 0
              under flex-shrink). ``h-full`` on the wrapper plus
              explicit ``min-h-[150px]`` on the chart container is
              the Recharts-documented shape for filling a flex parent:
              ResponsiveContainer reads ``clientWidth`` /
              ``clientHeight`` on the parent and bails out silently
              if either is 0 — exactly the 2026-05-12 symptom where
              the donut's colour keys rendered but the donut itself
              never drew because the parent was 0px tall under
              ``items-center``.
            */}
            <div className="flex-1 min-w-0 h-full min-h-[150px]">
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

/**
 * Convert the live ``/api/epg-health`` response (verified shape:
 * ``{healthy, unhealthy, countries, providers, details}``) into a
 * pass/fail/unknown breakdown the donut renders. Two earlier
 * shapes the controller has never actually returned are still
 * accepted for forward-compat — ``{probes: [...]}`` and
 * ``{sources: [...]}`` — but the real path now reads ``healthy``
 * + ``unhealthy`` (preferred) or walks ``details`` (per-country,
 * per-provider boolean map) when the aggregates are missing.
 *
 * The pre-fix code only looked at ``probes`` / ``sources`` and
 * always returned ``[]``, so the donut showed "No guide sources
 * configured" even on a fully-healthy deploy with EPG providers
 * actively probing — the bug the user reported 2026-05-12.
 */
function bucketHealth(
  raw: unknown,
): { status: string; count: number }[] {
  if (!raw || typeof raw !== "object") return [];
  const probesShape =
    (raw as { probes?: ProbeShape[] }).probes ??
    (raw as { sources?: ProbeShape[] }).sources ??
    null;
  if (probesShape && probesShape.length > 0) {
    return bucketFromProbes(probesShape);
  }
  return bucketFromAggregates(raw as Record<string, unknown>);
}

function bucketFromProbes(
  probes: ProbeShape[],
): { status: string; count: number }[] {
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

function bucketFromAggregates(
  raw: Record<string, unknown>,
): { status: string; count: number }[] {
  const healthy =
    typeof raw.healthy === "number" ? raw.healthy : null;
  const unhealthy =
    typeof raw.unhealthy === "number" ? raw.unhealthy : null;
  if (healthy !== null || unhealthy !== null) {
    const buckets: { status: string; count: number }[] = [];
    if ((healthy ?? 0) > 0) {
      buckets.push({ status: "pass", count: healthy ?? 0 });
    }
    if ((unhealthy ?? 0) > 0) {
      buckets.push({ status: "fail", count: unhealthy ?? 0 });
    }
    return buckets;
  }
  // ``details`` is the per-country per-provider boolean matrix —
  // walk it when the aggregate counts are missing (older controller
  // builds, or test fixtures).
  const details = raw.details;
  if (!details || typeof details !== "object") return [];
  let pass = 0;
  let fail = 0;
  for (const perCountry of Object.values(
    details as Record<string, Record<string, boolean>>,
  )) {
    if (!perCountry || typeof perCountry !== "object") continue;
    for (const ok of Object.values(perCountry)) {
      if (ok === true) pass += 1;
      else if (ok === false) fail += 1;
    }
  }
  const buckets: { status: string; count: number }[] = [];
  if (pass > 0) buckets.push({ status: "pass", count: pass });
  if (fail > 0) buckets.push({ status: "fail", count: fail });
  return buckets;
}
