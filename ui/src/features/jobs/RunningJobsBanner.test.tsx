import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const runningState = vi.hoisted(() => ({
  data: { running: [], count: 0 } as unknown,
}));
const statusState = vi.hoisted(() => ({
  data: { initial_bootstrap_done: false } as
    | { initial_bootstrap_done?: boolean }
    | undefined,
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  );
  return {
    ...actual,
    useQuery: (opts: { queryKey: readonly unknown[] }) => {
      const key = Array.isArray(opts?.queryKey) ? opts.queryKey : [];
      if (key[0] === "controller" && key[1] === "status") {
        return { data: statusState.data };
      }
      return { data: runningState.data };
    },
    useMutation: () => ({ mutate: vi.fn(), isPending: false }),
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  };
});

import { RunningJobsBanner } from "./RunningJobsBanner";

describe("RunningJobsBanner", () => {
  it("hides bootstrap jobs during the first-run window (setup hero card owns that story)", () => {
    statusState.data = { initial_bootstrap_done: false };
    runningState.data = {
      count: 1,
      running: [
        {
          id: "bootstrap-1",
          name: "bootstrap",
          kind: "action",
          elapsed_seconds: 5,
        },
      ],
    };

    renderWithProviders(<RunningJobsBanner />);

    expect(screen.queryByTestId("running-jobs-banner")).not.toBeInTheDocument();
  });

  it("surfaces bootstrap re-runs after first-run completes", () => {
    statusState.data = { initial_bootstrap_done: true };
    runningState.data = {
      count: 1,
      running: [
        {
          id: "bootstrap-2",
          name: "bootstrap",
          kind: "action",
          elapsed_seconds: 8,
        },
      ],
    };

    renderWithProviders(<RunningJobsBanner />);

    expect(screen.getByTestId("running-jobs-banner")).toBeInTheDocument();
    expect(screen.getByText(/bootstrap/i)).toBeInTheDocument();
  });

  it("still renders non-bootstrap jobs", () => {
    statusState.data = { initial_bootstrap_done: false };
    runningState.data = {
      count: 1,
      running: [
        {
          id: "guardrails-1",
          name: "guardrails:evaluate",
          kind: "action",
          elapsed_seconds: 5,
        },
      ],
    };

    renderWithProviders(<RunningJobsBanner />);

    expect(screen.getByTestId("running-jobs-banner")).toBeInTheDocument();
    expect(screen.getByText(/guardrails:evaluate/i)).toBeInTheDocument();
  });
});
