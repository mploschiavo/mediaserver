import type { ComponentType } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

// `useLibraryStats` was retired in v1.3.2 — `LibraryStatsTiles` now
// derives counts from `useLibraries()` (`{live[], configured[]}` shape
// from the live `/api/libraries` payload). We mock both for parity
// with any consumer that hasn't migrated.
const statsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));
const librariesState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useLibraryStats: () => statsState,
  };
});

vi.mock("@/features/library/hooks", () => ({
  useLibraries: () => librariesState,
}));

// Stub each feature component out — content.test focuses on the
// composition (tab switching + stats tile wiring), not the children.
vi.mock("@/features/library/LibrariesTable", () => ({
  LibrariesTable: () => <div data-testid="stub-libraries-table" />,
}));
vi.mock("@/features/library/RecentAdditionsCard", () => ({
  RecentAdditionsCard: () => <div data-testid="stub-recent-additions" />,
}));
vi.mock("@/features/library/LibraryDataSourceBanner", () => ({
  LibraryDataSourceBanner: () => (
    <div data-testid="stub-library-defaults-banner" />
  ),
}));
vi.mock("@/features/indexers/IndexersTable", () => ({
  IndexersTable: () => <div data-testid="stub-indexers-table" />,
}));
vi.mock("@/features/quality-profiles/QualityProfilesCard", () => ({
  QualityProfilesCard: () => <div data-testid="stub-quality-profiles" />,
}));
vi.mock("@/features/discovery/ImportListsCard", () => ({
  ImportListsCard: () => <div data-testid="stub-import-lists" />,
}));
vi.mock("@/features/discovery/DiscoveryListsCard", () => ({
  DiscoveryListsCard: () => <div data-testid="stub-discovery-lists" />,
}));
vi.mock("@/features/downloads/ActiveDownloadsTable", () => ({
  ActiveDownloadsTable: () => <div data-testid="stub-active-downloads" />,
}));
vi.mock("@/features/downloads/DownloadHistoryTable", () => ({
  DownloadHistoryTable: () => <div data-testid="stub-download-history" />,
}));
vi.mock("@/features/downloads/DownloadAnalyticsCard", () => ({
  DownloadAnalyticsCard: () => <div data-testid="stub-download-analytics" />,
}));
vi.mock("@/features/custom-services/CustomServiceCard", () => ({
  CustomServiceCard: () => <div data-testid="stub-custom-services" />,
}));
vi.mock("@/features/custom-formats/CustomFormatsCard", () => ({
  CustomFormatsCard: () => <div data-testid="stub-custom-formats" />,
}));

import { Route as ContentRoute } from "./content";

const ContentPage = ContentRoute.options.component as ComponentType;

describe("content route", () => {
  beforeEach(() => {
    statsState.data = undefined;
    statsState.isLoading = false;
    statsState.error = null;
    librariesState.data = undefined;
    librariesState.isLoading = false;
    librariesState.error = null;
  });

  it("shows skeletons while stats load", () => {
    librariesState.isLoading = true;
    renderWithProviders(<ContentPage />);
    expect(
      screen.getAllByTestId("library-stats-skeleton").length,
    ).toBeGreaterThan(0);
  });

  it("renders the four stat cards when populated", () => {
    librariesState.data = {
      live: [
        { name: "Movies", collection_type: "movies", paths: [], item_count: 12 },
        { name: "Books", collection_type: "books", paths: [], item_count: 99 },
      ],
      configured: [],
      source: "live",
      media_server: "jellyfin",
    };
    renderWithProviders(<ContentPage />);
    expect(screen.getByTestId("library-stat-movies")).toHaveTextContent("12");
    expect(screen.getByTestId("library-stat-books")).toHaveTextContent("99");
  });

  it("renders an error banner with the message when stats fail", () => {
    librariesState.error = new Error("offline");
    renderWithProviders(<ContentPage />);
    const banner = screen.getByTestId("library-stats-error");
    expect(banner).toHaveTextContent("offline");
  });

  it("renders the library tab by default", () => {
    statsState.data = { movies: 1, tv: 0, tracks: 0, books: 0 };
    renderWithProviders(<ContentPage />);
    expect(screen.getByTestId("stub-libraries-table")).toBeInTheDocument();
    expect(screen.getByTestId("stub-recent-additions")).toBeInTheDocument();
  });

  it("renders all six tab triggers", () => {
    renderWithProviders(<ContentPage />);
    expect(screen.getByTestId("content-tab-library")).toBeInTheDocument();
    expect(screen.getByTestId("content-tab-indexers")).toBeInTheDocument();
    expect(screen.getByTestId("content-tab-quality")).toBeInTheDocument();
    expect(screen.getByTestId("content-tab-discovery")).toBeInTheDocument();
    expect(screen.getByTestId("content-tab-custom")).toBeInTheDocument();
    expect(screen.getByTestId("content-tab-downloads")).toBeInTheDocument();
  });

  it("switches to the indexers tab", async () => {
    renderWithProviders(<ContentPage />);
    await userEvent.click(screen.getByTestId("content-tab-indexers"));
    expect(screen.getByTestId("stub-indexers-table")).toBeInTheDocument();
  });

  it("switches to the downloads tab and renders all download children", async () => {
    renderWithProviders(<ContentPage />);
    await userEvent.click(screen.getByTestId("content-tab-downloads"));
    expect(screen.getByTestId("stub-active-downloads")).toBeInTheDocument();
    expect(screen.getByTestId("stub-download-analytics")).toBeInTheDocument();
    expect(screen.getByTestId("stub-download-history")).toBeInTheDocument();
  });

  it("switches to the quality tab", async () => {
    renderWithProviders(<ContentPage />);
    await userEvent.click(screen.getByTestId("content-tab-quality"));
    expect(screen.getByTestId("stub-quality-profiles")).toBeInTheDocument();
  });

  it("switches to the discovery tab and renders both children", async () => {
    renderWithProviders(<ContentPage />);
    await userEvent.click(screen.getByTestId("content-tab-discovery"));
    expect(screen.getByTestId("stub-import-lists")).toBeInTheDocument();
    expect(screen.getByTestId("stub-discovery-lists")).toBeInTheDocument();
  });

  it("switches to the custom tab and renders both children", async () => {
    renderWithProviders(<ContentPage />);
    await userEvent.click(screen.getByTestId("content-tab-custom"));
    expect(screen.getByTestId("stub-custom-services")).toBeInTheDocument();
    expect(screen.getByTestId("stub-custom-formats")).toBeInTheDocument();
  });

  it("mounts the LibraryDataSourceBanner above the content tabs", () => {
    renderWithProviders(<ContentPage />);
    const banner = screen.getByTestId("stub-library-defaults-banner");
    const tabs = screen.getByTestId("content-tabs");
    expect(banner).toBeInTheDocument();
    // Banner appears in DOM before the tabs (it sits above them).
    expect(
      banner.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
