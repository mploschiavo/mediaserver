import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const runsState = vi.hoisted(() => ({
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
      data: null,
      isLoading: false,
      error: null,
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
});
