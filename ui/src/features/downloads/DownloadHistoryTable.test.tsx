import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const historyState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useDownloadHistory: () => historyState,
  };
});

import { DownloadHistoryTable } from "./DownloadHistoryTable";

describe("DownloadHistoryTable", () => {
  beforeEach(() => {
    historyState.data = undefined;
    historyState.isLoading = false;
    historyState.error = null;
  });

  it("renders skeletons while loading", () => {
    historyState.isLoading = true;
    renderWithProviders(<DownloadHistoryTable />);
    expect(
      screen.getByTestId("download-history-loading"),
    ).toBeInTheDocument();
  });

  it("renders an error banner on failure", () => {
    historyState.error = new Error("offline");
    renderWithProviders(<DownloadHistoryTable />);
    expect(screen.getByTestId("download-history-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders the empty state when no events", () => {
    historyState.data = { history: { sonarr: [] } };
    renderWithProviders(<DownloadHistoryTable />);
    expect(screen.getByText(/No history yet/i)).toBeInTheDocument();
  });

  it("flattens per-service history entries", () => {
    historyState.data = {
      history: {
        sonarr: [
          {
            title: "Breaking Bad S05E16",
            event: "downloadFolderImported",
            date: new Date().toISOString(),
          },
        ],
        radarr: [
          {
            title: "Inception (2010)",
            event: "grabbed",
            date: new Date().toISOString(),
          },
        ],
      },
    };
    renderWithProviders(<DownloadHistoryTable />);
    const table = screen.getByTestId("download-history");
    expect(table).toHaveTextContent("Breaking Bad");
    expect(table).toHaveTextContent("Inception");
    expect(table).toHaveTextContent("downloadFolderImported");
    expect(table).toHaveTextContent("grabbed");
  });
});
