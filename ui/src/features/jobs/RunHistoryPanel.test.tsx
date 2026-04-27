import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const runsState = vi.hoisted(() => ({
  data: null as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const runDetailState = vi.hoisted(() => ({
  data: null as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useRuns: () => ({
      data: runsState.data,
      isLoading: runsState.isLoading,
      error: runsState.error,
    }),
    useRun: () => ({
      data: runDetailState.data,
      isLoading: runDetailState.isLoading,
      error: runDetailState.error,
    }),
  };
});

vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>(
    "@tanstack/react-router",
  );
  return {
    ...actual,
    Link: ({
      children,
      to,
      ...rest
    }: {
      children: React.ReactNode;
      to?: string;
      [key: string]: unknown;
    }) => (
      <a
        href={typeof to === "string" ? to : "#"}
        {...(rest as Record<string, unknown>)}
      >
        {children}
      </a>
    ),
  };
});

import { RunHistoryPanel } from "./RunHistoryPanel";
import type { RunRecordShape, RunRecordWithChildrenShape } from "./hooks";

function makeRun(overrides: Partial<RunRecordShape> = {}): RunRecordShape {
  return {
    run_id: "01J5RUNAAA0000000000000001",
    job_name: "scan-completed-downloads",
    status: "ok",
    started_at: 1_700_000_000,
    triggered_by: "cron",
    attempts: 1,
    child_run_ids: [],
    elapsed: 1.2,
    ...overrides,
  };
}

function reset() {
  runsState.data = null;
  runsState.isLoading = false;
  runsState.error = null;
  runDetailState.data = null;
  runDetailState.isLoading = false;
  runDetailState.error = null;
}

describe("RunHistoryPanel", () => {
  it("renders skeletons while loading", () => {
    reset();
    runsState.isLoading = true;
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-loading")).toBeInTheDocument();
  });

  it("renders an error alert on fetch failure", () => {
    reset();
    runsState.error = new Error("boom");
    renderWithProviders(<RunHistoryPanel />);
    const err = screen.getByTestId("run-history-error");
    expect(err).toHaveTextContent(/boom/);
    expect(err).toHaveAttribute("role", "alert");
  });

  it("renders the empty state when no runs are recorded", () => {
    reset();
    runsState.data = [];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-empty")).toHaveTextContent(
      /No recorded runs yet/,
    );
  });

  it("renders one row per run with status, job name and triggered_by", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5RUNAAA0000000000000001",
        job_name: "scan-completed-downloads",
        status: "ok",
        triggered_by: "cron",
      }),
      makeRun({
        run_id: "01J5RUNBBB0000000000000002",
        job_name: "configure-media-server",
        status: "error",
        triggered_by: "manual",
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    const list = screen.getByTestId("run-history-list");
    expect(list).toBeInTheDocument();
    expect(
      screen.getByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    ).toHaveTextContent(/scan-completed-downloads/);
    expect(
      screen.getByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    ).toHaveTextContent(/cron/);
    expect(
      screen.getByTestId("run-history-row-01J5RUNBBB0000000000000002"),
    ).toHaveAttribute("data-status", "error");
  });

  it("filters by job-name needle (case-insensitive substring)", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5RUNAAA0000000000000001",
        job_name: "scan-completed-downloads",
      }),
      makeRun({
        run_id: "01J5RUNBBB0000000000000002",
        job_name: "configure-media-server",
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    const input = screen.getByTestId("run-history-job-filter");
    fireEvent.change(input, { target: { value: "CONFIG" } });
    expect(
      screen.queryByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    ).toBeNull();
    expect(
      screen.getByTestId("run-history-row-01J5RUNBBB0000000000000002"),
    ).toBeInTheDocument();
  });

  it("filters by status when the operator picks a non-default option", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5RUNAAA0000000000000001",
        status: "ok",
      }),
      makeRun({
        run_id: "01J5RUNBBB0000000000000002",
        status: "error",
      }),
      makeRun({
        run_id: "01J5RUNCCC0000000000000003",
        status: "skipped",
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    const select = screen.getByTestId(
      "run-history-status-filter",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "error" } });
    expect(
      screen.queryByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    ).toBeNull();
    expect(
      screen.getByTestId("run-history-row-01J5RUNBBB0000000000000002"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("run-history-row-01J5RUNCCC0000000000000003"),
    ).toBeNull();
  });

  it("renders 'No runs match' when filters narrow to zero", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5RUNAAA0000000000000001",
        job_name: "scan",
        status: "ok",
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    fireEvent.change(screen.getByTestId("run-history-job-filter"), {
      target: { value: "no-such-job" },
    });
    expect(
      screen.getByTestId("run-history-empty"),
    ).toHaveTextContent(/No runs match/);
  });

  it("renders the unknown-status badge for an unrecognised status", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5RUNAAA0000000000000001",
        status: "future-status",
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    const row = screen.getByTestId(
      "run-history-row-01J5RUNAAA0000000000000001",
    );
    expect(row).toHaveAttribute("data-status", "future-status");
  });

  it("opens the run drawer when a row is clicked", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5RUNAAA0000000000000001" })];
    renderWithProviders(<RunHistoryPanel />);
    // Vaul only mounts the drawer content when open — closed state
    // means no `run-drawer` node in the DOM.
    expect(screen.queryByTestId("run-drawer")).toBeNull();
    fireEvent.click(
      screen.getByTestId(
        "run-history-row-button-01J5RUNAAA0000000000000001",
      ),
    );
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      "01J5RUNAAA0000000000000001",
    );
  });

  it("closes the drawer via its close button (resets selectedRunId)", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5RUNAAA0000000000000001" })];
    renderWithProviders(<RunHistoryPanel />);
    fireEvent.click(
      screen.getByTestId(
        "run-history-row-button-01J5RUNAAA0000000000000001",
      ),
    );
    fireEvent.click(screen.getByTestId("run-drawer-close"));
    // Drawer attribute reflects the cleared state immediately, even
    // if Vaul keeps the content node around for an exit animation.
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      "",
    );
  });

  it("renders the triggered-by source as a 'via X' badge", () => {
    reset();
    runsState.data = [
      makeRun({ run_id: "01J5BY", triggered_by: "cron" }),
      makeRun({ run_id: "01J5BM", triggered_by: "manual" }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(
      screen.getByTestId("run-history-trigger-01J5BY"),
    ).toHaveTextContent("via cron");
    expect(
      screen.getByTestId("run-history-trigger-01J5BM"),
    ).toHaveTextContent("via manual");
  });

  it("renders a one-click Logs deep-link button on every row", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5L", job_name: "scan" })];
    renderWithProviders(<RunHistoryPanel />);
    const link = screen.getByTestId("run-history-logs-01J5L");
    expect(link).toBeInTheDocument();
    // The Tanstack Link mock above renders to ``<a href={to}>``,
    // so checking the href confirms the button routes to /logs
    // — the search params live on data-search.
    expect(link).toHaveAttribute("href", "/logs");
  });

  it("does not tint a row when anomaly_score is absent or null", () => {
    reset();
    runsState.data = [
      makeRun({ run_id: "01J5NORM0000000000000001" }),
      makeRun({ run_id: "01J5NORM0000000000000002", anomaly_score: null }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(
      screen.getByTestId("run-history-row-01J5NORM0000000000000001"),
    ).not.toHaveAttribute("data-tone");
    expect(
      screen.getByTestId("run-history-row-01J5NORM0000000000000002"),
    ).not.toHaveAttribute("data-tone");
  });

  it("tints a row warn for anomaly_score in [1, 2)", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5WARN", anomaly_score: 1.4 })];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-row-01J5WARN")).toHaveAttribute(
      "data-tone",
      "warn",
    );
  });

  it("tints a row err for anomaly_score >= 2", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5ERR", anomaly_score: 3.2 })];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-row-01J5ERR")).toHaveAttribute(
      "data-tone",
      "err",
    );
  });

  it("does not tint a row for negative z-scores (faster than baseline)", () => {
    reset();
    runsState.data = [
      makeRun({ run_id: "01J5FAST", anomaly_score: -1.5 }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-row-01J5FAST")).not.toHaveAttribute(
      "data-tone",
    );
  });

  it("surfaces the anomaly z-score in the row title for hover tooltip", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5HOV", anomaly_score: 2.5 })];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-row-01J5HOV")).toHaveAttribute(
      "title",
      expect.stringContaining("2.5"),
    );
  });

  it("flags rows with parent + child indicators on data-attributes", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5PARENTROW00000000000001",
        parent_run_id: "01J5GRANDPARENT0000000000",
        child_run_ids: ["a", "b", "c"],
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    const row = screen.getByTestId(
      "run-history-row-01J5PARENTROW00000000000001",
    );
    expect(row).toHaveAttribute("data-has-parent", "true");
    expect(row).toHaveAttribute("data-child-count", "3");
  });

  it("renders cancelled and timeout badges with their distinct variants", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5RUNAAA0000000000000001",
        status: "cancelled",
      }),
      makeRun({
        run_id: "01J5RUNBBB0000000000000002",
        status: "timeout",
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(
      screen.getByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    ).toHaveAttribute("data-status", "cancelled");
    expect(
      screen.getByTestId("run-history-row-01J5RUNBBB0000000000000002"),
    ).toHaveAttribute("data-status", "timeout");
  });
  it("closes the run drawer via close button (exercises onClose)", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5RUNAAA0000000000000001" })];
    renderWithProviders(<RunHistoryPanel />);
    fireEvent.click(
      screen.getByTestId("run-history-row-button-01J5RUNAAA0000000000000001"),
    );
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      "01J5RUNAAA0000000000000001",
    );
    // Close fires onClose arrow → setSelectedRunId(null).
    // Vaul keeps the portal in DOM during exit animation (jsdom);
    // data-run-id resets synchronously to "".
    fireEvent.click(screen.getByTestId("run-drawer-close"));
    expect(screen.getByTestId("run-drawer")).toHaveAttribute("data-run-id", "");
  });

  it("navigates to a child run from the drawer (exercises onSelectRunId)", async () => {
    reset();
    const childId = "01J5CHILD0000000000000001";
    const runId = "01J5RUNAAA0000000000000001";
    runsState.data = [makeRun({ run_id: runId, child_run_ids: [childId] })];
    // useRun returns this when the drawer loads the selected run
    runDetailState.data = {
      run_id: runId,
      job_name: "scan-completed-downloads",
      status: "ok",
      started_at: 1_700_000_000,
      triggered_by: "cron",
      attempts: 1,
      child_run_ids: [childId],
      elapsed: 1.2,
      children: [
        {
          run_id: childId,
          job_name: "configure-arr",
          status: "ok",
          started_at: 1_700_000_010,
          triggered_by: "parent",
          attempts: 1,
          child_run_ids: [],
          elapsed: 0.4,
        },
      ],
    } as RunRecordWithChildrenShape;
    const user = userEvent.setup();
    renderWithProviders(<RunHistoryPanel />);
    fireEvent.click(screen.getByTestId(`run-history-row-button-${runId}`));
    expect(screen.getByTestId("run-drawer")).toHaveAttribute("data-run-id", runId);
    // Open Children tab and click the child row → onSelectRunId fires
    await user.click(screen.getByTestId("run-drawer-tab-children"));
    await user.click(screen.getByTestId(`run-drawer-child-${childId}`));
    // onSelectRunId(childId) → setSelectedRunId(childId) → drawer data-run-id updates
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      childId,
    );
  });

});