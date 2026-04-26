import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const concurrentState = vi.hoisted(() => ({
  data: undefined as { alerts: unknown[] } | undefined,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("./hooks", () => ({
  useConcurrentSpikes: () => concurrentState,
}));

import { ConcurrentSpikesCard } from "./ConcurrentSpikesCard";

describe("ConcurrentSpikesCard", () => {
  beforeEach(() => {
    concurrentState.data = undefined;
    concurrentState.isLoading = false;
    concurrentState.error = null;
    concurrentState.refetch.mockReset();
  });

  it("renders skeletons while loading", () => {
    concurrentState.isLoading = true;
    renderWithProviders(<ConcurrentSpikesCard />);
    expect(
      screen.getByTestId("concurrent-spikes-loading"),
    ).toBeInTheDocument();
  });

  it("renders the empty state when alerts=[]", () => {
    concurrentState.data = { alerts: [] };
    renderWithProviders(<ConcurrentSpikesCard />);
    expect(
      screen.getByText(/no concurrent-session spikes/i),
    ).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    concurrentState.error = new Error("kaboom");
    renderWithProviders(<ConcurrentSpikesCard />);
    expect(screen.getByTestId("concurrent-spikes-error")).toHaveTextContent(
      "kaboom",
    );
  });

  it("renders one row per spike with provider badges", () => {
    concurrentState.data = {
      alerts: [
        {
          username: "alice",
          count: 7,
          threshold: 5,
          providers: ["authelia", "jellyfin"],
        },
      ],
    };
    renderWithProviders(<ConcurrentSpikesCard />);
    expect(screen.getByTestId("concurrent-spikes-table")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("authelia")).toBeInTheDocument();
    expect(screen.getByText("jellyfin")).toBeInTheDocument();
  });

  it("filters spikes via the DataTable user filter", async () => {
    concurrentState.data = {
      alerts: [
        {
          username: "alice",
          count: 7,
          threshold: 5,
          providers: ["authelia"],
        },
        {
          username: "bob",
          count: 9,
          threshold: 5,
          providers: ["jellyfin"],
        },
      ],
    };
    renderWithProviders(<ConcurrentSpikesCard />);
    expect(screen.getByTestId("concurrent-spike-row-alice")).toBeInTheDocument();
    expect(screen.getByTestId("concurrent-spike-row-bob")).toBeInTheDocument();
    await userEvent.type(
      screen.getByTestId("concurrent-spike-filter-user"),
      "bob",
    );
    expect(screen.queryByTestId("concurrent-spike-row-alice")).toBeNull();
    expect(screen.getByTestId("concurrent-spike-row-bob")).toBeInTheDocument();
  });

  it("links Review sessions to /sessions with the user query param", () => {
    concurrentState.data = {
      alerts: [
        {
          username: "alice",
          count: 7,
          threshold: 5,
          providers: ["authelia"],
        },
      ],
    };
    renderWithProviders(<ConcurrentSpikesCard />);
    const link = screen.getByTestId("concurrent-spike-review-alice");
    expect(link).toHaveAttribute("href", "/sessions?user=alice");
  });
});
