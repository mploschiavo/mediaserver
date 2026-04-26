import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const snapshotsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const takeMutate = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useSnapshots: () => snapshotsState,
  useTakeSnapshot: () => ({ mutate: takeMutate, isPending: false }),
  useSnapshotContent: () => ({
    data: undefined,
    isLoading: false,
    error: null,
  }),
  useSnapshotDiff: () => ({ data: undefined, isLoading: false, error: null }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { SnapshotsTable } from "./SnapshotsTable";

describe("SnapshotsTable", () => {
  beforeEach(() => {
    snapshotsState.data = undefined;
    snapshotsState.isLoading = false;
    snapshotsState.error = null;
    takeMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });
  afterEach(() => {
    snapshotsState.data = undefined;
  });

  it("shows the empty state when there are no snapshots", () => {
    snapshotsState.data = { snapshots: [] };
    renderWithProviders(<SnapshotsTable />);
    expect(screen.getByText(/No snapshots yet/i)).toBeInTheDocument();
  });

  it("renders a row per snapshot", () => {
    snapshotsState.data = {
      snapshots: [
        {
          file: "snapshot-20260101T000000.json",
          size: 4096,
          created: "2026-01-01 00:00:00",
        },
        {
          file: "snapshot-20260102T000000.json",
          size: 8192,
          created: "2026-01-02 00:00:00",
        },
      ],
    };
    renderWithProviders(<SnapshotsTable />);
    expect(
      screen.getAllByText("snapshot-20260101T000000.json").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText("snapshot-20260102T000000.json").length,
    ).toBeGreaterThan(0);
  });

  it("fires take-snapshot mutation when Take snapshot now is clicked", async () => {
    snapshotsState.data = { snapshots: [] };
    renderWithProviders(<SnapshotsTable />);
    await userEvent.click(screen.getByTestId("snapshot-take"));
    expect(takeMutate).toHaveBeenCalledOnce();
  });

  it("opens the content drawer when View is clicked", async () => {
    snapshotsState.data = {
      snapshots: [
        {
          file: "snapshot-A.json",
          size: 100,
          created: "2026-01-01 00:00:00",
        },
      ],
    };
    renderWithProviders(<SnapshotsTable />);
    await userEvent.click(screen.getByTestId("snapshot-view-snapshot-A.json"));
    // Drawer mounts a Vaul portal — query for it once it's rendered.
    expect(
      await screen.findByTestId("snapshot-content-drawer"),
    ).toBeInTheDocument();
  });

  it("opens the diff dialog after two Diff buttons are toggled", async () => {
    snapshotsState.data = {
      snapshots: [
        { file: "A.json", size: 1, created: "t1" },
        { file: "B.json", size: 1, created: "t2" },
      ],
    };
    renderWithProviders(<SnapshotsTable />);
    await userEvent.click(screen.getByTestId("snapshot-diff-A.json"));
    await userEvent.click(screen.getByTestId("snapshot-diff-B.json"));
    expect(
      await screen.findByTestId("snapshot-diff-dialog"),
    ).toBeInTheDocument();
  });

  it("filters snapshots in-memory via the per-column filename filter", async () => {
    snapshotsState.data = {
      snapshots: [
        { file: "alpha.json", size: 100, created: "2026-01-01" },
        { file: "beta.json", size: 200, created: "2026-01-02" },
      ],
    };
    renderWithProviders(<SnapshotsTable />);
    // Two rows initially.
    expect(screen.getAllByTestId(/^snapshots-rows-row-/).length).toBe(2);
    const fileFilter = screen.getByTestId("snapshots-rows-filter-file");
    await userEvent.type(fileFilter, "alpha");
    await waitFor(() =>
      expect(screen.getAllByTestId(/^snapshots-rows-row-/).length).toBe(1),
    );
  });
});
