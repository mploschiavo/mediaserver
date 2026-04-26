import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const newLocationsState = vi.hoisted(() => ({
  data: undefined as { alerts: unknown[] } | undefined,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("./hooks", () => ({
  useNewLocations: () => newLocationsState,
}));

import { NewLocationsCard } from "./NewLocationsCard";

describe("NewLocationsCard", () => {
  beforeEach(() => {
    newLocationsState.data = undefined;
    newLocationsState.isLoading = false;
    newLocationsState.error = null;
    newLocationsState.refetch.mockReset();
  });

  it("renders skeletons while loading", () => {
    newLocationsState.isLoading = true;
    renderWithProviders(<NewLocationsCard />);
    expect(screen.getByTestId("new-locations-loading")).toBeInTheDocument();
  });

  it("renders the empty state when alerts=[]", () => {
    newLocationsState.data = { alerts: [] };
    renderWithProviders(<NewLocationsCard />);
    expect(screen.getByText("No new-location alerts")).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    newLocationsState.error = new Error("network down");
    renderWithProviders(<NewLocationsCard />);
    expect(screen.getByTestId("new-locations-error")).toHaveTextContent(
      "network down",
    );
  });

  it("renders one row per alert with user, prior IP, new IP, timestamp", () => {
    newLocationsState.data = {
      alerts: [
        {
          username: "alice",
          provider: "authelia",
          prior_ip: "10.0.0.5",
          prior_geo: "Berlin, DE",
          ip: "203.0.113.7",
          geo: "London, GB",
          observed_at: new Date(Date.now() - 60_000).toISOString(),
        },
      ],
    };
    renderWithProviders(<NewLocationsCard />);
    expect(screen.getByTestId("new-locations-table")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.5")).toBeInTheDocument();
    expect(screen.getByText("Berlin, DE")).toBeInTheDocument();
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
    expect(screen.getByText("London, GB")).toBeInTheDocument();
    expect(screen.getByText("authelia")).toBeInTheDocument();
  });

  it("filters alerts via the DataTable user filter", async () => {
    newLocationsState.data = {
      alerts: [
        {
          username: "alice",
          provider: "authelia",
          prior_ip: "10.0.0.5",
          ip: "203.0.113.7",
          observed_at: new Date(0).toISOString(),
        },
        {
          username: "bob",
          provider: "authelia",
          prior_ip: "10.0.0.6",
          ip: "203.0.113.8",
          observed_at: new Date(0).toISOString(),
        },
      ],
    };
    renderWithProviders(<NewLocationsCard />);
    expect(
      screen.getByTestId(/^new-location-row-alice/),
    ).toBeInTheDocument();
    expect(screen.getByTestId(/^new-location-row-bob/)).toBeInTheDocument();
    await userEvent.type(
      screen.getByTestId("new-location-filter-user"),
      "bob",
    );
    expect(screen.queryByTestId(/^new-location-row-alice/)).toBeNull();
    expect(screen.getByTestId(/^new-location-row-bob/)).toBeInTheDocument();
  });

  it("renders an Acknowledge button that is disabled with a pending tooltip", () => {
    newLocationsState.data = {
      alerts: [
        {
          username: "alice",
          ip: "203.0.113.7",
          observed_at: new Date(0).toISOString(),
        },
      ],
    };
    renderWithProviders(<NewLocationsCard />);
    const buttons = screen.getAllByRole("button", { name: /acknowledge/i });
    expect(buttons.length).toBeGreaterThan(0);
    expect(buttons[0]).toBeDisabled();
  });
});
