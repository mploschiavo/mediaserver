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
import type { RunRecordShape } from "./hooks";

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

  it("renders one DataTable row per run with status / job / trigger", () => {
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
    const row1 = screen.getByTestId(
      "run-history-row-01J5RUNAAA0000000000000001",
    );
    expect(row1).toHaveTextContent(/scan-completed-downloads/);
    expect(row1).toHaveTextContent(/cron/);
    const row2 = screen.getByTestId(
      "run-history-row-01J5RUNBBB0000000000000002",
    );
    expect(row2).toHaveAttribute("data-status", "error");
  });

  it("filters by the per-column job-name DataTable filter", () => {
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
    const input = screen.getByTestId("run-history-filter-job_name");
    fireEvent.change(input, { target: { value: "configure" } });
    expect(
      screen.queryByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    ).toBeNull();
    expect(
      screen.getByTestId("run-history-row-01J5RUNBBB0000000000000002"),
    ).toBeInTheDocument();
  });

  it("filters by the per-column status DataTable filter", () => {
    reset();
    runsState.data = [
      makeRun({ run_id: "01J5RUNAAA0000000000000001", status: "ok" }),
      makeRun({ run_id: "01J5RUNBBB0000000000000002", status: "error" }),
      makeRun({ run_id: "01J5RUNCCC0000000000000003", status: "skipped" }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    fireEvent.change(screen.getByTestId("run-history-filter-status"), {
      target: { value: "error" },
    });
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
    fireEvent.change(screen.getByTestId("run-history-filter-job_name"), {
      target: { value: "no-such-job" },
    });
    expect(screen.getByTestId("run-history-empty")).toHaveTextContent(
      /No runs match/,
    );
  });

  it("renders the unknown status as its own variant on the row data-attribute", () => {
    reset();
    runsState.data = [
      makeRun({ run_id: "01J5RUNAAA0000000000000001", status: "future-status" }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(
      screen.getByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    ).toHaveAttribute("data-status", "future-status");
  });

  it("opens the run drawer when a row is clicked (DataTable onRowClick)", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5RUNAAA0000000000000001" })];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.queryByTestId("run-drawer")).toBeNull();
    fireEvent.click(
      screen.getByTestId("run-history-row-01J5RUNAAA0000000000000001"),
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
      screen.getByTestId("run-history-row-01J5RUNAAA0000000000000001"),
    );
    fireEvent.click(screen.getByTestId("run-drawer-close"));
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
    expect(screen.getByTestId("run-history-trigger-01J5BY")).toHaveTextContent(
      "via cron",
    );
    expect(screen.getByTestId("run-history-trigger-01J5BM")).toHaveTextContent(
      "via manual",
    );
  });

  it("renders a one-click Logs deep-link button on every row", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5L", job_name: "scan" })];
    renderWithProviders(<RunHistoryPanel />);
    const link = screen.getByTestId("run-history-logs-01J5L");
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/logs");
  });

  it("Logs link click does not bubble up and open the drawer", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5L", job_name: "scan" })];
    renderWithProviders(<RunHistoryPanel />);
    fireEvent.click(screen.getByTestId("run-history-logs-01J5L"));
    expect(screen.queryByTestId("run-drawer")).toBeNull();
  });

  it("renders 'under <parent>' secondary line when parent_job_name is set", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5CHILD",
        job_name: "discover-api-keys",
        parent_run_id: "01J5PARENT",
        parent_job_name: "bootstrap",
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-parent-01J5CHILD")).toHaveTextContent(
      /under bootstrap/,
    );
  });

  it("does not render the parent line when parent_job_name is absent", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5ROOT",
        parent_run_id: undefined,
        parent_job_name: undefined,
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(
      screen.queryByTestId("run-history-parent-01J5ROOT"),
    ).toBeNull();
  });

  it("does not tint a row when anomaly_score is absent or null", () => {
    reset();
    runsState.data = [
      makeRun({ run_id: "01J5NORM", anomaly_score: null }),
      makeRun({ run_id: "01J5NRM2" }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(
      screen.getByTestId("run-history-row-01J5NORM"),
    ).not.toHaveAttribute("data-tone");
    expect(
      screen.getByTestId("run-history-row-01J5NRM2"),
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

  it("does not tint a row for negative z-scores", () => {
    reset();
    runsState.data = [makeRun({ run_id: "01J5FAST", anomaly_score: -1.5 })];
    renderWithProviders(<RunHistoryPanel />);
    expect(
      screen.getByTestId("run-history-row-01J5FAST"),
    ).not.toHaveAttribute("data-tone");
  });

  it("flags rows with parent + child indicators on data-attributes", () => {
    reset();
    runsState.data = [
      makeRun({
        run_id: "01J5PCHILD",
        parent_run_id: "01J5GP",
        child_run_ids: ["a", "b", "c"],
      }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    const row = screen.getByTestId("run-history-row-01J5PCHILD");
    expect(row).toHaveAttribute("data-has-parent", "true");
    expect(row).toHaveAttribute("data-child-count", "3");
  });

  it("drills into a child run when the drawer's onSelectRunId fires", async () => {
    reset();
    const childId = "01J5CHILDDRILL00000000000";
    runsState.data = [
      makeRun({ run_id: "01J5PARENTDRILL000000000" }),
    ];
    runDetailState.data = {
      run_id: "01J5PARENTDRILL000000000",
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
          job_name: "child-job",
          status: "ok",
          started_at: 1_700_000_010,
          triggered_by: "parent",
          attempts: 1,
          child_run_ids: [],
          elapsed: 0.3,
        },
      ],
    };
    const user = userEvent.setup();
    renderWithProviders(<RunHistoryPanel />);
    // Open the drawer by clicking the parent row.
    fireEvent.click(
      screen.getByTestId("run-history-row-01J5PARENTDRILL000000000"),
    );
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      "01J5PARENTDRILL000000000",
    );
    // Radix Tabs need pointer events to activate — userEvent gets
    // it right where fireEvent.click silently no-ops.
    await user.click(screen.getByTestId("run-drawer-tab-children"));
    await user.click(screen.getByTestId(`run-drawer-child-${childId}`));
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      childId,
    );
  });

  it("renders cancelled and timeout statuses on data-status", () => {
    reset();
    runsState.data = [
      makeRun({ run_id: "01J5CAN", status: "cancelled" }),
      makeRun({ run_id: "01J5TMO", status: "timeout" }),
    ];
    renderWithProviders(<RunHistoryPanel />);
    expect(screen.getByTestId("run-history-row-01J5CAN")).toHaveAttribute(
      "data-status",
      "cancelled",
    );
    expect(screen.getByTestId("run-history-row-01J5TMO")).toHaveAttribute(
      "data-status",
      "timeout",
    );
  });
});
