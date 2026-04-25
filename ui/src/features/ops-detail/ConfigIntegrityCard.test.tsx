import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const integrityState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useConfigIntegrity: () => integrityState,
}));

import { ConfigIntegrityCard } from "./ConfigIntegrityCard";

describe("ConfigIntegrityCard", () => {
  beforeEach(() => {
    integrityState.data = undefined;
    integrityState.isLoading = false;
    integrityState.error = null;
  });

  it("renders skeleton while loading", () => {
    integrityState.isLoading = true;
    renderWithProviders(<ConfigIntegrityCard />);
    expect(screen.getByTestId("config-integrity-loading")).toBeInTheDocument();
  });

  it("renders the error message when the query fails", () => {
    integrityState.error = new Error("read-only fs");
    renderWithProviders(<ConfigIntegrityCard />);
    expect(screen.getByTestId("config-integrity-error")).toHaveTextContent(
      "read-only fs",
    );
  });

  it("rolls up to 'ok' when every service is ok or skipped", () => {
    integrityState.data = {
      services: {
        sonarr: { service_id: "sonarr", status: "ok" },
        bazarr: { service_id: "bazarr", status: "skipped" },
      },
      checked_at: 1714000000,
    };
    renderWithProviders(<ConfigIntegrityCard />);
    expect(screen.getByTestId("config-integrity-status")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.getByText("all configs verified")).toBeInTheDocument();
  });

  // Regression for the "1 broken · 16 drift" mystery: the controller
  // emits `unknown` for services that don't declare a config file in
  // the registry. Pre-v1.3.3 this card bucketed `unknown` into drift,
  // surfacing 16 "drifted" services that had nothing to drift. The
  // fix excludes `unknown` and `skipped` from the rollup entirely.
  it("does NOT roll up `unknown` to drift — it's not actionable", () => {
    integrityState.data = {
      services: {
        sonarr: { service_id: "sonarr", status: "ok" },
        radarr: { service_id: "radarr", status: "unknown" },
        lidarr: { service_id: "lidarr", status: "unknown" },
      },
      checked_at: 1714000000,
    };
    renderWithProviders(<ConfigIntegrityCard />);
    // Status rolls up to ok — unknown is no-op, not drift.
    // (CardDescription literally contains "drift", so we scope the
    //  assertion to the status line and the rollup count text.)
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.getByText("all configs verified")).toBeInTheDocument();
    expect(screen.queryByText(/\d+ drift/)).toBeNull();
    // No flagged-list rendered for the unknown entries.
    expect(screen.queryByTestId("config-integrity-flagged")).toBeNull();
  });

  it("rolls up to 'drift' only when status is 'drift' or 'drifted'", () => {
    integrityState.data = {
      services: {
        sonarr: { service_id: "sonarr", status: "ok" },
        radarr: { service_id: "radarr", status: "drift", reason: "key changed" },
      },
      checked_at: 1714000000,
    };
    renderWithProviders(<ConfigIntegrityCard />);
    expect(screen.getAllByText("drift").length).toBeGreaterThan(0);
    expect(screen.getByText("1 drift")).toBeInTheDocument();
    // Flagged list now exists and shows the radarr row.
    expect(
      screen.getByTestId("config-integrity-row-radarr"),
    ).toBeInTheDocument();
  });

  it("rolls up to 'broken' when any service is corrupt or missing", () => {
    integrityState.data = {
      services: {
        sonarr: { service_id: "sonarr", status: "ok" },
        radarr: { service_id: "radarr", status: "corrupt", reason: "yaml syntax error" },
        lidarr: { service_id: "lidarr", status: "missing", file: "/etc/lidarr.yml" },
      },
      checked_at: 1714000000,
    };
    renderWithProviders(<ConfigIntegrityCard />);
    expect(screen.getByText("broken")).toBeInTheDocument();
    expect(screen.getByText("2 broken")).toBeInTheDocument();
    // Flagged list renders both broken rows with reason/file context.
    expect(
      screen.getByTestId("config-integrity-row-radarr"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("config-integrity-reason-radarr"),
    ).toHaveTextContent("yaml syntax error");
    expect(
      screen.getByTestId("config-integrity-row-lidarr"),
    ).toBeInTheDocument();
  });
});
