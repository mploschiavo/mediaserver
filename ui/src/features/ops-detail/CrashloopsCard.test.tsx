import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const crashState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useCrashloops: () => crashState,
}));

import { CrashloopsCard } from "./CrashloopsCard";

describe("CrashloopsCard", () => {
  beforeEach(() => {
    crashState.data = undefined;
    crashState.isLoading = false;
    crashState.error = null;
  });

  it("renders skeletons while loading", () => {
    crashState.isLoading = true;
    renderWithProviders(<CrashloopsCard />);
    expect(screen.getByTestId("crashloops-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no services are crashlooping", () => {
    crashState.data = { services: {} };
    renderWithProviders(<CrashloopsCard />);
    expect(screen.getByText("No services crashlooping")).toBeInTheDocument();
  });

  it("excludes healthy services from the table", () => {
    crashState.data = {
      services: {
        sonarr: {
          service_id: "sonarr",
          restart_count: 0,
          cause: "healthy",
          description: "",
          healable: false,
          sample_log_line: "",
          last_terminated_reason: "",
          checked_at: Date.now() / 1000,
        },
      },
    };
    renderWithProviders(<CrashloopsCard />);
    expect(screen.getByText("No services crashlooping")).toBeInTheDocument();
  });

  it("renders the error message when the query fails", () => {
    crashState.error = new Error("nope");
    renderWithProviders(<CrashloopsCard />);
    expect(screen.getByTestId("crashloops-error")).toHaveTextContent("nope");
  });

  it("renders a row for each crashlooping service", () => {
    crashState.data = {
      services: {
        radarr: {
          service_id: "radarr",
          restart_count: 7,
          cause: "oom_kill",
          description: "Pod was OOMKilled",
          healable: true,
          sample_log_line: "",
          last_terminated_reason: "OOMKilled",
          checked_at: Date.now() / 1000,
        },
      },
    };
    renderWithProviders(<CrashloopsCard />);
    // Both desktop + mobile branches of ResponsiveTable render at the
    // same time; we just need the service id to appear at least once.
    expect(screen.getAllByText("radarr").length).toBeGreaterThan(0);
    expect(screen.getAllByText("OOMKilled").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7").length).toBeGreaterThan(0);
  });
});
