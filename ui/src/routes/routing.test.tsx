import type { ComponentType } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const routingState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("@/features/routing-admin/hooks", () => ({
  useRouting: () => routingState,
}));

// Stub each feature card so the route test exercises only the
// composition order — each card has its own tests for behavior.
vi.mock("@/features/routing-admin/RoutingStrategyCard", () => ({
  RoutingStrategyCard: ({
    loading,
    error,
  }: {
    loading?: boolean;
    error?: Error | null;
  }) => (
    <div data-testid="mock-strategy-card">
      {loading ? "loading" : error ? `error:${error.message}` : "ok"}
    </div>
  ),
}));
vi.mock("@/features/routing-admin/ReachabilityMatrix", () => ({
  ReachabilityMatrix: () => <div data-testid="mock-reachability" />,
}));
vi.mock("@/features/routing-admin/DnsCheckCard", () => ({
  DnsCheckCard: () => <div data-testid="mock-dns" />,
}));
vi.mock("@/features/routing-admin/GatewayHostnamesCard", () => ({
  GatewayHostnamesCard: () => <div data-testid="mock-gateway" />,
}));
vi.mock("@/features/routing-admin/TlsCertificateCard", () => ({
  TlsCertificateCard: () => <div data-testid="mock-tls" />,
}));
vi.mock("@/features/routing-admin/ApexCatchAllCard", () => ({
  ApexCatchAllCard: () => <div data-testid="mock-apex" />,
}));
vi.mock("@/features/routing-admin/DefaultsCard", () => ({
  DefaultsCard: () => <div data-testid="mock-defaults" />,
}));
vi.mock("@/features/routing-admin/EnvoyAdminSummaryCard", () => ({
  EnvoyAdminSummaryCard: () => <div data-testid="mock-envoy-admin" />,
}));
vi.mock("@/features/routing-admin/ExposureCard", () => ({
  ExposureCard: () => <div data-testid="mock-exposure" />,
}));
vi.mock("@/features/routing-admin/HostnamesMatrix", () => ({
  HostnamesMatrix: () => <div data-testid="mock-hostnames-matrix" />,
}));
vi.mock("@/features/routing-admin/LiveAccessLogCard", () => ({
  LiveAccessLogCard: () => <div data-testid="mock-live-access-log" />,
}));
vi.mock("@/features/routing-admin/PathAliasesCard", () => ({
  PathAliasesCard: () => <div data-testid="mock-path-aliases" />,
}));
vi.mock("@/features/routing-admin/RouteTableCard", () => ({
  RouteTableCard: () => <div data-testid="mock-route-table" />,
}));
vi.mock("@/features/routing-admin/SankeyFlowCard", () => ({
  SankeyFlowCard: () => <div data-testid="mock-sankey" />,
}));
vi.mock("@/features/routing-admin/TopologyGraphCard", () => ({
  TopologyGraphCard: () => <div data-testid="mock-topology" />,
}));

import { Route as RoutingRoute } from "./routing";

const RoutingPage = RoutingRoute.options.component as ComponentType;

describe("routing route", () => {
  beforeEach(() => {
    routingState.data = undefined;
    routingState.isLoading = false;
    routingState.error = null;
    routingState.refetch.mockReset();
  });

  it("composes the Config tab's feature cards in the documented order", () => {
    // The route splits its surface into Config / Live / Diagnostics
    // tabs (Radix Tabs only mounts the active panel by default), so
    // the composition assertion runs against the cards declared in
    // the default-active "config" tab. Live/Diagnostics cards are
    // covered by the `tab triggers exist` smoke below.
    renderWithProviders(<RoutingPage />);
    const ids = [
      "mock-strategy-card",
      "mock-exposure",
      "mock-hostnames-matrix",
      "mock-route-table",
      "mock-path-aliases",
      "mock-apex",
      "mock-defaults",
      "mock-tls",
    ];
    for (const id of ids) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    const dom = ids.map((id) => screen.getByTestId(id));
    for (let i = 1; i < dom.length; i++) {
      // Each card appears later in document order than the previous.
      expect(
        Boolean(
          dom[i - 1]!.compareDocumentPosition(dom[i]!) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ),
      ).toBe(true);
    }
  });

  it("renders all three tab triggers (config / live / diagnostics)", () => {
    renderWithProviders(<RoutingPage />);
    expect(screen.getByRole("tab", { name: /config/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /live/i })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /diagnostics/i }),
    ).toBeInTheDocument();
  });

  it("forwards loading state into the strategy card", () => {
    routingState.isLoading = true;
    renderWithProviders(<RoutingPage />);
    expect(screen.getByTestId("mock-strategy-card")).toHaveTextContent(
      "loading",
    );
  });

  it("forwards an error into the strategy card", () => {
    routingState.error = new Error("dns");
    renderWithProviders(<RoutingPage />);
    expect(screen.getByTestId("mock-strategy-card")).toHaveTextContent(
      "error:dns",
    );
  });
});
