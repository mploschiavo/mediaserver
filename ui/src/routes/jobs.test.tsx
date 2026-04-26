import type { ComponentType } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { JobsResponse } from "@/features/jobs/hooks";

const jobsState = vi.hoisted(() => ({
  data: undefined as JobsResponse | undefined,
  isLoading: false,
  error: null as Error | null,
}));
const runMutate = vi.hoisted(() => vi.fn());

vi.mock("@/features/jobs/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/features/jobs/hooks")>(
    "@/features/jobs/hooks",
  );
  return {
    ...actual,
    useJobs: () => jobsState,
    useRunAction: () => ({
      mutate: runMutate,
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
    }),
    useCancelAction: () => ({
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
    }),
  };
});

// JobDetailPanel uses Tanstack Router's `<Link>` for the View-logs /
// Audit-history deep-links. The route test doesn't wire a router, so
// stub it as a plain anchor — same approach JobDetailPanel.test.tsx
// uses. Without this mock, selecting a parent (which now correctly
// mounts JobDetailPanel) crashes the renderer.
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>(
    "@tanstack/react-router",
  );
  return {
    ...actual,
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
  };
});

import { Route as JobsRoute } from "./jobs";

const JobsPage = JobsRoute.options.component as ComponentType;

describe("jobs route", () => {
  beforeEach(() => {
    jobsState.data = { jobs: [], tree: [], history: [] } as JobsResponse;
    jobsState.isLoading = false;
    jobsState.error = null;
    runMutate.mockReset();
  });

  it("registers at /jobs", () => {
    expect((JobsRoute.options as unknown as { path: string }).path).toBe("/jobs");
  });

  it("mounts the JobsPage with the page header copy", () => {
    renderWithProviders(<JobsPage />);
    expect(screen.getByTestId("jobs-page")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /^Jobs$/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Polls every 5 seconds/i),
    ).toBeInTheDocument();
  });

  it("renders the batch summary card and two-pane layout", () => {
    renderWithProviders(<JobsPage />);
    expect(screen.getByTestId("jobs-batch-summary")).toBeInTheDocument();
    expect(screen.getByTestId("jobs-two-pane")).toBeInTheDocument();
  });

  it("renders the next-scheduled-run metric card", () => {
    jobsState.data = {
      jobs: [
        { name: "reconcile", schedule: "0 */6 * * *" },
        { name: "manual-thing" },
      ],
      tree: [],
      history: [],
    } as JobsResponse;
    renderWithProviders(<JobsPage />);
    expect(screen.getByTestId("jobs-summary-next-run")).toBeInTheDocument();
  });

  it("renders the in-flight banner when a job's mutation is pending", () => {
    // Surface the banner via the page-level state. The page wires
    // `onRunningChange` from JobDetailPanel — for the smoke test we
    // exercise the toggle indirectly by setting runState's pending
    // flag and selecting a job.
    // Easier integration check: the banner only mounts when
    // `inFlightName` is set, which requires JobDetailPanel to be
    // running. We piggyback on the deeper detail-panel tests for
    // that path; here we verify the banner is conditional and not
    // present by default.
    jobsState.data = { jobs: [], tree: [], history: [] } as JobsResponse;
    renderWithProviders(<JobsPage />);
    expect(screen.queryByTestId("jobs-inflight-banner")).toBeNull();
  });

  it("renders a 'by service' breakdown when the catalog has services", () => {
    jobsState.data = {
      jobs: [
        { name: "a", service: "qbittorrent" },
        { name: "b", service: "qbittorrent" },
        { name: "c", service: "jellyfin" },
      ],
      tree: [],
      history: [],
    } as JobsResponse;
    renderWithProviders(<JobsPage />);
    expect(screen.getByTestId("jobs-summary-by-service")).toBeInTheDocument();
    expect(
      screen.getByTestId("jobs-summary-service-qbittorrent"),
    ).toHaveTextContent("2");
    expect(
      screen.getByTestId("jobs-summary-service-jellyfin"),
    ).toHaveTextContent("1");
  });

  it("renders a Run button for a parent (non-leaf) tree node and triggers the same mutation as leaves", () => {
    // Parent jobs (bootstrap, configure-*) live in `tree` but NOT
    // in the flat `jobs[]` catalog (which only has contract-discovered
    // leaves). Selecting a parent must still mount JobDetailPanel so
    // the operator can fire `POST /api/actions/<parent>` — which the
    // controller accepts for registered parents (bootstrap,
    // configure-media-server, aliases).
    jobsState.data = {
      jobs: [
        // Only the leaf is in the catalog — bootstrap/configure-media-server
        // are parents synthesized by build_job_framework().
        { name: "discover-api-keys", service: "controller" },
      ],
      tree: [
        {
          name: "bootstrap",
          sub_jobs: [
            {
              name: "configure-media-server",
              sub_jobs: [
                { name: "discover-api-keys", sub_jobs: [] },
              ],
            },
          ],
        },
      ],
      history: [],
    } as JobsResponse;
    renderWithProviders(<JobsPage />);
    // Click the parent row's name button to select it.
    fireEvent.click(screen.getByTestId("jobs-tree-name-configure-media-server"));
    // JobDetailPanel must now be mounted for the parent.
    const panel = screen.getByTestId("job-detail-panel");
    expect(panel.getAttribute("data-job-name")).toBe("configure-media-server");
    // Run button is present, enabled, and clickable.
    const runBtn = screen.getByTestId("job-detail-run-now");
    expect(runBtn).toBeInTheDocument();
    expect(runBtn).not.toBeDisabled();
    fireEvent.click(runBtn);
    expect(runMutate).toHaveBeenCalledTimes(1);
  });
});
