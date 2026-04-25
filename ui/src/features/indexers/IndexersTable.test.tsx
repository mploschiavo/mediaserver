import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const indexersState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const statsState = vi.hoisted(() => ({
  data: { stats: [] as unknown[] } as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const toggleMutate = vi.hoisted(() => vi.fn());
const deleteMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useIndexers: () => indexersState,
    useIndexerStats: () => statsState,
    useToggleIndexer: () => ({
      mutate: toggleMutate,
      isPending: false,
    }),
    useDeleteIndexer: () => ({
      mutate: deleteMutate,
      isPending: false,
    }),
  };
});

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { IndexersTable } from "./IndexersTable";

describe("IndexersTable", () => {
  beforeEach(() => {
    indexersState.data = undefined;
    indexersState.isLoading = false;
    indexersState.error = null;
    statsState.data = { stats: [] };
    toggleMutate.mockReset();
    deleteMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders skeletons while loading", () => {
    indexersState.isLoading = true;
    renderWithProviders(<IndexersTable />);
    expect(screen.getByTestId("indexers-table-loading")).toBeInTheDocument();
  });

  it("renders an error banner on failure", () => {
    indexersState.error = new Error("offline");
    renderWithProviders(<IndexersTable />);
    expect(screen.getByTestId("indexers-table-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders the empty state when no indexers exist", () => {
    indexersState.data = { indexers: [] };
    renderWithProviders(<IndexersTable />);
    expect(screen.getByText(/No indexers configured/i)).toBeInTheDocument();
  });

  it("renders rows with stats inline", () => {
    indexersState.data = {
      indexers: [
        { id: 1, name: "1337x", enable: true, protocol: "torrent" },
        { id: 2, name: "NZBgeek", enable: false, protocol: "usenet" },
      ],
    };
    statsState.data = {
      stats: [
        { indexerId: 1, numberOfGrabs: 14, numberOfRssQueries: 200 },
        { indexerId: 2, numberOfGrabs: 0, lastError: "auth failed" },
      ],
    };
    renderWithProviders(<IndexersTable />);
    const table = screen.getByTestId("indexers-table");
    expect(table).toHaveTextContent("1337x");
    expect(table).toHaveTextContent("NZBgeek");
    expect(table).toHaveTextContent("14 grabs");
    // Last-error inline. ResponsiveTable mounts both desktop + mobile
    // branches under happy-dom; pick the first occurrence rather than
    // asserting a single match.
    const lastError = screen.getAllByTestId("indexer-last-error");
    expect(lastError.length).toBeGreaterThan(0);
    expect(lastError[0]).toHaveTextContent("auth failed");
  });

  it("fires toggle mutation when the switch is clicked", async () => {
    indexersState.data = {
      indexers: [{ id: 1, name: "1337x", enable: true, protocol: "torrent" }],
    };
    toggleMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<IndexersTable />);
    // ResponsiveTable mounts both desktop + mobile branches under
    // happy-dom; pick the first occurrence rather than asserting one.
    const toggles = screen.getAllByTestId("indexer-toggle-1");
    expect(toggles.length).toBeGreaterThan(0);
    await userEvent.click(toggles[0]!);
    await waitFor(() => expect(toggleMutate).toHaveBeenCalledOnce());
    const args = toggleMutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(args).toEqual({ indexerId: 1, enable: false });
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("fires delete mutation when the trash button is clicked", async () => {
    indexersState.data = {
      indexers: [{ id: 7, name: "RARBG", enable: true, protocol: "torrent" }],
    };
    deleteMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<IndexersTable />);
    // ResponsiveTable mounts both desktop + mobile branches under
    // happy-dom; pick the first occurrence rather than asserting one.
    const deletes = screen.getAllByTestId("indexer-delete-7");
    expect(deletes.length).toBeGreaterThan(0);
    await userEvent.click(deletes[0]!);
    await waitFor(() => expect(deleteMutate).toHaveBeenCalledOnce());
    const args = deleteMutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(args).toEqual({ indexerId: 7 });
    expect(toastSuccess).toHaveBeenCalled();
  });
});
