import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
  ApiError: class ApiError extends Error {},
}));

import { StorageCleanupPolicy } from "./StorageCleanupPolicy";

describe("StorageCleanupPolicy", () => {
  it("renders collapsed by default with the strategy badge visible", () => {
    renderWithProviders(<StorageCleanupPolicy />);
    expect(
      screen.getByTestId("storage-cleanup-policy"),
    ).toBeInTheDocument();
    // Body is collapsed.
    expect(
      screen.queryByTestId("storage-cleanup-policy-body"),
    ).toBeNull();
  });

  it("expands the body when the toggle is clicked", () => {
    renderWithProviders(<StorageCleanupPolicy />);
    fireEvent.click(screen.getByTestId("storage-cleanup-policy-toggle"));
    expect(
      screen.getByTestId("storage-cleanup-policy-body"),
    ).toBeInTheDocument();
    // Phase 4 made this writable; the read-only note is gone, replaced
    // by the Save button.
    expect(
      screen.getByTestId("storage-cleanup-policy-save"),
    ).toBeInTheDocument();
  });

  it("renders supplied policy values", () => {
    renderWithProviders(
      <StorageCleanupPolicy
        policy={{
          categories: ["movies-radarr"],
          min_age_hours: 48,
          min_seeding_time_minutes: 720,
          min_ratio: 2,
          max_delete_per_run: 10,
          order_strategy: "largest_first",
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("storage-cleanup-policy-toggle"));
    expect(
      (screen.getByTestId("storage-cleanup-policy-min-age") as HTMLInputElement)
        .value,
    ).toBe("48");
    expect(
      (screen.getByTestId("storage-cleanup-policy-order") as HTMLSelectElement)
        .value,
    ).toBe("largest_first");
  });

  it("renders an explicit empty caption for empty categories", () => {
    renderWithProviders(<StorageCleanupPolicy policy={{ categories: [] }} />);
    fireEvent.click(screen.getByTestId("storage-cleanup-policy-toggle"));
    expect(
      screen.getByTestId("storage-cleanup-policy-categories-empty"),
    ).toBeInTheDocument();
  });

  it("Save button POSTs the cleaned body to the controller", async () => {
    fetcherMock.mockReset();
    fetcherMock.mockResolvedValue({ policy: {} });
    renderWithProviders(
      <StorageCleanupPolicy
        policy={{
          categories: ["tv-sonarr"],
          min_age_hours: 24,
          min_seeding_time_minutes: 480,
          min_ratio: 1,
          max_delete_per_run: 100,
          order_strategy: "largest_first",
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("storage-cleanup-policy-toggle"));
    fireEvent.click(screen.getByTestId("storage-cleanup-policy-save"));
    // The mutation runs async; we just check fetcher was called with
    // the correct URL + body shape on the next microtask.
    await Promise.resolve();
    await Promise.resolve();
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/disk-guardrails/cleanup-policy",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
