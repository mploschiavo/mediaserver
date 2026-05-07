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
    statusState.data = { initial_bootstrap_done: false };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(screen.getByTestId("bootstrap-progress-banner")).toBeInTheDocument();
    expect(screen.getByText(/waiting for the controller/i)).toBeInTheDocument();
  });

  it("renders the live bootstrap path + step summary from the running tree", () => {
    reset();
    statusState.data = { initial_bootstrap_done: false };
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

  it("renders a failed terminal state with retry CTA", () => {
    reset();
    statusState.data = { initial_bootstrap_done: true };
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
    statusState.data = { initial_bootstrap_done: true };
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

  it("keeps the progress bar visible at 100% on Complete (no auto-hide)", () => {
    reset();
    statusState.data = { initial_bootstrap_done: true };
    jobsState.data = {
      history: [
        {
          ts: 1700000000,
          jobs: { bootstrap: { status: SetupStatus.Ok } },
          errors: 0,
        },
      ],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    const bar = screen.getByTestId("bootstrap-progress-banner-bar");
    expect(bar).toBeInTheDocument();
    expect(bar).toHaveAttribute("aria-valuenow", "100");
  });

  it("renders an explicit Close button on Complete; clicking dismisses the current run", async () => {
    const { userEvent } = await import("@testing-library/user-event");
    reset();
    statusState.data = { initial_bootstrap_done: true };
    jobsState.data = {
      history: [
        {
          ts: 1700000000,
          jobs: { bootstrap: { status: SetupStatus.Ok } },
          errors: 0,
        },
      ],
    };
    const { unmount } = renderWithProviders(<BootstrapProgressBanner />);
    const closeBtn = screen.getByTestId("bootstrap-progress-banner-close");
    expect(closeBtn).toBeInTheDocument();
    await userEvent.click(closeBtn);
    expect(
      screen.queryByTestId("bootstrap-progress-banner"),
    ).not.toBeInTheDocument();
    // Persisted to localStorage keyed on the run's ts.
    expect(window.localStorage.getItem("media-stack:bootstrap-dismissed-run"))
      .toBe("ts:1700000000");
    unmount();

    // A NEW bootstrap run (different ts) re-shows the banner because
    // the dismissal key no longer matches the current run.
    jobsState.data = {
      history: [
        {
          ts: 1700001000,
          jobs: { bootstrap: { status: SetupStatus.Ok } },
          errors: 0,
        },
      ],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(
      screen.getByTestId("bootstrap-progress-banner"),
    ).toBeInTheDocument();
  });

  it("dismisses Complete state via the Close button when only action_history identifies the run (legacy bootstrap path)", async () => {
    // Regression: the bootstrap action runs through the controller's
    // legacy ``action_trigger`` path, NOT the Job framework, so it
    // never appears in ``/api/jobs/running`` or ``/api/jobs?history``.
    // ``state.action_history`` (on /status) is the only durable
    // per-run identifier — the Close button must derive its key
    // from there or it's a no-op.
    const { userEvent } = await import("@testing-library/user-event");
    reset();
    statusState.data = {
      initial_bootstrap_done: true,
      action_history: [
        { id: "bootstrap-1", name: "bootstrap", status: "complete" },
      ],
    };
    jobsState.data = {
      // Empty Job-framework history — only the legacy path ran.
      history: [],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    const closeBtn = screen.getByTestId("bootstrap-progress-banner-close");
    await userEvent.click(closeBtn);
    expect(
      screen.queryByTestId("bootstrap-progress-banner"),
    ).not.toBeInTheDocument();
    // Persisted as action:<id> so a subsequent re-bootstrap (new
    // action id) re-shows the banner automatically.
    expect(window.localStorage.getItem("media-stack:bootstrap-dismissed-run"))
      .toBe("action:bootstrap-1");
  });

  it("does NOT render a Close button while bootstrap is Running", () => {
    reset();
    statusState.data = { initial_bootstrap_done: false };
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
          children: [],
        },
      ],
    };
    renderWithProviders(<BootstrapProgressBanner />);
    expect(
      screen.queryByTestId("bootstrap-progress-banner-close"),
    ).not.toBeInTheDocument();
  });
});
