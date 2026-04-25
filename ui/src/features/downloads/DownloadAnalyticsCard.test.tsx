import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const analyticsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useDownloadAnalytics: () => analyticsState,
  };
});

import { DownloadAnalyticsCard } from "./DownloadAnalyticsCard";

describe("DownloadAnalyticsCard", () => {
  beforeEach(() => {
    analyticsState.data = undefined;
    analyticsState.isLoading = false;
    analyticsState.error = null;
  });

  it("renders skeletons while loading", () => {
    analyticsState.isLoading = true;
    renderWithProviders(<DownloadAnalyticsCard />);
    expect(
      screen.getByTestId("download-analytics-loading"),
    ).toBeInTheDocument();
  });

  it("renders an error banner on failure", () => {
    analyticsState.error = new Error("offline");
    renderWithProviders(<DownloadAnalyticsCard />);
    expect(screen.getByTestId("download-analytics-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders totals when present", () => {
    analyticsState.data = {
      totals: { completed: 42, grabbed: 8, failed: 2 },
      series: [],
    };
    renderWithProviders(<DownloadAnalyticsCard />);
    const totals = screen.getByTestId("download-totals");
    expect(totals).toHaveTextContent("42");
    expect(totals).toHaveTextContent("8");
    expect(totals).toHaveTextContent("2");
  });

  it("renders the sparkline when there are series points", () => {
    analyticsState.data = {
      totals: { completed: 1 },
      series: [
        { ts: "2026-04-22T00:00:00Z", count: 3 },
        { ts: "2026-04-23T00:00:00Z", count: 7 },
        { ts: "2026-04-24T00:00:00Z", count: 5 },
      ],
    };
    renderWithProviders(<DownloadAnalyticsCard />);
    expect(
      screen.getByTestId("download-analytics-sparkline"),
    ).toBeInTheDocument();
  });

  it("renders the empty-trend message with no series points", () => {
    analyticsState.data = { totals: { completed: 0 }, series: [] };
    renderWithProviders(<DownloadAnalyticsCard />);
    expect(
      screen.getByTestId("download-analytics-empty"),
    ).toBeInTheDocument();
  });
});
