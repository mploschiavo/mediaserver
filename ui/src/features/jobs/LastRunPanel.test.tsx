import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const latestState = vi.hoisted(() => ({
  data: null as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const detailState = vi.hoisted(() => ({
  data: null as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useLatestRunForJob: () => ({
      data: latestState.data,
      isLoading: latestState.isLoading,
      error: latestState.error,
    }),
    useRun: () => ({
      data: detailState.data,
      isLoading: detailState.isLoading,
      error: detailState.error,
    }),
  };
});

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    search,
    children,
    ...rest
  }: {
    to: string;
    search?: Record<string, unknown>;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => {
    const qs = search
      ? `?${Object.entries(search)
          .filter(([, v]) => v !== undefined && v !== "")
          .map(
            ([k, v]) =>
              `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`,
          )
          .join("&")}`
      : "";
    return (
      <a href={`${to}${qs}`} {...rest}>
        {children}
      </a>
    );
  },
}));

import { LastRunPanel } from "./LastRunPanel";
import type {
  RunRecordShape,
  RunRecordWithChildrenShape,
} from "./hooks";

function makeRun(overrides: Partial<RunRecordShape> = {}): RunRecordShape {
  return {
    run_id: "01J5ABCDEF1234567890MNPQRS",
    job_name: "scan-completed-downloads",
    status: "ok",
    started_at: 1_700_000_000,
    triggered_by: "cron",
    attempts: 1,
    child_run_ids: [],
    elapsed: 1.2,
    completed_at: 1_700_000_001.2,
    ...overrides,
  };
}

function reset() {
  latestState.data = null;
  latestState.isLoading = false;
  latestState.error = null;
  detailState.data = null;
  detailState.isLoading = false;
  detailState.error = null;
}

