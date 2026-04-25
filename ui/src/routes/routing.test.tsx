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

import { Route as RoutingRoute } from "./routing";

const RoutingPage = RoutingRoute.options.component as ComponentType;

describe("routing route", () => {
  beforeEach(() => {
    routingState.data = undefined;
    routingState.isLoading = false;
    routingState.error = null;
    routingState.refetch.mockReset();
  });

  it("composes the feature cards in the documented order", () => {
    renderWithProviders(<RoutingPage />);
    const ids = [
      "mock-strategy-card",
      "mock-reachability",
      "mock-dns",
      "mock-gateway",
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
