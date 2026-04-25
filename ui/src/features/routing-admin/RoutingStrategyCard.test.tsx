import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { RoutingResponse } from "./hooks";

vi.mock("./RoutingEditor", () => ({
  RoutingEditor: ({ onCancel }: { onCancel?: () => void }) => (
    <div data-testid="routing-editor-mock">
      <button onClick={onCancel} data-testid="routing-editor-mock-cancel">
        cancel
      </button>
    </div>
  ),
}));

import { RoutingStrategyCard } from "./RoutingStrategyCard";

// Live /api/routing payload (verbatim from
// ui/.ratchets/notes/API-RESPONSE-SHAPES-2026-04-25.txt). The
// previously-mocked `{strategy: {...}, apps: [...]}` shape never
// existed on the wire — the controller emits flat config.
const sample: RoutingResponse = {
  base_domain: "io",
  stack_subdomain: "iomio",
  gateway_host: "m.iomio.io",
  gateway_port: 443,
  app_path_prefix: "/app",
  strategy: "hybrid",
  internet_exposed: true,
  direct_hosts: {
    media_server: "jf.iomio.io",
    auth: "auth.iomio.io",
  },
};

describe("RoutingStrategyCard", () => {
  it("renders skeleton while loading", () => {
    renderWithProviders(<RoutingStrategyCard loading />);
    expect(screen.getByTestId("routing-strategy-loading")).toBeInTheDocument();
  });

  it("renders the flat routing config when populated", () => {
    renderWithProviders(<RoutingStrategyCard data={sample} />);
    expect(screen.getByTestId("routing-strategy-card")).toBeInTheDocument();
    expect(screen.getByTestId("routing-strategy-mode")).toHaveTextContent(
      "hybrid",
    );
    expect(
      screen.getByTestId("routing-strategy-base-domain"),
    ).toHaveTextContent("io");
    expect(screen.getByTestId("routing-strategy-gateway")).toHaveTextContent(
      "m.iomio.io:443",
    );
    expect(
      screen.getByTestId("routing-strategy-app-path-prefix"),
    ).toHaveTextContent("/app");
    // The internet-exposed badge is "yes" when the controller sets it true.
    expect(screen.getByText(/Internet exposed/i)).toBeInTheDocument();
  });

  it("falls back to em-dashes when fields are missing", () => {
    renderWithProviders(<RoutingStrategyCard data={{}} />);
    expect(screen.getByTestId("routing-strategy-mode")).toHaveTextContent(
      "unknown",
    );
    expect(
      screen.getByTestId("routing-strategy-base-domain"),
    ).toHaveTextContent("—");
  });

  it("swaps in the editor when Edit is clicked", () => {
    renderWithProviders(<RoutingStrategyCard data={sample} />);
    fireEvent.click(screen.getByTestId("routing-strategy-edit"));
    expect(screen.getByTestId("routing-editor-mock")).toBeInTheDocument();
    expect(screen.queryByTestId("routing-strategy-card")).toBeNull();
  });

  it("returns to the read view when the editor cancels", () => {
    renderWithProviders(<RoutingStrategyCard data={sample} />);
    fireEvent.click(screen.getByTestId("routing-strategy-edit"));
    fireEvent.click(screen.getByTestId("routing-editor-mock-cancel"));
    expect(screen.getByTestId("routing-strategy-card")).toBeInTheDocument();
  });

  it("renders an error card with retry", () => {
    const onRetry = vi.fn();
    renderWithProviders(
      <RoutingStrategyCard error={new Error("boom")} onRetry={onRetry} />,
    );
    expect(screen.getByTestId("routing-strategy-error")).toHaveTextContent(
      "boom",
    );
    fireEvent.click(screen.getByTestId("routing-strategy-retry"));
    expect(onRetry).toHaveBeenCalled();
  });
});
