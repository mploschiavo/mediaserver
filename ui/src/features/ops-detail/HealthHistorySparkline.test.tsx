import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const historyState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useHealthHistory: () => historyState,
}));

import { HealthHistorySparkline } from "./HealthHistorySparkline";

describe("HealthHistorySparkline", () => {
  beforeEach(() => {
    historyState.data = undefined;
    historyState.isLoading = false;
    historyState.error = null;
  });

  it("renders skeleton while loading", () => {
    historyState.isLoading = true;
    renderWithProviders(<HealthHistorySparkline />);
    expect(screen.getByTestId("health-history-loading")).toBeInTheDocument();
  });

  it("renders the empty state when there is no history", () => {
    historyState.data = { history: [], period_hours: 0 };
    renderWithProviders(<HealthHistorySparkline />);
    expect(screen.getByTestId("health-history-empty")).toBeInTheDocument();
  });

  it("renders the error message when the query fails", () => {
    historyState.error = new Error("nope");
    renderWithProviders(<HealthHistorySparkline />);
    expect(screen.getByTestId("health-history-error")).toHaveTextContent(
      "nope",
    );
  });

  it("renders the SVG once samples are present", () => {
    const now = Math.floor(Date.now() / 1000);
    historyState.data = {
      history: [
        {
          ts: now - 120,
          services: {
            sonarr: { status: "ok" },
            radarr: { status: "ok" },
            lidarr: { status: "error" },
          },
        },
        {
          ts: now - 60,
          services: {
            sonarr: { status: "ok" },
            radarr: { status: "ok" },
            lidarr: { status: "ok" },
          },
        },
        {
          ts: now,
          services: {
            sonarr: { status: "ok" },
            radarr: { status: "ok" },
            lidarr: { status: "ok" },
          },
        },
      ],
      period_hours: 0.05,
    };
    renderWithProviders(<HealthHistorySparkline />);
    const svg = screen.getByTestId("health-history-svg");
    expect(svg).toBeInTheDocument();
    expect(svg.getAttribute("width")).toBe("240");
    expect(svg.getAttribute("height")).toBe("40");
    expect(screen.getByText(/Latest: 3\/3 services ok/)).toBeInTheDocument();
  });

  it("shows the tooltip when a sample is hovered", () => {
    const now = Math.floor(Date.now() / 1000);
    historyState.data = {
      history: [
        {
          ts: now - 60,
          services: {
            sonarr: { status: "ok" },
            radarr: { status: "ok" },
          },
        },
        {
          ts: now,
          services: {
            sonarr: { status: "ok" },
            radarr: { status: "error" },
          },
        },
      ],
    };
    renderWithProviders(<HealthHistorySparkline />);
    const hit = screen.getByTestId("spark-hit-1");
    fireEvent.mouseEnter(hit);
    const tip = screen.getByTestId("health-history-tooltip");
    expect(tip).toBeInTheDocument();
    expect(tip.textContent).toMatch(/1\/2 ok/);
  });
});
