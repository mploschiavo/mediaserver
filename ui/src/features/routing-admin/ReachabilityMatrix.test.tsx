import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const probeState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  isFetching: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("./hooks", () => ({
  useRoutingProbe: () => probeState,
}));

import { ReachabilityMatrix } from "./ReachabilityMatrix";

describe("ReachabilityMatrix", () => {
  beforeEach(() => {
    probeState.data = undefined;
    probeState.isLoading = false;
    probeState.isFetching = false;
    probeState.error = null;
    probeState.refetch.mockReset();
  });

  it("renders skeleton while loading", () => {
    probeState.isLoading = true;
    renderWithProviders(<ReachabilityMatrix />);
    expect(screen.getByTestId("reachability-loading")).toBeInTheDocument();
  });

  it("renders an empty state when no rows are returned", () => {
    probeState.data = { rows: [] };
    renderWithProviders(<ReachabilityMatrix />);
    expect(screen.getByText(/No probe data/i)).toBeInTheDocument();
  });

  it("renders a row per probe entry with status badges", () => {
    probeState.data = {
      rows: [
        {
          app: "sonarr",
          internal_url: "http://sonarr:8989",
          external_url: "https://sonarr.example.test",
          ok: true,
          status_code: 200,
          latency_ms: 42,
          probed_at: new Date().toISOString(),
        },
        {
          app: "radarr",
          internal_url: "http://radarr:7878",
          external_url: "https://radarr.example.test",
          ok: false,
          status_code: 502,
          latency_ms: 5000,
          probed_at: new Date().toISOString(),
          error: "bad gateway",
        },
      ],
    };
    renderWithProviders(<ReachabilityMatrix />);
    expect(screen.getAllByText("sonarr").length).toBeGreaterThan(0);
    expect(screen.getAllByText("radarr").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/ok \(200\)/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/fail \(502\)/i).length).toBeGreaterThan(0);
  });

  it("triggers refetch when Re-probe is clicked", () => {
    probeState.data = { rows: [] };
    renderWithProviders(<ReachabilityMatrix />);
    fireEvent.click(screen.getByTestId("reachability-refresh"));
    expect(probeState.refetch).toHaveBeenCalled();
  });

  it("renders the error banner when the probe fails", () => {
    probeState.error = new Error("probe blew up");
    renderWithProviders(<ReachabilityMatrix />);
    expect(screen.getByTestId("reachability-error")).toHaveTextContent(
      "probe blew up",
    );
  });

  it("falls back to a per-app object map when rows is missing", () => {
    probeState.data = {
      sonarr: {
        ok: true,
        status_code: 200,
        latency_ms: 12,
        external_url: "https://sonarr.example.test",
      },
    };
    renderWithProviders(<ReachabilityMatrix />);
    expect(screen.getAllByText("sonarr").length).toBeGreaterThan(0);
  });

  it("filters rows in-memory via the per-column app filter", async () => {
    probeState.data = {
      rows: [
        {
          app: "sonarr",
          internal_url: "http://sonarr:8989",
          external_url: "https://sonarr.example.test",
          ok: true,
          status_code: 200,
          latency_ms: 42,
          probed_at: new Date().toISOString(),
        },
        {
          app: "radarr",
          internal_url: "http://radarr:7878",
          external_url: "https://radarr.example.test",
          ok: false,
          status_code: 502,
          latency_ms: 5000,
          probed_at: new Date().toISOString(),
        },
      ],
    };
    renderWithProviders(<ReachabilityMatrix />);
    expect(screen.getAllByTestId(/^reachability-rows-row-/).length).toBe(2);
    const appFilter = screen.getByTestId("reachability-rows-filter-app");
    await userEvent.type(appFilter, "sonarr");
    await waitFor(() =>
      expect(screen.getAllByTestId(/^reachability-rows-row-/).length).toBe(1),
    );
  });
});
