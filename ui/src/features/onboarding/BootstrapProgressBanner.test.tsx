import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const statusState = vi.hoisted(() => ({
  data: undefined as unknown,
  isError: false,
}));
const runningState = vi.hoisted(() => ({
  data: undefined as unknown,
}));
const jobsState = vi.hoisted(() => ({
  data: undefined as unknown,
}));
const retryMutation = vi.hoisted(() => ({
  mutateAsync: vi.fn().mockResolvedValue({ task_id: "t-1" }),
  isPending: false,
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  );
  return {
    ...actual,
    useQuery: () => ({
      data: statusState.data,
      isLoading: false,
      isError: statusState.isError,
      error: null,
    }),
  };
});

vi.mock("@/features/jobs/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/features/jobs/hooks")>(
    "@/features/jobs/hooks",
  );
  return {
    ...actual,
    useJobsRunning: () => ({
      data: runningState.data,
      isLoading: false,
      error: null,
    }),
    useJobs: () => ({
      data: jobsState.data,
      isLoading: false,
      error: null,
    }),
    useRunAction: () => retryMutation,
  };
});

vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>(
    "@tanstack/react-router",
  );
  return {
    ...actual,
    Link: (props: { to: string; children: React.ReactNode; className?: string }) => (
      <a href={props.to} className={props.className}>
        {props.children}
      </a>
    ),
  };
});

import { BootstrapProgressBanner } from "./BootstrapProgressBanner";
import { SetupStatus } from "./setupStatusConstants";

function reset() {
  statusState.data = undefined;
  statusState.isError = false;
  runningState.data = { tree: [] };
  jobsState.data = { history: [] };
  retryMutation.mutateAsync = vi.fn().mockResolvedValue({ task_id: "t-1" });
  retryMutation.isPending = false;
  if (typeof window !== "undefined") {
    window.localStorage.clear();
  }
}

describe("BootstrapProgressBanner", () => {
  it("renders a warming-up state when controller is unreachable", () => {
    reset();
    statusState.data = undefined;
    statusState.isError = true;
    renderWithProviders(<BootstrapProgressBanner />);
    expect(screen.getByTestId("bootstrap-progress-banner")).toBeInTheDocument();
    expect(screen.getByText(/reaching the controller/i)).toBeInTheDocument();
  });

  it("renders queued copy while waiting for bootstrap pickup", () => {
    reset();
    statusState.data = {
      initial_bootstrap_done: false,
      phase: SetupStatus.Starting,
    };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(screen.getByTestId("bootstrap-progress-banner")).toBeInTheDocument();
    expect(screen.getByText(/waiting for the controller/i)).toBeInTheDocument();
  });

  it("renders the live bootstrap path + step summary from the running tree", () => {
    reset();
    statusState.data = { initial_bootstrap_done: false, phase: SetupStatus.Running };
    runningState.data = {
      tree: [
        {
          run_id: "r1",
          job_name: "bootstrap",
          status: SetupStatus.Running,
          started_at: 100,
          elapsed_seconds: 30,
          triggered_by: "manual",
          actor: "",
          parent_run_id: "",
          batch_id: "",
          children: [
            {
              run_id: "r2",
              job_name: "discover_api_keys",
              status: SetupStatus.Running,
              started_at: 110,
              elapsed_seconds: 20,
              triggered_by: "parent",
              actor: "",
              parent_run_id: "r1",
              batch_id: "r1",
              children: [],
            },
          ],
        },
      ],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(
      screen.getByTestId("bootstrap-progress-banner-timeline"),
    ).toBeInTheDocument();
    expect(screen.getByText(/discovering api keys/i)).toBeInTheDocument();
  });

  it("falls back to current_action when the running tree is empty", () => {
    reset();
    statusState.data = {
      initial_bootstrap_done: false,
      phase: SetupStatus.Running,
      current_action: {
        id: "a-12",
        name: "configure_sonarr",
        status: SetupStatus.Running,
        started_at: 1,
        elapsed_seconds: 12,
      },
      phases_completed: ["preflight"],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(
      screen.getByTestId("bootstrap-progress-banner-description"),
    ).toHaveTextContent(/configuring sonarr/i);
    expect(
      screen.getByTestId("bootstrap-progress-banner-timeline"),
    ).toBeInTheDocument();
  });

  it("renders a failed terminal state with retry CTA", () => {
    reset();
    statusState.data = {
      initial_bootstrap_done: true,
      phase: SetupStatus.Error,
    };
    jobsState.data = {
      history: [
        { jobs: { bootstrap: { status: SetupStatus.Error } }, errors: 2 },
      ],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(screen.getByText(/needs attention/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /retry setup/i }),
    ).toBeInTheDocument();
  });

  it("renders the celebration state with next-steps CTAs when ready", () => {
    reset();
    statusState.data = {
      initial_bootstrap_done: true,
      phase: SetupStatus.Complete,
    };
    jobsState.data = {
      history: [
        { jobs: { bootstrap: { status: SetupStatus.Ok } }, errors: 0 },
      ],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(
      screen.getByTestId("bootstrap-progress-banner-title"),
    ).toHaveTextContent(/ready/i);
    expect(screen.getByRole("link", { name: /open apps/i })).toHaveAttribute(
      "href",
      "/apps",
    );
  });
});
