import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const recentState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useRecentLibraryAdditions: () => recentState,
  };
});

import { RecentAdditionsCard } from "./RecentAdditionsCard";

describe("RecentAdditionsCard", () => {
  beforeEach(() => {
    recentState.data = undefined;
    recentState.isLoading = false;
    recentState.error = null;
  });

  it("renders skeletons while loading", () => {
    recentState.isLoading = true;
    renderWithProviders(<RecentAdditionsCard />);
    expect(
      screen.getByTestId("recent-additions-loading"),
    ).toBeInTheDocument();
  });

  it("renders the empty state when there are no items", () => {
    recentState.data = { recent: {} };
    renderWithProviders(<RecentAdditionsCard />);
    expect(screen.getByText(/Your library is quiet/i)).toBeInTheDocument();
  });

  it("renders the error message when the query fails", () => {
    recentState.error = new Error("offline");
    renderWithProviders(<RecentAdditionsCard />);
    expect(
      screen.getByTestId("recent-additions-error"),
    ).toHaveTextContent("offline");
  });

  it("flattens per-service entries into a list", () => {
    recentState.data = {
      recent: {
        sonarr: [{ title: "Breaking Bad", added: new Date().toISOString() }],
        radarr: [{ title: "Inception" }],
      },
    };
    renderWithProviders(<RecentAdditionsCard />);
    const list = screen.getByTestId("recent-additions-list");
    expect(list).toHaveTextContent("Breaking Bad");
    expect(list).toHaveTextContent("Inception");
  });

  // Regression test sourced from the live ground-truth payload at
  // ui/.ratchets/notes/API-RESPONSE-SHAPES-2026-04-25.txt. The wave-4
  // agent assumed a top-level `items[]`; the controller actually emits
  // a service-keyed map under `recent`.
  it("handles the live /api/recent shape (radarr+sonarr buckets)", () => {
    recentState.data = {
      recent: {
        radarr: [
          { title: "The Super Mario Galaxy Movie", added: "" },
          { title: "Your Heart Will Be Broken", added: "" },
          { title: "Vengeance", added: "" },
          { title: "Project Hail Mary", added: "" },
          { title: "The Mortuary Assistant", added: "" },
        ],
        sonarr: [
          { title: "Breaking Bad", added: "" },
          { title: "Stranger Things", added: "" },
          { title: "The Last of Us", added: "" },
          { title: "The Boys", added: "" },
          { title: "Severance", added: "" },
        ],
      },
    };
    renderWithProviders(<RecentAdditionsCard />);
    const list = screen.getByTestId("recent-additions-list");
    // The default limit is 6; both buckets contribute, sorted by `added`
    // desc with ties (all empty) preserving discovery order.
    expect(list).toHaveTextContent("The Super Mario Galaxy Movie");
    expect(list).toHaveTextContent("Breaking Bad");
  });
});
