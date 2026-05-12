/**
 * Tests for the EPG health donut bucketing. Guards the 2026-05-12
 * "No guide sources configured" bug where ``bucketHealth`` only
 * looked at ``probes`` / ``sources`` arrays the live
 * ``/api/epg-health`` payload never returns. The real payload is
 * ``{healthy, unhealthy, countries, providers, details}`` and the
 * chart now buckets from those.
 */

import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { LivetvHealthChart } from "./LivetvHealthChart";
import * as hooks from "./hooks";

function withQueryClient(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("LivetvHealthChart", () => {
  it("buckets from healthy/unhealthy aggregates on the live payload shape", () => {
    vi.spyOn(hooks, "useEpgHealth").mockReturnValue({
      data: {
        healthy: 7,
        unhealthy: 2,
        countries: 3,
        providers: 4,
        details: {},
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof hooks.useEpgHealth>);
    render(withQueryClient(<LivetvHealthChart />));
    expect(
      screen.queryByTestId("livetv-health-chart-empty"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("livetv-health-chart-area")).toBeInTheDocument();
  });

  it("falls back to walking details when aggregates are missing", () => {
    vi.spyOn(hooks, "useEpgHealth").mockReturnValue({
      data: {
        details: {
          us: { "iptv-org": true, "schedules-direct": false },
          uk: { "iptv-org": true },
        },
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof hooks.useEpgHealth>);
    render(withQueryClient(<LivetvHealthChart />));
    expect(
      screen.queryByTestId("livetv-health-chart-empty"),
    ).not.toBeInTheDocument();
  });

  it("renders the empty state when there are no providers + no aggregates", () => {
    vi.spyOn(hooks, "useEpgHealth").mockReturnValue({
      data: { healthy: 0, unhealthy: 0, countries: 0, providers: 0, details: {} },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof hooks.useEpgHealth>);
    render(withQueryClient(<LivetvHealthChart />));
    expect(screen.getByTestId("livetv-health-chart-empty")).toBeInTheDocument();
  });
});
