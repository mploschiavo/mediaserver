import { useMemo } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDownloadAnalytics } from "./hooks";

const WIDTH = 240;
const HEIGHT = 40;
const PADDING_X = 2;
const PADDING_Y = 4;

interface Point {
  ts: string;
  count: number;
}

function buildPoints(
  series: readonly { ts?: string; count?: number }[] | undefined,
): Point[] {
  if (!series || series.length === 0) return [];
  return series
    .map((p) => ({
      ts: typeof p.ts === "string" ? p.ts : "",
      count: typeof p.count === "number" && Number.isFinite(p.count) ? p.count : 0,
    }))
    .filter((p) => p.ts !== "");
}

interface Geometry {
  pathD: string;
  fillD: string;
}

function computeGeometry(points: Point[]): Geometry | null {
  if (points.length === 0) return null;
  const max = Math.max(1, ...points.map((p) => p.count));
  const n = points.length;
  const xStep = n === 1 ? 0 : (WIDTH - 2 * PADDING_X) / (n - 1);
  const innerH = HEIGHT - 2 * PADDING_Y;
  const coords = points.map((p, i) => {
    const ratio = max > 0 ? p.count / max : 0;
    const x = PADDING_X + i * xStep;
    const y = PADDING_Y + (1 - Math.max(0, Math.min(1, ratio))) * innerH;
    return { x, y };
  });
  const pathD = coords
    .map(
      (c, i) =>
        `${i === 0 ? "M" : "L"} ${c.x.toFixed(2)} ${c.y.toFixed(2)}`,
    )
    .join(" ");
  const first = coords[0];
  const last = coords[coords.length - 1];
  const fillD =
    first && last && coords.length > 1
      ? `${pathD} L ${last.x.toFixed(2)} ${(HEIGHT - PADDING_Y).toFixed(2)} L ${first.x.toFixed(2)} ${(HEIGHT - PADDING_Y).toFixed(2)} Z`
      : "";
  return { pathD, fillD };
}

interface StatProps {
  label: string;
  value: number;
}

function Stat({ label, value }: StatProps) {
  return (
    <div className="flex flex-col">
      <span className="text-xs uppercase tracking-wide text-fg-muted">
        {label}
      </span>
      <span className="font-mono text-2xl font-semibold tabular-nums text-fg">
        {value.toLocaleString()}
      </span>
    </div>
  );
}

export function DownloadAnalyticsCard() {
  const query = useDownloadAnalytics();
  const points = useMemo(
    () => buildPoints(query.data?.series),
    [query.data?.series],
  );
  const geometry = useMemo(() => computeGeometry(points), [points]);

  const totals = query.data?.totals;
  const completed = typeof totals?.completed === "number" ? totals.completed : 0;
  const failed = typeof totals?.failed === "number" ? totals.failed : 0;
  const grabbed = typeof totals?.grabbed === "number" ? totals.grabbed : 0;

  return (
    <Card data-testid="download-analytics">
      <CardHeader>
        <CardTitle>Analytics</CardTitle>
        <CardDescription>
          Aggregate download counts and trend over the recent window.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {query.isLoading ? (
          <div data-testid="download-analytics-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="mt-2 h-12" style={{ width: WIDTH }} />
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="download-analytics-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-4" data-testid="download-totals">
              <Stat label="Completed" value={completed} />
              <Stat label="Grabbed" value={grabbed} />
              <Stat label="Failed" value={failed} />
            </div>
            {geometry ? (
              <svg
                width={WIDTH}
                height={HEIGHT}
                viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
                role="img"
                aria-label="Download volume trend"
                data-testid="download-analytics-sparkline"
              >
                {geometry.fillD ? (
                  <path
                    d={geometry.fillD}
                    fill="color-mix(in oklab, var(--color-accent) 18%, transparent)"
                    stroke="none"
                  />
                ) : null}
                <path
                  d={geometry.pathD}
                  fill="none"
                  stroke="var(--color-accent)"
                  strokeWidth={1.5}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </svg>
            ) : (
              <p
                className="text-sm text-fg-muted"
                data-testid="download-analytics-empty"
              >
                Not enough samples to plot a trend yet.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
