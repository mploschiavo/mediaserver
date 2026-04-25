import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { StatusOverview } from "./StatusOverview";
import type { MediaIntegrityStatusShape } from "@/api";

// useBytesCounter is built on Framer Motion's animation engine; in
// tests we don't need the easing — we just want the formatted target
// to land in the DOM.
vi.mock("./use-bytes-counter", () => ({
  useBytesCounter: (n: number, fmt: (x: number) => string) => fmt(n),
}));

const baseStatus: MediaIntegrityStatusShape = {
  last_enforce: { ts: new Date(Date.now() - 120_000).toISOString(), detail: {} },
  last_reconcile: {
    ts: new Date(Date.now() - 12 * 60_000).toISOString(),
    detail: { bytes_freed: 1024 * 1024 * 1024 * 12.3 },
  },
  policy_version: 7,
  servarr_adapters: ["radarr", "sonarr"],
  bazarr_present: true,
  missing_api_keys: [],
};

describe("StatusOverview", () => {
  it("renders skeletons while loading", () => {
    renderWithProviders(<StatusOverview loading />);
    expect(screen.getByTestId("status-overview-loading")).toBeInTheDocument();
  });

  it("renders the error banner with the message", () => {
    renderWithProviders(
      <StatusOverview error={new Error("boom") as Error} />,
    );
    const banner = screen.getByTestId("status-overview-error");
    expect(banner).toHaveTextContent("Failed to load");
    expect(banner).toHaveTextContent("boom");
  });

  it("renders the populated grid with three cards", () => {
    renderWithProviders(<StatusOverview status={baseStatus} />);
    expect(screen.getByTestId("status-overview")).toBeInTheDocument();
    expect(screen.getByTestId("bytes-freed")).toHaveTextContent(/GB/);
  });

  it("renders the policy version", () => {
    renderWithProviders(<StatusOverview status={baseStatus} />);
    expect(screen.getByText(/policy v7/)).toBeInTheDocument();
  });

  it("shows the servarr adapter count and bazarr-on chip", () => {
    renderWithProviders(<StatusOverview status={baseStatus} />);
    expect(screen.getByText("2 servarr")).toBeInTheDocument();
    expect(screen.getByText("bazarr on")).toBeInTheDocument();
  });

  it("renders 'bazarr off' when bazarr is absent", () => {
    renderWithProviders(
      <StatusOverview status={{ ...baseStatus, bazarr_present: false }} />,
    );
    expect(screen.getByText("bazarr off")).toBeInTheDocument();
  });

  it("surfaces missing API keys as an alert", () => {
    renderWithProviders(
      <StatusOverview
        status={{ ...baseStatus, missing_api_keys: ["radarr", "sonarr"] }}
      />,
    );
    const alert = screen.getByTestId("missing-api-keys");
    expect(alert).toHaveTextContent("Missing API keys: radarr, sonarr");
  });

  it("does not render the missing-keys alert when none are missing", () => {
    renderWithProviders(<StatusOverview status={baseStatus} />);
    expect(screen.queryByTestId("missing-api-keys")).toBeNull();
  });
});
