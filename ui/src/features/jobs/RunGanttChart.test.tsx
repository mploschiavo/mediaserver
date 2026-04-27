import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import {
  RunGanttChart,
  buildGanttLayout,
} from "./RunGanttChart";
import type { RunRecordShape } from "./hooks";

function makeRun(overrides: Partial<RunRecordShape> = {}): RunRecordShape {
  return {
    run_id: "01J5GANTT00000000000000000",
    job_name: "scan",
    status: "ok",
    started_at: 1_700_000_000,
    triggered_by: "cron",
    attempts: 1,
    child_run_ids: [],
    elapsed: 1,
    completed_at: 1_700_000_001,
    ...overrides,
  };
}

describe("buildGanttLayout", () => {
  it("returns one row per child sorted by start time", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 100,
      completed_at: 110,
      elapsed: 10,
    });
    const children: RunRecordShape[] = [
      makeRun({
        run_id: "B",
        started_at: 105,
        completed_at: 108,
        elapsed: 3,
      }),
      makeRun({
        run_id: "A",
        started_at: 100,
        completed_at: 103,
        elapsed: 3,
      }),
    ];
    const layout = buildGanttLayout(parent, children, 100, 20);
    expect(layout.rows.map((r) => r.run.run_id)).toEqual(["A", "B"]);
  });

  it("anchors x at the parent start and scales width to total elapsed", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: 10,
      elapsed: 10,
    });
    const children: RunRecordShape[] = [
      makeRun({
        run_id: "C",
        started_at: 5,
        completed_at: 8,
        elapsed: 3,
      }),
    ];
    const layout = buildGanttLayout(parent, children, 100, 20);
    // Inner width = 100 - 4 - 4 = 92. Half-way through the span ⇒ x = 4 + 46.
    expect(layout.totalElapsed).toBeCloseTo(10, 5);
    const row = layout.rows[0]!;
    // X anchored at PAD_X (4) + (5/10)*92 = 50.
    expect(row.x).toBeCloseTo(50, 1);
    // W = (3/10)*92 = 27.6.
    expect(row.w).toBeCloseTo(27.6, 1);
  });

  it("falls back to elapsed-derived end when completed_at is missing", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: undefined,
      elapsed: undefined,
    });
    const children: RunRecordShape[] = [
      makeRun({
        run_id: "C",
        started_at: 0,
        completed_at: undefined,
        elapsed: 4,
      }),
    ];
    const layout = buildGanttLayout(parent, children, 100, 20);
    expect(layout.totalElapsed).toBeCloseTo(4, 5);
  });

  it("widens narrow bars to a 2px floor so they remain visible", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: 10,
      elapsed: 10,
    });
    const children: RunRecordShape[] = [
      makeRun({
        run_id: "C",
        started_at: 0,
        completed_at: 0.0001,
        elapsed: 0.0001,
      }),
    ];
    const layout = buildGanttLayout(parent, children, 100, 20);
    const [first] = layout.rows;
    if (first === undefined) {
      throw new Error("expected one row");
    }
    expect(first.w).toBeGreaterThanOrEqual(2);
  });
});

describe("RunGanttChart component", () => {
  it("renders an empty notice when there are no children", () => {
    renderWithProviders(
      <RunGanttChart parent={makeRun({ run_id: "P" })} children={[]} />,
    );
    expect(screen.getByTestId("run-gantt-empty")).toBeInTheDocument();
  });

  it("renders one bar per child", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: 10,
    });
    const children: RunRecordShape[] = [
      makeRun({ run_id: "A", started_at: 0, completed_at: 3 }),
      makeRun({ run_id: "B", started_at: 4, completed_at: 7, status: "error" }),
    ];
    renderWithProviders(
      <RunGanttChart parent={parent} children={children} />,
    );
    expect(screen.getByTestId("run-gantt-row-A")).toBeInTheDocument();
    expect(screen.getByTestId("run-gantt-row-B")).toHaveAttribute(
      "data-status",
      "error",
    );
  });

  it("emits a tooltip with the job name + elapsed on hover", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: 10,
    });
    const children: RunRecordShape[] = [
      makeRun({
        run_id: "A",
        job_name: "configure-media-server",
        elapsed: 1.25,
        started_at: 0,
        completed_at: 1.25,
      }),
    ];
    renderWithProviders(
      <RunGanttChart parent={parent} children={children} />,
    );
    fireEvent.mouseEnter(screen.getByTestId("run-gantt-row-A"));
    const tip = screen.getByTestId("run-gantt-tooltip");
    expect(tip).toHaveTextContent("configure-media-server");
    expect(tip).toHaveTextContent("1.3s");
  });

  it("clears the tooltip on mouse leave of the SVG", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: 10,
    });
    const children: RunRecordShape[] = [
      makeRun({ run_id: "A", started_at: 0, completed_at: 1, elapsed: 1 }),
    ];
    renderWithProviders(
      <RunGanttChart parent={parent} children={children} />,
    );
    fireEvent.mouseEnter(screen.getByTestId("run-gantt-row-A"));
    expect(screen.getByTestId("run-gantt-tooltip")).toBeInTheDocument();
    const svg = screen.getByRole("img");
    fireEvent.mouseLeave(svg);
    expect(screen.queryByTestId("run-gantt-tooltip")).toBeNull();
  });

  it("emits the tooltip via keyboard focus too", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: 10,
    });
    const children: RunRecordShape[] = [
      makeRun({
        run_id: "A",
        job_name: "audit",
        started_at: 0,
        completed_at: 1,
        elapsed: 1,
      }),
    ];
    renderWithProviders(
      <RunGanttChart parent={parent} children={children} />,
    );
    fireEvent.focus(screen.getByTestId("run-gantt-row-A"));
    expect(screen.getByTestId("run-gantt-tooltip")).toHaveTextContent(
      "audit",
    );
  });

  it("colours an unknown status with the muted-fg fallback", () => {
    const parent = makeRun({
      run_id: "P",
      started_at: 0,
      completed_at: 5,
    });
    const children: RunRecordShape[] = [
      makeRun({
        run_id: "A",
        status: "novel-status",
        started_at: 0,
        completed_at: 1,
      }),
    ];
    renderWithProviders(
      <RunGanttChart parent={parent} children={children} />,
    );
    const row = screen.getByTestId("run-gantt-row-A");
    expect(row).toHaveAttribute("data-status", "novel-status");
  });
});
