import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const importListsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const toggleMutate = vi.hoisted(() => vi.fn());
const deleteMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useImportLists: () => importListsState,
    useToggleImportList: () => ({
      mutate: toggleMutate,
      isPending: false,
    }),
    useDeleteImportList: () => ({
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

import { ImportListsCard } from "./ImportListsCard";

describe("ImportListsCard", () => {
  beforeEach(() => {
    importListsState.data = undefined;
    importListsState.isLoading = false;
    importListsState.error = null;
    toggleMutate.mockReset();
    deleteMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders skeletons while loading", () => {
    importListsState.isLoading = true;
    renderWithProviders(<ImportListsCard />);
    expect(screen.getByTestId("import-lists-loading")).toBeInTheDocument();
  });

  it("renders an error banner on failure", () => {
    importListsState.error = new Error("offline");
    renderWithProviders(<ImportListsCard />);
    expect(screen.getByTestId("import-lists-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders an empty state when no lists exist", () => {
    importListsState.data = { lists: {} };
    renderWithProviders(<ImportListsCard />);
    expect(screen.getByText(/No import lists yet/i)).toBeInTheDocument();
  });

  it("groups lists by service", () => {
    importListsState.data = {
      lists: {
        sonarr: [
          { id: 1, name: "Trakt Popular", enabled: true, listType: "trakt" },
        ],
        radarr: [
          { id: 1, name: "IMDb Top 250", enabled: false, listType: "imdb" },
        ],
      },
    };
    renderWithProviders(<ImportListsCard />);
    expect(screen.getByTestId("import-lists-sonarr")).toHaveTextContent(
      "Trakt Popular",
    );
    expect(screen.getByTestId("import-lists-radarr")).toHaveTextContent(
      "IMDb Top 250",
    );
  });

  it("fires toggle on switch click", async () => {
    importListsState.data = {
      lists: {
        sonarr: [
          { id: 1, name: "Trakt Popular", enabled: true, listType: "trakt" },
        ],
      },
    };
    toggleMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<ImportListsCard />);
    await userEvent.click(screen.getByTestId("import-list-toggle-sonarr-1"));
    await waitFor(() => expect(toggleMutate).toHaveBeenCalledOnce());
    const args = toggleMutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(args).toEqual({ service: "sonarr", listId: 1, enabled: false });
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("fires delete on trash click", async () => {
    importListsState.data = {
      lists: {
        radarr: [{ id: 8, name: "Old List", enabled: true, listType: "imdb" }],
      },
    };
    deleteMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<ImportListsCard />);
    await userEvent.click(screen.getByTestId("import-list-delete-radarr-8"));
    await waitFor(() => expect(deleteMutate).toHaveBeenCalledOnce());
    const args = deleteMutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(args).toEqual({ service: "radarr", listId: 8 });
    expect(toastSuccess).toHaveBeenCalled();
  });
});
