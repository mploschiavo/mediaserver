import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { JobHistoryPanel } from "./JobHistoryPanel";
import type { JobHistoryEntry, JobMeta } from "./hooks";

function makeHistory(): readonly JobHistoryEntry[] {
  return [
    {
      ts: 1_700_000_000,
      elapsed: 1.5,
      ok: 2,
      skipped: 1,
      errors: 0,
      jobs: {
        "scan-completed-downloads": { status: "ok", elapsed: 1.2 },
        "configure-media-server": { status: "skipped", elapsed: 0 },
        "discover-api-keys": { status: "ok", elapsed: 0.05 },
      },
    },
    {
      ts: 1_699_999_000,
      elapsed: 0.5,
      ok: 1,
      skipped: 0,
      errors: 1,
      jobs: {
        "scan-completed-downloads": {
          status: "error",
          elapsed: 0.4,
          error: "boom",
        },
        "discover-api-keys": { status: "ok", elapsed: 0.1 },
      },
    },
  ];
}

describe("JobHistoryPanel", () => {
  it("renders a row per history entry", () => {
    renderWithProviders(<JobHistoryPanel history={makeHistory()} />);
    expect(screen.getByTestId("job-history-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("job-history-row-1")).toBeInTheDocument();
  });

  it("renders an empty state when there's no history", () => {
    renderWithProviders(<JobHistoryPanel history={[]} />);
    expect(screen.getByText(/No batch history yet/i)).toBeInTheDocument();
  });

  it("opens the drawer with a per-job breakdown when a row is clicked", () => {
    renderWithProviders(<JobHistoryPanel history={makeHistory()} />);
    fireEvent.click(screen.getByTestId("job-history-row-1"));
    // Breakdown table shown.
    expect(screen.getByTestId("job-history-breakdown")).toBeInTheDocument();
    // Per-job rows for that batch.
    expect(
      screen.getByTestId(
        "job-history-breakdown-row-scan-completed-downloads",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("job-history-breakdown-row-discover-api-keys"),
    ).toBeInTheDocument();
  });

  it("surfaces the per-job error message in the drawer", () => {
    renderWithProviders(<JobHistoryPanel history={makeHistory()} />);
    fireEvent.click(screen.getByTestId("job-history-row-1"));
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("renders the source badge on a row when entry.source is set", () => {
    const history: readonly JobHistoryEntry[] = [
      {
        ts: 1_700_000_000,
        elapsed: 0.5,
        ok: 1,
        skipped: 0,
        errors: 0,
        source: "cron",
        jobs: { scan: { status: "ok", elapsed: 0.5 } },
      },
    ];
    renderWithProviders(<JobHistoryPanel history={history} />);
    expect(screen.getByTestId("job-history-source-0")).toHaveTextContent(
      "cron",
    );
  });

  it("does NOT render the source badge when entry.source is absent", () => {
    renderWithProviders(<JobHistoryPanel history={makeHistory()} />);
    expect(screen.queryByTestId("job-history-source-0")).toBeNull();
  });

  it("prepends a service badge to each breakdown row when a catalog is supplied", () => {
    const catalog = new Map<string, JobMeta>([
      [
        "scan-completed-downloads",
        { name: "scan-completed-downloads", service: "qbittorrent" },
      ],
      [
        "configure-media-server",
        { name: "configure-media-server", service: "jellyfin" },
      ],
    ]);
    renderWithProviders(
      <JobHistoryPanel history={makeHistory()} catalog={catalog} />,
    );
    fireEvent.click(screen.getByTestId("job-history-row-0"));
    expect(
      screen.getByTestId(
        "job-history-breakdown-service-scan-completed-downloads",
      ),
    ).toHaveTextContent("qbittorrent");
    expect(
      screen.getByTestId(
        "job-history-breakdown-service-configure-media-server",
      ),
    ).toHaveTextContent("jellyfin");
  });
});
