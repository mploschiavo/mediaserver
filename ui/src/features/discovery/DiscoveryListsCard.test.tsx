import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const discoveryState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const popularState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useDiscoveryLists: () => discoveryState,
    usePopularTv: () => popularState,
  };
});

import { DiscoveryListsCard } from "./DiscoveryListsCard";

describe("DiscoveryListsCard", () => {
  beforeEach(() => {
    discoveryState.data = undefined;
    discoveryState.isLoading = false;
    discoveryState.error = null;
    popularState.data = undefined;
    popularState.isLoading = false;
    popularState.error = null;
  });

  it("shows loading skeletons in both sections", () => {
    discoveryState.isLoading = true;
    popularState.isLoading = true;
    renderWithProviders(<DiscoveryListsCard />);
    expect(screen.getByTestId("discovery-lists-loading")).toBeInTheDocument();
    expect(screen.getByTestId("popular-tv-loading")).toBeInTheDocument();
  });

  it("renders configured discovery lists", () => {
    discoveryState.data = {
      lists: [
        { name: "Trakt Anticipated", source: "trakt" },
        { name: "TVMaze Popular", source: "tvmaze" },
      ],
    };
    popularState.data = [];
    renderWithProviders(<DiscoveryListsCard />);
    const list = screen.getByTestId("discovery-lists-list");
    expect(list).toHaveTextContent("Trakt Anticipated");
    expect(list).toHaveTextContent("TVMaze Popular");
  });

  it("renders an empty state when no discovery lists are configured", () => {
    discoveryState.data = { lists: [] };
    popularState.data = [];
    renderWithProviders(<DiscoveryListsCard />);
    expect(
      screen.getByText(/No discovery sources configured/i),
    ).toBeInTheDocument();
  });

  it("renders popular TV picks", () => {
    discoveryState.data = { lists: [] };
    popularState.data = [
      { tvdbId: 81189, title: "Breaking Bad" },
      { tvdbId: 121361, title: "Game of Thrones" },
    ];
    renderWithProviders(<DiscoveryListsCard />);
    const list = screen.getByTestId("popular-tv-list");
    expect(list).toHaveTextContent("Breaking Bad");
    expect(list).toHaveTextContent("Game of Thrones");
  });

  it("renders the popular-tv empty message when no items", () => {
    discoveryState.data = { lists: [{ name: "X" }] };
    popularState.data = [];
    renderWithProviders(<DiscoveryListsCard />);
    expect(screen.getByTestId("popular-tv-empty")).toBeInTheDocument();
  });
});
