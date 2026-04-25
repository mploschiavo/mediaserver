import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const failedState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useFailedServices: () => failedState,
}));

import { FailedServicesCard } from "./FailedServicesCard";

describe("FailedServicesCard", () => {
  beforeEach(() => {
    failedState.data = undefined;
    failedState.isLoading = false;
    failedState.error = null;
  });

  it("renders skeletons while loading", () => {
    failedState.isLoading = true;
    renderWithProviders(<FailedServicesCard />);
    expect(screen.getByTestId("failed-services-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no services are failed", () => {
    failedState.data = { failed_services: [], count: 0 };
    renderWithProviders(<FailedServicesCard />);
    expect(screen.getByText("No failed services")).toBeInTheDocument();
  });

  it("renders the error message when the query fails", () => {
    failedState.error = new Error("auth gone");
    renderWithProviders(<FailedServicesCard />);
    expect(screen.getByTestId("failed-services-error")).toHaveTextContent(
      "auth gone",
    );
  });

  it("renders one row per failed service (string entries)", () => {
    failedState.data = {
      failed_services: ["sonarr", "radarr"],
      count: 2,
    };
    renderWithProviders(<FailedServicesCard />);
    expect(screen.getByTestId("failed-sonarr")).toBeInTheDocument();
    expect(screen.getByTestId("failed-radarr")).toBeInTheDocument();
  });

  it("renders rich entries with reason and since", () => {
    failedState.data = {
      failed_services: [
        {
          service_id: "qbittorrent",
          reason: "exit code 137 - oom",
          since: new Date(Date.now() - 5 * 60_000).toISOString(),
        },
      ],
    };
    renderWithProviders(<FailedServicesCard />);
    expect(screen.getByTestId("failed-qbittorrent")).toBeInTheDocument();
    expect(
      screen.getByText("exit code 137 - oom"),
    ).toBeInTheDocument();
  });

  it("expands long reasons on click", () => {
    const longReason = "x".repeat(200);
    failedState.data = {
      failed_services: [
        {
          service_id: "lidarr",
          reason: longReason,
          since: new Date().toISOString(),
        },
      ],
    };
    renderWithProviders(<FailedServicesCard />);
    const toggle = screen.getByTestId("failed-toggle-lidarr");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
