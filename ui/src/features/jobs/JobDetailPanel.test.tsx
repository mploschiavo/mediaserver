import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const runMutate = vi.hoisted(() => vi.fn());
const cancelMutate = vi.hoisted(() => vi.fn());
const runState = vi.hoisted(() => ({
  isPending: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useRunAction: () => ({
      mutate: runMutate,
      mutateAsync: vi.fn(),
      isPending: runState.isPending,
      error: runState.error,
    }),
    useCancelAction: () => ({
      mutate: cancelMutate,
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
    }),
  };
});

vi.mock("@tanstack/react-router", () => ({
  // Anchor stand-in so deep-link buttons render without a router.
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
            ([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`,
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

import { JobDetailPanel } from "./JobDetailPanel";
import type { JobHistoryEntry, JobMeta } from "./hooks";

function makeJob(overrides: Partial<JobMeta> = {}): JobMeta {
  return {
    name: "scan-completed-downloads",
    label: "Scan completed downloads",
    service: "qbittorrent",
    requires: ["media_server_api_key"],
    after: ["bootstrap"],
    non_blocking: false,
    max_attempts: 3,
    ...overrides,
  };
}

function makeHistory(): readonly JobHistoryEntry[] {
  return [
    {
      ts: 1_700_000_000,
      elapsed: 1.2,
      ok: 1,
      skipped: 0,
      errors: 0,
      jobs: {
        "scan-completed-downloads": { status: "ok", elapsed: 1.2 },
      },
    },
    {
      ts: 1_699_999_000,
      elapsed: 0.8,
      ok: 0,
      skipped: 1,
      errors: 0,
      jobs: {
        "scan-completed-downloads": { status: "skipped", elapsed: 0 },
      },
    },
  ];
}

describe("JobDetailPanel", () => {
  beforeEach(() => {
    runMutate.mockReset();
    cancelMutate.mockReset();
    runState.isPending = false;
    runState.error = null;
  });

  it("renders the dependency chips for requires and after", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("job-dep-chip-media_server_api_key"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("job-dep-chip-bootstrap")).toBeInTheDocument();
  });

  it("calls onReveal when a chip's Show button is clicked", () => {
    const onReveal = vi.fn();
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={onReveal}
      />,
    );
    fireEvent.click(screen.getByTestId("job-dep-reveal-bootstrap"));
    expect(onReveal).toHaveBeenCalledWith("bootstrap");
  });

  it("renders the last-runs table with relative timestamps and elapsed", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(screen.getByTestId("job-detail-runs-table")).toBeInTheDocument();
    // Two recorded runs in the fixture.
    const rows = screen
      .getByTestId("job-detail-runs-table")
      .querySelectorAll("tbody tr");
    expect(rows.length).toBe(2);
  });

  it("Run now button calls the mutation when no deps are unmet", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    const btn = screen.getByTestId("job-detail-run-now");
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(runMutate).toHaveBeenCalledTimes(1);
  });

  it("disables Run now when a required dep is in the unmet set", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set(["media_server_api_key"])}
        onReveal={vi.fn()}
      />,
    );
    const btn = screen.getByTestId("job-detail-run-now");
    expect(btn).toBeDisabled();
    expect(
      screen.getByTestId("job-detail-blocked-hint"),
    ).toBeInTheDocument();
  });

  it("Cancel is disabled until Run now flips the in-flight flag", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(screen.getByTestId("job-detail-cancel")).toBeDisabled();
    fireEvent.click(screen.getByTestId("job-detail-run-now"));
    // After click, running flag is set → Cancel is now enabled.
    expect(screen.getByTestId("job-detail-cancel")).not.toBeDisabled();
  });

  it("renders non-blocking and max-attempts pills when set", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob({ non_blocking: true, max_attempts: 5 })}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("job-detail-non-blocking"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("job-detail-max-attempts"),
    ).toBeInTheDocument();
  });

  it("falls back to a no-runs message when history has no entry for the job", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob({ name: "never-ran" })}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(screen.getByTestId("job-detail-no-runs")).toBeInTheDocument();
  });

  it("renders 'Manual / dependency-driven' when no schedule is present", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(screen.getByTestId("job-detail-schedule")).toHaveTextContent(
      /Manual \/ dependency-driven/,
    );
  });

  it("renders a 'Next run' line when the job has a cron schedule", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob({ schedule: "0 */6 * * *" })}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    const node = screen.getByTestId("job-detail-schedule");
    expect(node).toHaveTextContent(/Next run/);
    expect(node).toHaveTextContent(/0 \*\/6 \* \* \*/);
  });

  it("renders the View logs deep-link with service + filter params", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    // Button asChild merges the testid onto the Link's <a> via Radix
    // Slot, so the anchor is the testid'd element directly.
    const link = screen.getByTestId("job-detail-view-logs");
    expect(link.tagName.toLowerCase()).toBe("a");
    expect(link.getAttribute("href")).toContain("/logs?");
    expect(link.getAttribute("href")).toContain("service=controller");
    expect(link.getAttribute("href")).toContain(
      "filter=scan-completed-downloads",
    );
  });

  it("renders the Audit history deep-link with the action prefix", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    const link = screen.getByTestId("job-detail-audit-history");
    expect(link.tagName.toLowerCase()).toBe("a");
    expect(link.getAttribute("href")).toContain("/audit-log?");
    expect(link.getAttribute("href")).toContain(
      "action=job%3Ascan-completed-downloads",
    );
  });

  it("computes 'Required by' from the catalog client-side", () => {
    const catalog = new Map<string, JobMeta>([
      ["scan-completed-downloads", makeJob()],
      [
        "post-import-cleanup",
        {
          name: "post-import-cleanup",
          requires: ["scan-completed-downloads"],
        },
      ],
      [
        "trigger-rescan",
        {
          name: "trigger-rescan",
          after: ["scan-completed-downloads"],
        },
      ],
      [
        "unrelated",
        { name: "unrelated", requires: ["something-else"] },
      ],
    ]);
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
        catalog={catalog}
      />,
    );
    const list = screen.getByTestId("job-detail-required-by");
    expect(list).toBeInTheDocument();
    expect(
      screen.getByTestId("job-required-by-chip-post-import-cleanup"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("job-required-by-chip-trigger-rescan"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("job-required-by-chip-unrelated"),
    ).toBeNull();
  });

  it("renders the empty 'Required by' sentinel when nothing depends on the job", () => {
    const catalog = new Map<string, JobMeta>([
      ["scan-completed-downloads", makeJob()],
    ]);
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
        catalog={catalog}
      />,
    );
    expect(
      screen.getByTestId("job-detail-required-by-empty"),
    ).toBeInTheDocument();
  });

  it("surfaces 'Last green: …' when history has at least one ok run", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("job-detail-last-green"),
    ).toHaveTextContent(/Last green/);
  });

  it("surfaces 'Never green' when history has no ok run for the job", () => {
    const failOnly: readonly JobHistoryEntry[] = [
      {
        ts: 1_700_000_000,
        jobs: { "scan-completed-downloads": { status: "error" } },
      },
      {
        ts: 1_699_999_000,
        jobs: { "scan-completed-downloads": { status: "skipped" } },
      },
    ];
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={failOnly}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("job-detail-last-green"),
    ).toHaveTextContent(/Never green/);
  });

  it("renders a sparkline when at least 2 runs are recorded", () => {
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(screen.getByTestId("job-detail-sparkline")).toBeInTheDocument();
  });

  it("does NOT render the sparkline when only 1 run is recorded", () => {
    const single: readonly JobHistoryEntry[] = [
      {
        ts: 1_700_000_000,
        jobs: { "scan-completed-downloads": { status: "ok", elapsed: 1.0 } },
      },
    ];
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={single}
        unmet={new Set()}
        onReveal={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("job-detail-sparkline")).toBeNull();
  });

  it("notifies the parent when the running flag flips on Run-now", () => {
    const onRunningChange = vi.fn();
    renderWithProviders(
      <JobDetailPanel
        job={makeJob()}
        history={makeHistory()}
        unmet={new Set()}
        onReveal={vi.fn()}
        onRunningChange={onRunningChange}
      />,
    );
    fireEvent.click(screen.getByTestId("job-detail-run-now"));
    // First call sets parent to running=true. The on-mount reset
    // emits running=false; we only assert the meaningful flip.
    expect(onRunningChange).toHaveBeenCalledWith(true);
  });
});
