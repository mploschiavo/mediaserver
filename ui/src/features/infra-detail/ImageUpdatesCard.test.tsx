import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const updatesState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useImageUpdates: () => updatesState,
}));

import { ImageUpdatesCard } from "./ImageUpdatesCard";

describe("ImageUpdatesCard", () => {
  beforeEach(() => {
    updatesState.data = undefined;
    updatesState.isLoading = false;
    updatesState.error = null;
  });

  it("renders skeletons while loading", () => {
    updatesState.isLoading = true;
    renderWithProviders(<ImageUpdatesCard />);
    expect(screen.getByTestId("image-updates-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no updates are reported", () => {
    updatesState.data = { updates: [] };
    renderWithProviders(<ImageUpdatesCard />);
    expect(screen.getByText("All images up to date")).toBeInTheDocument();
  });

  it("renders the error banner when the query fails", () => {
    updatesState.error = new Error("nope");
    renderWithProviders(<ImageUpdatesCard />);
    expect(screen.getByTestId("image-updates-error")).toHaveTextContent("nope");
  });

  it("renders rows sorted newest first by available_at", () => {
    updatesState.data = {
      updates: [
        {
          service: "sonarr",
          current: "4.0.13",
          latest: "4.0.14",
          available_at: "2026-04-20T08:00:00Z",
        },
        {
          service: "radarr",
          current: "5.0.0",
          latest: "5.1.0",
          available_at: "2026-04-23T08:00:00Z",
        },
      ],
    };
    renderWithProviders(<ImageUpdatesCard />);
    // Both desktop + mobile branches mount; service names appear at
    // least once.
    expect(screen.getAllByText("sonarr").length).toBeGreaterThan(0);
    expect(screen.getAllByText("radarr").length).toBeGreaterThan(0);
    // Radarr is the newest so its row should appear first in the
    // desktop table body.
    const desktop = document.querySelector(
      "[data-testid='responsive-table-desktop']",
    );
    const firstRow = desktop?.querySelector("tbody tr");
    expect(firstRow?.textContent).toContain("radarr");
  });

  it("falls back to OpenAPI image[] + tag/image_created shape", () => {
    updatesState.data = {
      images: [
        {
          name: "jellyfin",
          image: "lscr.io/linuxserver/jellyfin:10.10.0",
          tag: "10.10.0",
          image_created: "2026-04-22T12:00:00Z",
        },
      ],
    };
    renderWithProviders(<ImageUpdatesCard />);
    expect(screen.getAllByText("jellyfin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("10.10.0").length).toBeGreaterThan(0);
  });
});
