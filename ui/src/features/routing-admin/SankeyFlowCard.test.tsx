import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { SankeyFlowCard } from "./SankeyFlowCard";
import type { EnvoyAdminSummary } from "./useEnvoyAdminSummary";

vi.mock("./useEnvoyAdminSummary", async () => {
  const actual = await vi.importActual<
    typeof import("./useEnvoyAdminSummary")
  >("./useEnvoyAdminSummary");
  return { ...actual, useEnvoyAdminSummary: vi.fn() };
});
const { useEnvoyAdminSummary } = await import("./useEnvoyAdminSummary");

const baseData: EnvoyAdminSummary = {
  clusters: [],
  request_totals: {
    service_jellyfin: 5000,
    service_sonarr: 1200,
    service_authelia: 800,
  },
  request_p_latency_ms: {},
  active_connections: {},
  downstream_breakdown: { total: 7000, rq_2xx: 6900, rq_4xx: 50, rq_5xx: 50 },
  tls_handshake_errors: 0,
};

beforeEach(() => vi.mocked(useEnvoyAdminSummary).mockReset());

describe("SankeyFlowCard", () => {
  it("renders the SVG when traffic exists", () => {
    vi.mocked(useEnvoyAdminSummary).mockReturnValue({
      data: baseData, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useEnvoyAdminSummary>);
    renderWithProviders(<SankeyFlowCard />);
    expect(screen.getByTestId("sankey-flow-svg")).toBeInTheDocument();
  });

  it("renders one node per top cluster + the gateway", () => {
    vi.mocked(useEnvoyAdminSummary).mockReturnValue({
      data: baseData, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useEnvoyAdminSummary>);
    renderWithProviders(<SankeyFlowCard />);
    expect(screen.getByTestId("sankey-node-__gateway__")).toBeInTheDocument();
    expect(screen.getByTestId("sankey-node-service_jellyfin")).toBeInTheDocument();
    expect(screen.getByTestId("sankey-node-service_sonarr")).toBeInTheDocument();
    expect(screen.getByTestId("sankey-node-service_authelia")).toBeInTheDocument();
  });

  it("renders one link per cluster", () => {
    vi.mocked(useEnvoyAdminSummary).mockReturnValue({
      data: baseData, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useEnvoyAdminSummary>);
    renderWithProviders(<SankeyFlowCard />);
    expect(screen.getByTestId("sankey-link-service_jellyfin")).toBeInTheDocument();
  });

  it("renders empty-state when no traffic", () => {
    vi.mocked(useEnvoyAdminSummary).mockReturnValue({
      data: { ...baseData, request_totals: {} },
      isLoading: false, error: null,
    } as unknown as ReturnType<typeof useEnvoyAdminSummary>);
    renderWithProviders(<SankeyFlowCard />);
    expect(screen.getByTestId("sankey-flow-empty")).toBeInTheDocument();
  });

  it("renders skeleton during loading", () => {
    vi.mocked(useEnvoyAdminSummary).mockReturnValue({
      data: undefined, isLoading: true, error: null,
    } as unknown as ReturnType<typeof useEnvoyAdminSummary>);
    renderWithProviders(<SankeyFlowCard />);
    expect(screen.getByTestId("sankey-flow-card-loading")).toBeInTheDocument();
  });

  it("collapses everything below top-8 into 'other'", () => {
    const big: EnvoyAdminSummary = {
      ...baseData,
      request_totals: {
        s1: 1000, s2: 900, s3: 800, s4: 700, s5: 600,
        s6: 500, s7: 400, s8: 300, s9: 200, s10: 100,
      },
    };
    vi.mocked(useEnvoyAdminSummary).mockReturnValue({
      data: big, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useEnvoyAdminSummary>);
    renderWithProviders(<SankeyFlowCard />);
    expect(screen.getByTestId("sankey-node-__other__")).toBeInTheDocument();
  });
});
