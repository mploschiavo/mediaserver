import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import type { RunRecordShape } from "./hooks";
import { formatElapsed } from "./format";

export interface RunGanttChartProps {
  /**
   * The parent (batch) run plus its children — the same shape returned
   * by `useRun(<run_id>)`. The chart anchors the timeline at the
   * parent's `started_at` and lays each child out horizontally.
   */
  parent: RunRecordShape;
  children: readonly RunRecordShape[];
  /** Total chart width in CSS pixels. Defaults to 480. */
  width?: number;
  /** Per-row height in CSS pixels. Defaults to 18. */
  rowHeight?: number;
}

interface Layout {
  rows: readonly {
    run: RunRecordShape;
    x: number;
    w: number;
    y: number;
  }[];
  /** Total elapsed seconds — the timeline x-axis upper bound. */
  totalElapsed: number;
  /** Chart inner width minus padding. */
  innerWidth: number;
  /** Computed total height. */
  height: number;
}

const PAD_X = 4;
const STATUS_FILL: Record<string, string> = {
  running: "var(--color-info)",
  ok: "var(--color-success)",
  skipped: "var(--color-warning)",
  error: "var(--color-danger)",
  cancelled: "var(--color-fg-faint)",
  timeout: "var(--color-danger)",
};

/**
 * Compute geometry for the Gantt rows. Pulled out as a pure function
 * so the unit tests can verify the math without mounting the SVG.
 */
export function buildGanttLayout(
  parent: RunRecordShape,
  children: readonly RunRecordShape[],
  width: number,
  rowHeight: number,
): Layout {
  const innerWidth = Math.max(0, width - PAD_X * 2);
  const t0 = parent.started_at;
  // Determine the right edge: prefer the parent's own completed_at; fall
  // back to the latest child completed_at; finally to "now" so an
  // in-flight batch still draws a meaningful axis.
  const candidateEnds: number[] = [];
  if (typeof parent.completed_at === "number") {
    candidateEnds.push(parent.completed_at);
  }
  for (const c of children) {
    if (typeof c.completed_at === "number") {
      candidateEnds.push(c.completed_at);
    } else if (Number.isFinite(c.started_at) && typeof c.elapsed === "number") {
      candidateEnds.push(c.started_at + c.elapsed);
    }
  }
  const latest =
    candidateEnds.length > 0
      ? Math.max(...candidateEnds)
      : t0 + (parent.elapsed ?? 0);
  const totalElapsed = Math.max(0.0001, latest - t0);

  const sorted = [...children].sort(
    (a, b) => a.started_at - b.started_at,
  );
  const rows = sorted.map((c, i) => {
    const startOffset = Math.max(0, c.started_at - t0);
    const childEnd =
      typeof c.completed_at === "number"
        ? c.completed_at
        : typeof c.elapsed === "number"
          ? c.started_at + c.elapsed
          : latest;
    const span = Math.max(0, childEnd - c.started_at);
    const x = PAD_X + (startOffset / totalElapsed) * innerWidth;
    const w = Math.max(2, (span / totalElapsed) * innerWidth);
    return { run: c, x, w, y: i * rowHeight };
  });
  return {
    rows,
    totalElapsed,
    innerWidth,
    height: Math.max(rowHeight, sorted.length * rowHeight),
  };
}

/**
 * Phase-3 Gantt visualization for a batch run. Renders one horizontal
 * bar per child sub-run, with x-axis time-since-batch-start and width
 * proportional to elapsed. Bars are coloured by status. Hover surfaces
 * a tooltip with the job name + elapsed.
 *
 * Empty children → renders a "no sub-runs" notice rather than a blank
 * SVG (the operator should not see ambiguous empty space).
 */
export function RunGanttChart({
  parent,
  children,
  width = 480,
  rowHeight = 18,
}: RunGanttChartProps) {
  const [hover, setHover] = useState<number | null>(null);
  const layout = useMemo(
    () => buildGanttLayout(parent, children, width, rowHeight),
    [parent, children, width, rowHeight],
  );

  if (children.length === 0) {
    return (
      <p
        className="text-xs text-fg-faint"
        data-testid="run-gantt-empty"
      >
        No sub-runs to chart.
      </p>
    );
  }

  const hoverRow = hover !== null ? layout.rows[hover] : null;

  return (
    <div
      className="relative inline-block"
      style={{ width }}
      data-testid="run-gantt-chart"
      data-total-elapsed={layout.totalElapsed.toFixed(3)}
    >
      <svg
        width={width}
        height={layout.height}
        viewBox={`0 0 ${width} ${layout.height}`}
        role="img"
        aria-label="Per-child run timeline"
        onMouseLeave={() => setHover(null)}
      >
        {layout.rows.map((row, i) => (
          <g
            key={row.run.run_id}
            data-testid={`run-gantt-row-${row.run.run_id}`}
            data-status={row.run.status}
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
          >
            <rect
              x={PAD_X}
              y={row.y + 2}
              width={Math.max(0, layout.innerWidth)}
              height={rowHeight - 4}
              fill="var(--color-bg-2)"
              opacity={0.4}
            />
            <rect
              x={row.x}
              y={row.y + 2}
              width={row.w}
              height={rowHeight - 4}
              rx={2}
              fill={
                STATUS_FILL[row.run.status] ?? "var(--color-fg-muted)"
              }
              opacity={hover === i ? 1 : 0.85}
            />
          </g>
        ))}
      </svg>
      {hoverRow ? (
        <span
          role="tooltip"
          data-testid="run-gantt-tooltip"
          className={cn(
            "pointer-events-none absolute z-10 -translate-x-1/2",
            "whitespace-nowrap rounded-md border border-border",
            "bg-bg-3 px-1.5 py-0.5 text-[10px] text-fg shadow-md",
          )}
          style={{
            left: hoverRow.x + hoverRow.w / 2,
            top: -4,
            transform: "translate(-50%, -100%)",
          }}
        >
          {hoverRow.run.job_name} · {formatElapsed(hoverRow.run.elapsed)}
          {" · "}
          {hoverRow.run.status}
        </span>
      ) : null}
    </div>
  );
}
