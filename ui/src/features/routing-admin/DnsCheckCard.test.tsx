import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const dnsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  isFetching: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("./hooks", () => ({
  useDnsCheck: () => dnsState,
}));

import { DnsCheckCard } from "./DnsCheckCard";

describe("DnsCheckCard", () => {
  beforeEach(() => {
    dnsState.data = undefined;
    dnsState.isLoading = false;
    dnsState.isFetching = false;
    dnsState.error = null;
    dnsState.refetch.mockReset();
  });

  it("renders skeleton while loading", () => {
    dnsState.isLoading = true;
    renderWithProviders(<DnsCheckCard />);
    expect(screen.getByTestId("dns-check-loading")).toBeInTheDocument();
  });

  it("renders an empty state when there are no entries", () => {
    dnsState.data = { entries: [] };
    renderWithProviders(<DnsCheckCard />);
    expect(screen.getByText(/No hostnames configured/i)).toBeInTheDocument();
  });

  it("renders one row per hostname with resolved IPs", () => {
    dnsState.data = {
      entries: [
        {
          hostname: "media.example.test",
          resolved: ["10.0.0.1", "fd00::1"],
          status: "ok",
        },
        {
          hostname: "missing.example.test",
          resolved: [],
          status: "missing",
          error: "NXDOMAIN",
        },
        {
          hostname: "conflict.example.test",
          resolved: ["1.1.1.1", "2.2.2.2"],
          status: "conflict",
        },
      ],
    };
    renderWithProviders(<DnsCheckCard />);
    expect(screen.getByTestId("dns-row-media.example.test")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.1")).toBeInTheDocument();
    expect(screen.getByText("fd00::1")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.getByText("missing")).toBeInTheDocument();
    expect(screen.getByText("NXDOMAIN")).toBeInTheDocument();
    expect(screen.getByText("conflict")).toBeInTheDocument();
  });

  it("triggers refetch when Re-check is clicked", () => {
    dnsState.data = { entries: [] };
    renderWithProviders(<DnsCheckCard />);
    fireEvent.click(screen.getByTestId("dns-check-refresh"));
    expect(dnsState.refetch).toHaveBeenCalled();
  });

  it("renders the error banner when the probe fails", () => {
    dnsState.error = new Error("resolver crashed");
    renderWithProviders(<DnsCheckCard />);
    expect(screen.getByTestId("dns-check-error")).toHaveTextContent(
      "resolver crashed",
    );
  });

  it("derives a missing status from an empty IP list when none is provided", () => {
    dnsState.data = {
      entries: [{ hostname: "h.example.test", resolved: [] }],
    };
    renderWithProviders(<DnsCheckCard />);
    expect(screen.getByText("missing")).toBeInTheDocument();
  });
});
