import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

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
    expect(
      screen.getByTestId("storage-cleanup-policy-readonly-note"),
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
      screen.getByTestId("storage-cleanup-policy-min-age").textContent,
    ).toBe("48");
    expect(
      screen.getByTestId("storage-cleanup-policy-order").textContent,
    ).toBe("largest_first");
  });

  it("renders an explicit empty caption for empty categories", () => {
    renderWithProviders(<StorageCleanupPolicy policy={{ categories: [] }} />);
    fireEvent.click(screen.getByTestId("storage-cleanup-policy-toggle"));
    expect(
      screen.getByTestId("storage-cleanup-policy-categories-empty"),
    ).toBeInTheDocument();
  });
});
