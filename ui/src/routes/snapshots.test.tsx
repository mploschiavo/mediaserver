import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// Mock all snapshot hooks to keep the smoke test deterministic.
vi.mock("@/features/snapshots/hooks", () => ({
  useSnapshots: () => ({
    data: { snapshots: [] },
    isLoading: false,
    error: null,
  }),
  useTakeSnapshot: () => ({ mutate: vi.fn(), isPending: false }),
  useSnapshotContent: () => ({ data: undefined, isLoading: false, error: null }),
  useSnapshotDiff: () => ({ data: undefined, isLoading: false, error: null }),
  useDownloadBackup: () => ({ mutate: vi.fn(), isPending: false }),
  useRestoreBackup: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { SnapshotsRoute } from "./snapshots";

const SnapshotsPage = SnapshotsRoute.options.component as ComponentType;

describe("snapshots route", () => {
  it("renders the page header with title + description", () => {
    renderWithProviders(<SnapshotsPage />);
    // PageHeader + downstream cards may both surface "Snapshots" — the
    // page header is the first one in the DOM.
    expect(
      screen.getAllByRole("heading", { name: /Snapshots/i })[0],
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Configuration snapshots, diff, backup, and restore/i),
    ).toBeInTheDocument();
  });

  it("mounts the snapshots card and the backup/restore card", () => {
    renderWithProviders(<SnapshotsPage />);
    expect(screen.getByTestId("snapshots-card")).toBeInTheDocument();
    expect(screen.getByTestId("backup-restore-card")).toBeInTheDocument();
  });

  it("registers the route at /snapshots", () => {
    expect(
      (SnapshotsRoute.options as unknown as { path: string }).path,
    ).toBe("/snapshots");
  });
});
