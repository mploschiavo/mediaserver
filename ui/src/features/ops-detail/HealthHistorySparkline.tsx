import { useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHealthHistory, type HealthHistoryRawEntry } from "./hooks";

const WIDTH = 240;
const HEIGHT = 40;
const PADDING_X = 2;
const PADDING_Y = 4;

interface Point {
  /** epoch seconds */
  ts: number;
  ok: number;
  total: number;
}

function buildPoints(
  history: readonly HealthHistoryRawEntry[] | undefined,
): Point[] {
  if (!history || history.length === 0) return [];
  return history
    .map((e) => {
      const services = e.services ?? {};
      const total = Object.keys(services).length;
      let ok = 0;
      for (const v of Object.values(services)) {
        if ((v?.status ?? "").toLowerCase() === "ok") ok += 1;
      }
      return { ts: e.ts ?? 0, ok, total };
    })
    .filter((p) => p.total > 0);
}

interface Geometry {
  pathD: string;
  fillD: string;
  coords: { x: number; y: number; point: Point }[];
}

function computeGeometry(points: Point[]): Geometry | null {
  if (points.length === 0) return null;
  // Use ratios — keeps the y-scale stable (0..1).
  const n = points.length;
  const xStep =
    n === 1 ? 0 : (WIDTH - 2 * PADDING_X) / (n - 1);
  const innerH = HEIGHT - 2 * PADDING_Y;
  const coords = points.map((p, i) => {
    const ratio = p.total > 0 ? p.ok / p.total : 0;
    const x = PADDING_X + i * xStep;
    const y = PADDING_Y + (1 - Math.max(0, Math.min(1, ratio))) * innerH;
    return { x, y, point: p };
  });
  if (coords.length === 1 && coords[0]) {
    const c = coords[0];
    return {
      pathD: `M ${c.x.toFixed(2)} ${c.y.toFixed(2)}`,
      fillD: "",
      coords,
    };
  }
  const pathD = coords
    .map(
      (c, i) =>
        `${i === 0 ? "M" : "L"} ${c.x.toFixed(2)} ${c.y.toFixed(2)}`,
    )
    .join(" ");
  const first = coords[0];
  const last = coords[coords.length - 1];
  const fillD = first && last
    ? `${pathD} L ${last.x.toFixed(2)} ${(HEIGHT - PADDING_Y).toFixed(2)} L ${first.x.toFixed(2)} ${(HEIGHT - PADDING_Y).toFixed(2)} Z`
    : "";
  return { pathD, fillD, coords };
}

function formatTooltip(point: Point): string {
  const ts = point.ts ? new Date(point.ts * 1000).toLocaleString() : "—";
  return `${ts} · ${point.ok}/${point.total} ok`;
}

interface SparklineSvgProps {
  geometry: Geometry;
  onHover: (idx: number | null) => void;
  hoverIdx: number | null;
}

function SparklineSvg({
  geometry,
  onHover,
  hoverIdx,
}: SparklineSvgProps) {
  const hoverCoord =
    hoverIdx !== null ? geometry.coords[hoverIdx] : null;
  return (
    <svg
      width={WIDTH}
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label="Health history sparkline"
      data-testid="health-history-svg"
      onMouseLeave={() => onHover(null)}
    >
      {geometry.fillD ? (
        <path
          d={geometry.fillD}
          fill="color-mix(in oklab, var(--color-success) 18%, transparent)"
          stroke="none"
        />
      ) : null}
      <path
        d={geometry.pathD}
        fill="none"
        stroke="var(--color-success)"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {hoverCoord ? (
        <circle
          cx={hoverCoord.x}
          cy={hoverCoord.y}
          r={2.5}
          fill="var(--color-success)"
          stroke="var(--color-bg)"
          strokeWidth={1}
        />
      ) : null}
      {/* Hover hit-strips so users can hover anywhere along the x-axis. */}
      {geometry.coords.map((c, i) => {
        const half =
          i === geometry.coords.length - 1
            ? WIDTH - c.x
            : ((geometry.coords[i + 1]?.x ?? c.x) - c.x) / 2 + 1;
        const stripX = Math.max(0, c.x - half);
        const stripW =
          i === 0
            ? c.x + half
            : i === geometry.coords.length - 1
              ? half + 1
              : half * 2;
        return (
          <rect
            key={i}
            x={stripX}
            y={0}
            width={Math.max(1, stripW)}
            height={HEIGHT}
            fill="transparent"
            onMouseEnter={() => onHover(i)}
            onFocus={() => onHover(i)}
            data-testid={`spark-hit-${i}`}
          />
        );
      })}
    </svg>
  );
}

export function HealthHistorySparkline() {
  const query = useHealthHistory();
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const points = useMemo<Point[]>(
    () => buildPoints(query.data?.history),
    [query.data],
  );
  const geometry = useMemo(() => computeGeometry(points), [points]);

  const periodHours = query.data?.period_hours;
  const latest = points.length > 0 ? points[points.length - 1] : null;

  return (
    <Card data-testid="health-history-card">
      <CardHeader>
        <CardTitle>Health history</CardTitle>
        <CardDescription>
          {periodHours !== undefined && periodHours > 0
            ? `Last ${periodHours.toFixed(1)}h of probe samples`
            : "Recent probe samples"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div data-testid="health-history-loading">
            <Skeleton className="h-10" style={{ width: WIDTH }} />
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="health-history-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : !geometry || points.length === 0 ? (
          <div
            className="text-sm text-fg-muted"
            data-testid="health-history-empty"
          >
            No history yet — the controller hasn't recorded enough probe
            samples to plot.
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <div
              className="relative inline-block"
              style={{ width: WIDTH, height: HEIGHT }}
            >
              <SparklineSvg
                geometry={geometry}
                hoverIdx={hoverIdx}
                onHover={setHoverIdx}
              />
              {hoverIdx !== null && geometry.coords[hoverIdx] ? (
                <div
                  role="tooltip"
                  data-testid="health-history-tooltip"
                  className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-md border border-border bg-bg-3 px-2 py-1 text-xs text-fg shadow-md"
                  style={{
                    left: geometry.coords[hoverIdx].x,
                    top: -8,
                    transform: "translate(-50%, -100%)",
                  }}
                >
                  {formatTooltip(geometry.coords[hoverIdx].point)}
                </div>
              ) : null}
            </div>
            {latest ? (
              <div className="text-xs text-fg-muted tabular-nums">
                Latest: {latest.ok}/{latest.total} services ok
              </div>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
