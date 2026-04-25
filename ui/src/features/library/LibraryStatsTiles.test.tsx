import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const librariesState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useLibraries: () => librariesState,
  };
});

import { LibraryStatsTiles } from "./LibraryStatsTiles";

describe("LibraryStatsTiles", () => {
  beforeEach(() => {
    librariesState.data = undefined;
    librariesState.isLoading = false;
    librariesState.error = null;
    librariesState.refetch.mockReset();
  });

  it("renders skeletons while loading", () => {
    librariesState.isLoading = true;
    renderWithProviders(<LibraryStatsTiles />);
    expect(
      screen.getAllByTestId("library-stats-skeleton").length,
    ).toBeGreaterThan(0);
  });

  it("derives counts from live[].item_count when Jellyfin reports them", () => {
    // Real /api/libraries shape: live[] supplies item_count per library
    // (collection_type + name); we sum into the canonical 4-tile mapping.
    librariesState.data = {
      live: [
        { name: "Movies", collection_type: "movies", item_count: 12 },
        { name: "TV", collection_type: "tvshows", item_count: 4 },
        { name: "Books", collection_type: "books", item_count: 99 },
      ],
      configured: [
        { name: "Movies", collection_type: "movies", paths: [] },
        { name: "TV", collection_type: "tvshows", paths: [] },
        { name: "Music", collection_type: "music", paths: [] },
        { name: "Books", collection_type: "books", paths: [] },
      ],
      source: "profile",
      media_server: "jellyfin",
    };
    renderWithProviders(<LibraryStatsTiles />);
    expect(screen.getByTestId("library-stat-movies")).toHaveTextContent("12");
    expect(screen.getByTestId("library-stat-tv")).toHaveTextContent("4");
    expect(screen.getByTestId("library-stat-books")).toHaveTextContent("99");
    // music is not in live[], so falls back to 0 (no item_count reported,
    // and live data was surfaced for at least one entry — fallback path
    // doesn't kick in once live data is present).
    expect(screen.getByTestId("library-stat-tracks")).toHaveTextContent("0");
  });

  it("falls back to configured library count per collection_type when live[] is empty", () => {
    librariesState.data = {
      live: [],
      configured: [
        { name: "Movies", collection_type: "movies", paths: ["/media/movies"] },
        { name: "TV Shows", collection_type: "tvshows", paths: ["/media/tv"] },
        { name: "Music", collection_type: "music", paths: ["/media/music"] },
        { name: "Books", collection_type: "books", paths: ["/media/books"] },
      ],
      source: "defaults",
      media_server: "jellyfin",
    };
    renderWithProviders(<LibraryStatsTiles />);
    // Each tile shows 1 — one configured library per collection type.
    expect(screen.getByTestId("library-stat-movies")).toHaveTextContent("1");
    expect(screen.getByTestId("library-stat-tv")).toHaveTextContent("1");
    expect(screen.getByTestId("library-stat-tracks")).toHaveTextContent("1");
    expect(screen.getByTestId("library-stat-books")).toHaveTextContent("1");
  });

  it("renders the error banner with retry when stats fail", () => {
    librariesState.error = new Error("offline");
    renderWithProviders(<LibraryStatsTiles />);
    const banner = screen.getByTestId("library-stats-error");
    expect(banner).toHaveTextContent("offline");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
