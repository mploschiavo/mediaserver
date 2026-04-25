import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const storageState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useStorageBreakdown: () => storageState,
}));

import { StorageBreakdownCard } from "./StorageBreakdownCard";

describe("StorageBreakdownCard", () => {
  beforeEach(() => {
    storageState.data = undefined;
    storageState.isLoading = false;
    storageState.error = null;
  });

  it("renders skeletons while loading", () => {
    storageState.isLoading = true;
    renderWithProviders(<StorageBreakdownCard />);
    expect(screen.getByTestId("storage-breakdown-loading")).toBeInTheDocument();
  });

  it("renders empty state when no library has bytes", () => {
    storageState.data = {};
    renderWithProviders(<StorageBreakdownCard />);
    expect(screen.getByText("No usage data")).toBeInTheDocument();
  });

  it("renders the error banner when the query fails", () => {
    storageState.error = new Error("nope");
    renderWithProviders(<StorageBreakdownCard />);
    expect(screen.getByTestId("storage-breakdown-error")).toHaveTextContent(
      "nope",
    );
  });

  it("renders one SVG bar per library that has bytes", () => {
    storageState.data = {
      movies: { bytes: 4_000_000_000 },
      tv: { bytes: 2_000_000_000 },
      tracks: { bytes: 0 },
      books: { bytes: 100_000_000 },
    };
    renderWithProviders(<StorageBreakdownCard />);
    expect(screen.getByTestId("storage-breakdown-svg")).toBeInTheDocument();
    expect(screen.getByTestId("storage-bar-movies")).toBeInTheDocument();
    expect(screen.getByTestId("storage-bar-tv")).toBeInTheDocument();
    expect(screen.getByTestId("storage-bar-books")).toBeInTheDocument();
    // tracks had 0 bytes — no row.
    expect(screen.queryByTestId("storage-bar-tracks")).toBeNull();
  });

  it("sums by_kind when bytes is omitted", () => {
    storageState.data = {
      movies: { by_kind: { mkv: 1_500_000_000, mp4: 500_000_000 } },
    };
    renderWithProviders(<StorageBreakdownCard />);
    expect(screen.getByTestId("storage-bar-movies")).toBeInTheDocument();
  });

  // Regression: the live controller (`disk.py::get_storage_breakdown`)
  // emits `{breakdown: [{name, path, bytes, display}], total_bytes,
  // total_display, media_root}` — NOT a top-level keyed shape. The
  // pre-v1.3.3 card only read `data.movies` / `data.tv` / etc. and
  // showed "No usage data" against every real deployment. These
  // ratchets lock the live shape so it can't regress.
  describe("live controller shape: `breakdown[]` array", () => {
    it("renders a row per breakdown[] entry with bytes > 0", () => {
      storageState.data = {
        breakdown: [
          { name: "Movies", path: "/srv-stack/media/Movies", bytes: 4_000_000_000, display: "4.0 GB" },
          { name: "TV Shows", path: "/srv-stack/media/TV Shows", bytes: 2_000_000_000, display: "2.0 GB" },
          { name: "Music", path: "/srv-stack/media/Music", bytes: 100_000_000, display: "100 MB" },
        ],
        total_bytes: 6_100_000_000,
        media_root: "/srv-stack/media",
      };
      renderWithProviders(<StorageBreakdownCard />);
      expect(screen.getByTestId("storage-breakdown-svg")).toBeInTheDocument();
      expect(screen.getByTestId("storage-bar-movies")).toBeInTheDocument();
      expect(screen.getByTestId("storage-bar-tv-shows")).toBeInTheDocument();
      expect(screen.getByTestId("storage-bar-music")).toBeInTheDocument();
    });

    it("excludes breakdown[] entries with 0 bytes", () => {
      storageState.data = {
        breakdown: [
          { name: "Movies", bytes: 1_000_000_000 },
          { name: "Empty", bytes: 0 },
        ],
      };
      renderWithProviders(<StorageBreakdownCard />);
      expect(screen.getByTestId("storage-bar-movies")).toBeInTheDocument();
      expect(screen.queryByTestId("storage-bar-empty")).toBeNull();
    });

    it("falls back to empty state when breakdown[] is empty (no media_root configured)", () => {
      storageState.data = {
        breakdown: [],
        error: "Media root not found",
        total_bytes: 0,
      };
      renderWithProviders(<StorageBreakdownCard />);
      expect(screen.getByText("No usage data")).toBeInTheDocument();
    });

    it("prefers breakdown[] over legacy keyed shape when both are present", () => {
      storageState.data = {
        breakdown: [{ name: "Audiobooks", bytes: 5_000_000_000 }],
        // Legacy keys present too — ignored.
        movies: { bytes: 9_999 },
      };
      renderWithProviders(<StorageBreakdownCard />);
      expect(screen.getByTestId("storage-bar-audiobooks")).toBeInTheDocument();
      expect(screen.queryByTestId("storage-bar-movies")).toBeNull();
    });
  });
});
