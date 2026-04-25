import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const storiesState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useHealthStories: () => storiesState,
}));

import { HealthStoriesCard } from "./HealthStoriesCard";

describe("HealthStoriesCard", () => {
  beforeEach(() => {
    storiesState.data = undefined;
    storiesState.isLoading = false;
    storiesState.error = null;
  });

  it("renders skeletons while loading", () => {
    storiesState.isLoading = true;
    renderWithProviders(<HealthStoriesCard />);
    expect(screen.getByTestId("health-stories-loading")).toBeInTheDocument();
  });

  it("renders the empty state when there are no stories", () => {
    storiesState.data = { stories: [] };
    renderWithProviders(<HealthStoriesCard />);
    expect(screen.getByText("All systems quiet")).toBeInTheDocument();
  });

  it("treats an all-ok story list as quiet", () => {
    storiesState.data = {
      stories: [
        {
          id: "downloads",
          severity: "ok",
          headline: "Downloads working",
          description: "All good.",
        },
      ],
    };
    renderWithProviders(<HealthStoriesCard />);
    expect(screen.getByText("All systems quiet")).toBeInTheDocument();
  });

  it("renders the error message when the query fails", () => {
    storiesState.error = new Error("boom");
    renderWithProviders(<HealthStoriesCard />);
    expect(screen.getByTestId("health-stories-error")).toHaveTextContent(
      "boom",
    );
  });

  it("renders one row per non-ok story sorted by severity", () => {
    storiesState.data = {
      stories: [
        {
          id: "info-1",
          severity: "info",
          headline: "Auto-heal ran",
          description: "A service was restored.",
          affected_services: ["sonarr"],
        },
        {
          id: "crit-1",
          severity: "critical",
          headline: "Downloads broken",
          description: "qbittorrent is down.",
          affected_services: ["qbittorrent"],
          next_action: "Restart the container.",
        },
      ],
    };
    renderWithProviders(<HealthStoriesCard />);
    expect(screen.getByTestId("story-crit-1")).toBeInTheDocument();
    expect(screen.getByTestId("story-info-1")).toBeInTheDocument();
    // Sort: critical comes first.
    const list = screen.getByTestId("health-stories-list");
    const items = list.querySelectorAll("[data-testid^='story-']");
    expect(items[0]?.getAttribute("data-testid")).toBe("story-crit-1");
    // "View details" link present when next_action is provided.
    expect(screen.getByTestId("story-link-crit-1")).toBeInTheDocument();
    // No link on the info story (no next_action).
    expect(screen.queryByTestId("story-link-info-1")).toBeNull();
  });
});
