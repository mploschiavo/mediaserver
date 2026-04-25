import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const downloadsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useDownloads: () => downloadsState,
  };
});

import { ActiveDownloadsTable } from "./ActiveDownloadsTable";

describe("ActiveDownloadsTable", () => {
  beforeEach(() => {
    downloadsState.data = undefined;
    downloadsState.isLoading = false;
    downloadsState.error = null;
  });

  it("renders skeletons while loading", () => {
    downloadsState.isLoading = true;
    renderWithProviders(<ActiveDownloadsTable />);
    expect(
      screen.getByTestId("active-downloads-loading"),
    ).toBeInTheDocument();
  });

  it("renders an error banner on failure", () => {
    downloadsState.error = new Error("offline");
    renderWithProviders(<ActiveDownloadsTable />);
    expect(screen.getByTestId("active-downloads-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders the empty state when nothing is downloading", () => {
    downloadsState.data = { qbittorrent: { items: [] }, sabnzbd: { items: [] } };
    renderWithProviders(<ActiveDownloadsTable />);
    expect(screen.getByText(/Nothing downloading/i)).toBeInTheDocument();
  });

  it("renders both qBittorrent and SABnzbd queue items", () => {
    downloadsState.data = {
      qbittorrent: {
        active: 1,
        items: [
          {
            name: "Dune.Part.Two.2024",
            progress: 67.3,
            state: "downloading",
            size: 4_200_000_000,
            dlspeed: 12_500_000,
          },
        ],
      },
      sabnzbd: {
        active: 1,
        items: [{ name: "Shogun.S01.NZB", progress: 0.452 }],
      },
    };
    renderWithProviders(<ActiveDownloadsTable />);
    const table = screen.getByTestId("active-downloads");
    expect(table).toHaveTextContent("Dune.Part.Two.2024");
    expect(table).toHaveTextContent("Shogun.S01.NZB");
    expect(table).toHaveTextContent("qbittorrent");
    expect(table).toHaveTextContent("sabnzbd");
    // qbit progress is already a percentage; sab is a ratio that
    // should be normalised to a percentage.
    expect(table).toHaveTextContent("67.3%");
    expect(table).toHaveTextContent("45.2%");
  });
});
