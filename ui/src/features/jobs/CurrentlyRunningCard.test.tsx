import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const runningState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const cancelMutation = vi.hoisted(() => ({
  mutate: vi.fn(),
  isPending: false,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useJobsRunning: () => ({
      data: runningState.data,
      isLoading: runningState.isLoading,
      error: runningState.error,
    }),
    useCancelAction: () => cancelMutation,
    useRun: () => ({ data: null, isLoading: false, error: null }),
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

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { CurrentlyRunningCard } from "./CurrentlyRunningCard";
import type { RunningTreeNodeShape } from "./hooks";

function makeNode(
  overrides: Partial<RunningTreeNodeShape> = {},
): RunningTreeNodeShape {
  return {
    run_id: "01J5RUN0000000000000000001",
    job_name: "scan",
    status: "running",
    started_at: 1_700_000_000,
    elapsed_seconds: 12.5,
    triggered_by: "cron",
    actor: "",
    parent_run_id: "",
    batch_id: "",
    children: [],
    ...overrides,
  };
}

function reset() {
  runningState.data = undefined;
  runningState.isLoading = false;
  runningState.error = null;
  cancelMutation.mutate = vi.fn();
  cancelMutation.isPending = false;
}

describe("CurrentlyRunningCard", () => {
  it("renders nothing while the running query is loading", () => {
    reset();
    runningState.isLoading = true;
    renderWithProviders(<CurrentlyRunningCard />);
    expect(screen.queryByTestId("currently-running-card")).toBeNull();
  });

  it("renders nothing when the tree is empty", () => {
    reset();
    runningState.data = { running: [], count: 0, tree: [] };
    renderWithProviders(<CurrentlyRunningCard />);
    expect(screen.queryByTestId("currently-running-card")).toBeNull();
  });

  it("renders one node per top-level running record", () => {
    reset();
    runningState.data = {
      running: [],
      count: 2,
      tree: [
        makeNode({ run_id: "01J5A", job_name: "scan" }),
        makeNode({ run_id: "01J5B", job_name: "configure-arr" }),
      ],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    expect(screen.getByTestId("currently-running-card")).toBeInTheDocument();
    expect(screen.getByTestId("running-node-01J5A")).toHaveTextContent(/scan/);
    expect(screen.getByTestId("running-node-01J5B")).toHaveTextContent(
      /configure-arr/,
    );
    expect(screen.getByTestId("currently-running-count")).toHaveTextContent(
      "2",
    );
  });

  it("renders nested children indented under their parent", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [
        makeNode({
          run_id: "01J5PARENT0000000000000",
          job_name: "bootstrap",
          children: [
            makeNode({
              run_id: "01J5CHILD00000000000000",
              job_name: "discover-api-keys",
              parent_run_id: "01J5PARENT0000000000000",
            }),
          ],
        }),
      ],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    const child = screen.getByTestId("running-node-01J5CHILD00000000000000");
    expect(child).toHaveAttribute("data-depth", "1");
    expect(child).toHaveTextContent(/discover-api-keys/);
  });

  it("renders the running glyph for status=running", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5A", status: "running" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    expect(screen.getByTestId("running-node-glyph-01J5A")).toHaveTextContent(
      "▶",
    );
  });

  it("falls back to the pending glyph for an unknown status", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5A", status: "future-status" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    expect(screen.getByTestId("running-node-glyph-01J5A")).toHaveTextContent(
      "⏵",
    );
  });

  it("opens the run drawer when a node button is clicked", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5RUN0000000000000000001" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    expect(screen.queryByTestId("run-drawer")).toBeNull();
    fireEvent.click(
      screen.getByTestId("running-node-button-01J5RUN0000000000000000001"),
    );
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      "01J5RUN0000000000000000001",
    );
  });

  it("invokes the cancel mutation only on top-level Cancel buttons", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [
        makeNode({
          run_id: "01J5PARENT0000000000000",
          children: [
            makeNode({
              run_id: "01J5CHILD00000000000000",
              parent_run_id: "01J5PARENT0000000000000",
            }),
          ],
        }),
      ],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    // Top-level node has its Cancel button.
    expect(
      screen.getByTestId("running-node-cancel-01J5PARENT0000000000000"),
    ).toBeInTheDocument();
    // Nested child does NOT — design only puts cancel at the
    // top-level row (depth=0). A per-step cancel is a follow-up.
    expect(
      screen.queryByTestId("running-node-cancel-01J5CHILD00000000000000"),
    ).toBeNull();
    fireEvent.click(
      screen.getByTestId("running-node-cancel-01J5PARENT0000000000000"),
    );
    expect(cancelMutation.mutate).toHaveBeenCalled();
  });

  it("does not open the drawer when the cancel button is clicked", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5RUN0000000000000000001" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    fireEvent.click(
      screen.getByTestId("running-node-cancel-01J5RUN0000000000000000001"),
    );
    // The cancel button is a SIBLING of the row-open button, not
    // nested — clicking Cancel doesn't fire the row's onClick at
    // all so the drawer stays closed.
    expect(screen.queryByTestId("run-drawer")).toBeNull();
  });

  it("renders elapsed seconds via formatElapsed", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5A", elapsed_seconds: 90 })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    // formatElapsed renders ``1m 30s`` for 90s — assertion via
    // textContent so a future formatter rewrite still passes if
    // the rough shape stays.
    const elapsed = screen.getByTestId("running-node-elapsed-01J5A");
    expect(elapsed.textContent).toMatch(/1m\s*30s|90/);
  });

  it("toasts success when cancel.mutate fires its onSuccess callback", async () => {
    reset();
    const { toast } = await import("sonner");
    cancelMutation.mutate = vi.fn(
      (
        _vars: unknown,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onSuccess?.();
      },
    );
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5A" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    fireEvent.click(screen.getByTestId("running-node-cancel-01J5A"));
    expect(toast.success).toHaveBeenCalledWith("Cancel signal sent");
  });

  it("toasts error text when cancel.mutate fires its onError callback", async () => {
    reset();
    const { toast } = await import("sonner");
    cancelMutation.mutate = vi.fn(
      (
        _vars: unknown,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onError?.(new Error("boom"));
      },
    );
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5A" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    fireEvent.click(screen.getByTestId("running-node-cancel-01J5A"));
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("boom"),
    );
  });

  it("disables the cancel button while the mutation is pending", () => {
    reset();
    cancelMutation.isPending = true;
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5A" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    expect(screen.getByTestId("running-node-cancel-01J5A")).toBeDisabled();
  });

  it("closes the drawer via its close button (resets selectedRunId)", () => {
    reset();
    runningState.data = {
      running: [],
      count: 1,
      tree: [makeNode({ run_id: "01J5RUN0000000000000000001" })],
    };
    renderWithProviders(<CurrentlyRunningCard />);
    fireEvent.click(
      screen.getByTestId("running-node-button-01J5RUN0000000000000000001"),
    );
    fireEvent.click(screen.getByTestId("run-drawer-close"));
    expect(screen.getByTestId("run-drawer")).toHaveAttribute(
      "data-run-id",
      "",
    );
  });
});