describe("LastRunPanel", () => {
  it("shows a skeleton while loading", () => {
    reset();
    latestState.isLoading = true;
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(
      screen.getByTestId("last-run-panel-loading"),
    ).toBeInTheDocument();
  });

  it("shows an error alert when the latest-run fetch fails", () => {
    reset();
    latestState.error = new Error("network down");
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    const err = screen.getByTestId("last-run-panel-error");
    expect(err).toHaveTextContent(/network down/);
    expect(err).toHaveAttribute("role", "alert");
  });

  it("shows an empty state when no run has been recorded yet", () => {
    reset();
    latestState.data = null;
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(
      screen.getByTestId("last-run-panel-empty"),
    ).toBeInTheDocument();
  });

  it("renders run id, status badge and triggered_by chip for a populated run", () => {
    reset();
    latestState.data = makeRun({ status: "ok", triggered_by: "manual" });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(
      screen.getByTestId("last-run-id"),
    ).toHaveTextContent("01J5ABCDEF1234567890MNPQRS");
    expect(screen.getByTestId("run-status-ok")).toBeInTheDocument();
    expect(screen.getByText(/manual/)).toBeInTheDocument();
  });

  it("renders the actor when present", () => {
    reset();
    latestState.data = makeRun({ triggered_by: "manual", actor: "alice" });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(
      screen.getByText(/manual\s*·\s*alice/),
    ).toBeInTheDocument();
  });

  it("shows attempts ×N badge only when attempts > 1", () => {
    reset();
    latestState.data = makeRun({ attempts: 1 });
    const { rerender } = renderWithProviders(
      <LastRunPanel jobName="scan-completed-downloads" />,
    );
    expect(screen.queryByTestId("last-run-attempts")).toBeNull();
    latestState.data = makeRun({ attempts: 3 });
    rerender(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(screen.getByTestId("last-run-attempts")).toHaveTextContent("×3");
  });

  it("shows live elapsed when status is running", () => {
    reset();
    latestState.data = makeRun({
      status: "running",
      completed_at: undefined,
      elapsed: undefined,
      // 30s ago in epoch seconds
      started_at: Date.now() / 1000 - 30,
    });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(
      screen.getByText(/still running/),
    ).toBeInTheDocument();
    // Running badge surfaces the running status icon variant.
    expect(screen.getByTestId("run-status-running")).toBeInTheDocument();
  });

  it("renders a collapsible error block with the error text", () => {
    reset();
    latestState.data = makeRun({
      status: "error",
      error: "boom: connection refused",
    });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    const block = screen.getByTestId("last-run-error");
    expect(block).toBeInTheDocument();
    expect(
      screen.getByTestId("last-run-error-text"),
    ).toHaveTextContent("boom: connection refused");
  });

  it("renders a collapsible stdout block with the tail length surfaced", () => {
    reset();
    latestState.data = makeRun({
      stdout_tail: "alpha\nbeta\ngamma",
    });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    const block = screen.getByTestId("last-run-stdout");
    expect(block).toBeInTheDocument();
    expect(block).toHaveTextContent(/last 16 chars/);
  });

  it("renders a 'View logs for this run' deep-link when log_anchor is set", () => {
    reset();
    latestState.data = makeRun({
      log_anchor: {
        source: "controller",
        since_iso: "2026-01-01T00:00:00.000Z",
        action: "scan-completed-downloads",
      },
    });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    // `Button asChild` merges props onto the child <a>, so the testid
    // lands on the anchor itself.
    const link = screen.getByTestId("last-run-view-logs") as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toContain("/logs?");
    expect(link.getAttribute("href")).toContain("service=controller");
    expect(link.getAttribute("href")).toContain(
      "action=scan-completed-downloads",
    );
    expect(link.getAttribute("href")).toContain(
      `since=${encodeURIComponent("2026-01-01T00:00:00.000Z")}`,
    );
  });

  it("does NOT render the view-logs button when log_anchor is absent", () => {
    reset();
    latestState.data = makeRun({ log_anchor: undefined });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(screen.queryByTestId("last-run-view-logs")).toBeNull();
  });

  it("renders child runs when child_run_ids is non-empty", () => {
    reset();
    const child: RunRecordShape = makeRun({
      run_id: "01J5CHILDAAAAAAAAAAAAAAAAAA",
      job_name: "discover-api-keys",
      status: "ok",
      elapsed: 0.05,
    });
    const parent = makeRun({
      run_id: "01J5PARENTAAAAAAAAAAAAAAAAA",
      child_run_ids: [child.run_id],
    });
    latestState.data = parent;
    detailState.data = {
      ...parent,
      children: [child],
    } as RunRecordWithChildrenShape;
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(screen.getByTestId("last-run-children")).toBeInTheDocument();
    expect(
      screen.getByTestId(`last-run-child-${child.run_id}`),
    ).toHaveTextContent("discover-api-keys");
  });

  it("renders a child-section skeleton while child detail loads", () => {
    reset();
    const parent = makeRun({
      child_run_ids: ["01J5CHILDAAAAAAAAAAAAAAAAAA"],
    });
    latestState.data = parent;
    detailState.isLoading = true;
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(
      screen.getByTestId("last-run-children-loading"),
    ).toBeInTheDocument();
  });

  it("renders nothing for the children section when there are no child_run_ids", () => {
    reset();
    latestState.data = makeRun({ child_run_ids: [] });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(screen.queryByTestId("last-run-children")).toBeNull();
    expect(screen.queryByTestId("last-run-children-loading")).toBeNull();
  });

  it("falls back to the unknown status icon for an unrecognised status", () => {
    reset();
    latestState.data = makeRun({ status: "weird-future-status" });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(
      screen.getByTestId("run-status-weird-future-status"),
    ).toBeInTheDocument();
  });

  it("renders the cancelled status badge variant", () => {
    reset();
    latestState.data = makeRun({ status: "cancelled" });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(screen.getByTestId("run-status-cancelled")).toBeInTheDocument();
  });

  it("renders the timeout status badge variant", () => {
    reset();
    latestState.data = makeRun({ status: "timeout" });
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(screen.getByTestId("run-status-timeout")).toBeInTheDocument();
  });

  it("opens and closes the run drawer when a child row is clicked / closed", () => {
    reset();
    const childId = "01J5CHILDAAAAAAAAAAAAAAAAAA";
    const child: RunRecordShape = makeRun({
      run_id: childId,
      job_name: "discover-api-keys",
    });
    const parent = makeRun({ child_run_ids: [child.run_id] });
    latestState.data = parent;
    detailState.data = {
      ...parent,
      children: [child],
    } as RunRecordWithChildrenShape;
    renderWithProviders(<LastRunPanel jobName="scan-completed-downloads" />);
    expect(screen.queryByTestId("run-drawer")).toBeNull();
    fireEvent.click(screen.getByTestId(`last-run-child-button-${childId}`));
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      childId,
    );
    // Closing the drawer fires the onClose arrow which resets state
    // back to null. Vaul keeps the content node around during exit
    // animation in jsdom so we don't assert removal — the data-run-id
    // attribute, however, reflects the live state immediately.
    fireEvent.click(screen.getByTestId("run-drawer-close"));
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      "",
    );
  });
});
