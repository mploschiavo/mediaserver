import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProgressBar } from "./ProgressBar";

describe("ProgressBar", () => {
  it("renders nothing when not in flight", () => {
    const { container } = render(
      <ProgressBar inFlight={false} progress={{ in_progress: false }} />,
    );
    expect(container.querySelector('[data-testid="mi-progress"]')).toBeNull();
  });

  it("mounts the strip with role=status when in flight", () => {
    render(
      <ProgressBar
        inFlight
        progress={{
          in_progress: true,
          op: "reconcile",
          started_at: new Date(Date.now() - 30_000).toISOString(),
          phase: "scanning radarr",
          current: null,
          total: null,
        }}
      />,
    );
    const strip = screen.getByTestId("mi-progress");
    expect(strip).toHaveAttribute("role", "status");
    expect(strip).toHaveAttribute("aria-live", "polite");
  });

  it("renders the reconcile op label", () => {
    render(
      <ProgressBar
        inFlight
        progress={{
          in_progress: true,
          op: "reconcile",
          started_at: new Date().toISOString(),
          phase: "",
          current: null,
          total: null,
        }}
      />,
    );
    expect(screen.getByText(/Reconciling/)).toBeInTheDocument();
  });

  it("renders the enforce op label", () => {
    render(
      <ProgressBar
        inFlight
        progress={{
          in_progress: true,
          op: "enforce_config",
          started_at: new Date().toISOString(),
          phase: "",
          current: null,
          total: null,
        }}
      />,
    );
    expect(screen.getByText(/Enforcing config/)).toBeInTheDocument();
  });

  it("appends the phase when present", () => {
    render(
      <ProgressBar
        inFlight
        progress={{
          in_progress: true,
          op: "reconcile",
          started_at: new Date().toISOString(),
          phase: "deduping",
          current: null,
          total: null,
        }}
      />,
    );
    expect(screen.getByText(/deduping/)).toBeInTheDocument();
  });

  it("renders 'Working' when no progress data is available", () => {
    render(<ProgressBar inFlight progress={undefined} />);
    expect(screen.getByText(/Working/)).toBeInTheDocument();
  });

  it("shows the relative start time when started_at is present", () => {
    render(
      <ProgressBar
        inFlight
        progress={{
          in_progress: true,
          op: "reconcile",
          started_at: new Date(Date.now() - 30_000).toISOString(),
          phase: "",
          current: null,
          total: null,
        }}
      />,
    );
    expect(screen.getByText(/started/)).toBeInTheDocument();
  });
});
