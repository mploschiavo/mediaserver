import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const runState = vi.hoisted(() => ({
  data: null as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useRun: () => ({
      data: runState.data,
      isLoading: runState.isLoading,
      error: runState.error,
    }),
  };
});

// Tanstack Link needs router context; tests don't care about routing,
// so render Link as a passthrough <a> with a stable href for assertions.
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>(
    "@tanstack/react-router",
  );
  return {
    ...actual,
    Link: ({
      children,
      to,
      search,
      ...rest
    }: {
      children: React.ReactNode;
      to?: string;
      search?: Record<string, unknown>;
      [key: string]: unknown;
    }) => (
      <a
        href={typeof to === "string" ? to : "#"}
        data-search={search ? JSON.stringify(search) : undefined}
        {...(rest as Record<string, unknown>)}
      >
        {children}
      </a>
    ),
  };
});

import { RunDrawer } from "./RunDrawer";
import type { RunRecordWithChildrenShape } from "./hooks";

function makeRun(
  overrides: Partial<RunRecordWithChildrenShape> = {},
): RunRecordWithChildrenShape {
  return {
    run_id: "01J5RUNAAA0000000000000001",
    job_name: "scan-completed-downloads",
    status: "ok",
    started_at: 1_700_000_000,
    triggered_by: "cron",
    attempts: 1,
    child_run_ids: [],
    elapsed: 1.2,
    children: [],
    ...overrides,
  };
}

function reset() {
  runState.data = null;
  runState.isLoading = false;
  runState.error = null;
}

describe("RunDrawer", () => {
  it("does not render the body when runId is null", () => {
    reset();
    renderWithProviders(
      <RunDrawer runId={null} onClose={() => {}} />,
    );
    expect(screen.queryByTestId("run-drawer-loading")).toBeNull();
    expect(screen.queryByTestId("run-drawer-summary")).toBeNull();
  });

  it("renders skeletons while loading", () => {
    reset();
    runState.isLoading = true;
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    expect(screen.getByTestId("run-drawer-loading")).toBeInTheDocument();
  });

  it("renders an error alert on fetch failure", () => {
    reset();
    runState.error = new Error("boom");
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    const err = screen.getByTestId("run-drawer-error");
    expect(err).toHaveTextContent(/boom/);
    expect(err).toHaveAttribute("role", "alert");
  });

  it("renders the empty state when the controller returns no record", () => {
    reset();
    runState.data = null;
    runState.isLoading = false;
    runState.error = null;
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    expect(screen.getByTestId("run-drawer-empty")).toBeInTheDocument();
  });

  it("renders status, parent, and triggered-by on the summary tab", () => {
    reset();
    runState.data = makeRun({
      status: "ok",
      parent_run_id: "01J5PARENT000000000000",
      triggered_by: "manual",
      actor: "alice",
    });
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    const status = screen.getByTestId("run-drawer-status");
    expect(status).toHaveAttribute("data-status", "ok");
    expect(screen.getByTestId("run-drawer-parent-id")).toHaveTextContent(
      "01J5PARENT000000000000",
    );
    expect(screen.getByTestId("run-drawer-summary")).toHaveTextContent(
      /manual.*alice/,
    );
  });

  it("renders the explain-failure stub only on terminal failures", () => {
    reset();
    runState.data = makeRun({ status: "error", error: "boom" });
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    const stub = screen.getByTestId("run-drawer-explain-stub");
    expect(stub).toBeDisabled();
  });

  it("hides the explain stub for successful runs", () => {
    reset();
    runState.data = makeRun({ status: "ok" });
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    expect(screen.queryByTestId("run-drawer-explain-stub")).toBeNull();
  });

  it("renders the error text in a <pre> when run.error is set", () => {
    reset();
    runState.data = makeRun({
      status: "error",
      error: "Stack overflow at line 42",
    });
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    expect(screen.getByTestId("run-drawer-error-text")).toHaveTextContent(
      /Stack overflow/,
    );
  });

  it("switches to the Output tab and renders stdout_tail + view-logs link", async () => {
    reset();
    runState.data = makeRun({
      stdout_tail: "[INFO] hello world\n",
      log_anchor: {
        source: "controller",
        since_iso: "2026-04-27T10:00:00Z",
        action: "scan-completed-downloads",
      },
    });
    const user = userEvent.setup();
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    await user.click(screen.getByTestId("run-drawer-tab-output"));
    expect(screen.getByTestId("run-drawer-stdout")).toHaveTextContent(
      /hello world/,
    );
    expect(screen.getByTestId("run-drawer-view-logs")).toBeInTheDocument();
  });

  it("renders the no-output state on the Output tab when stdout_tail is empty", async () => {
    reset();
    runState.data = makeRun({ stdout_tail: undefined });
    const user = userEvent.setup();
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    await user.click(screen.getByTestId("run-drawer-tab-output"));
    expect(screen.getByTestId("run-drawer-no-output")).toBeInTheDocument();
  });

  it("renders no-children state on the Children tab when none are inlined", async () => {
    reset();
    runState.data = makeRun({ children: [] });
    const user = userEvent.setup();
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    await user.click(screen.getByTestId("run-drawer-tab-children"));
    expect(screen.getByTestId("run-drawer-no-children")).toBeInTheDocument();
  });

  it("calls onSelectRunId when a child row is clicked", async () => {
    reset();
    const childId = "01J5CHILD0000000000000001";
    runState.data = makeRun({
      child_run_ids: [childId],
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
    });
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <RunDrawer
        runId="01J5RUNAAA0000000000000001"
        onClose={() => {}}
        onSelectRunId={onSelect}
      />,
    );
    await user.click(screen.getByTestId("run-drawer-tab-children"));
    await user.click(screen.getByTestId(`run-drawer-child-${childId}`));
    expect(onSelect).toHaveBeenCalledWith(childId);
  });

  it("calls onClose when the close button is pressed", () => {
    reset();
    runState.data = makeRun();
    const onClose = vi.fn();
    renderWithProviders(
      <RunDrawer
        runId="01J5RUNAAA0000000000000001"
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("run-drawer-close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("calls onClose when the Vaul drawer signals open=false (Esc / overlay)", () => {
    reset();
    runState.data = makeRun();
    const onClose = vi.fn();
    renderWithProviders(
      <RunDrawer
        runId="01J5RUNAAA0000000000000001"
        onClose={onClose}
      />,
    );
    // Vaul listens for Escape on the drawer content; pressing Esc on
    // the overlay propagates to the same onOpenChange path.
    fireEvent.keyDown(screen.getByTestId("run-drawer"), {
      key: "Escape",
      code: "Escape",
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("renders the completed_at row when the run has settled", () => {
    reset();
    runState.data = makeRun({
      status: "ok",
      completed_at: 1_700_000_010,
    });
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    expect(screen.getByTestId("run-drawer-summary")).toHaveTextContent(
      /Completed/,
    );
  });

  it("shows the attempts count when the run retried", () => {
    reset();
    runState.data = makeRun({ attempts: 3 });
    renderWithProviders(
      <RunDrawer runId="01J5RUNAAA0000000000000001" onClose={() => {}} />,
    );
    expect(screen.getByTestId("run-drawer-summary")).toHaveTextContent(/×3/);
  });
});
