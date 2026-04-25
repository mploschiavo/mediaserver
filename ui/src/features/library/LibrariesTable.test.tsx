import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const librariesState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const addMutate = vi.hoisted(() => vi.fn());
const addState = vi.hoisted(() => ({ isPending: false }));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useLibraries: () => librariesState,
    useAddLibrary: () => ({
      mutate: addMutate,
      isPending: addState.isPending,
    }),
  };
});

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { LibrariesTable } from "./LibrariesTable";

describe("LibrariesTable", () => {
  beforeEach(() => {
    librariesState.data = undefined;
    librariesState.isLoading = false;
    librariesState.error = null;
    addMutate.mockReset();
    addState.isPending = false;
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders skeletons while loading", () => {
    librariesState.isLoading = true;
    renderWithProviders(<LibrariesTable />);
    expect(screen.getByTestId("libraries-table-loading")).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    librariesState.error = new Error("offline");
    renderWithProviders(<LibrariesTable />);
    expect(screen.getByTestId("libraries-table-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders an empty state when no libraries exist", () => {
    librariesState.data = {
      live: [],
      configured: [],
      source: "defaults",
      media_server: "jellyfin",
    };
    renderWithProviders(<LibrariesTable />);
    expect(screen.getByText(/No libraries yet/i)).toBeInTheDocument();
  });

  it("renders a row per configured library with kind badge", () => {
    // Real /api/libraries shape: configured[] is the source of truth,
    // live[] supplies item_count when Jellyfin is reachable.
    librariesState.data = {
      live: [
        {
          name: "Movies",
          collection_type: "movies",
          item_count: 891,
        },
        {
          name: "TV Shows",
          collection_type: "tvshows",
          item_count: 142,
        },
      ],
      configured: [
        { name: "Movies", collection_type: "movies", paths: ["/media/movies"] },
        { name: "TV Shows", collection_type: "tvshows", paths: ["/media/tv"] },
      ],
      source: "defaults",
      media_server: "jellyfin",
    };
    renderWithProviders(<LibrariesTable />);
    const table = screen.getByTestId("libraries-table");
    expect(table).toHaveTextContent("Movies");
    expect(table).toHaveTextContent("TV Shows");
    expect(table).toHaveTextContent("movies");
    expect(table).toHaveTextContent("tvshows");
    expect(table).toHaveTextContent("891");
    expect(table).toHaveTextContent("142");
  });

  it("submits the add-library mutation from the dialog", async () => {
    librariesState.data = {
      live: [],
      configured: [
        { name: "Movies", collection_type: "movies", paths: ["/media/movies"] },
      ],
      source: "defaults",
      media_server: "jellyfin",
    };
    addMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => {
        opts.onSuccess();
      },
    );
    renderWithProviders(<LibrariesTable />);
    await userEvent.click(screen.getByTestId("add-library-trigger"));
    await screen.findByTestId("add-library-dialog");
    await userEvent.type(screen.getByTestId("add-library-name"), "Anime");
    await userEvent.clear(screen.getByTestId("add-library-type"));
    await userEvent.type(screen.getByTestId("add-library-type"), "tvshows");
    await userEvent.type(
      screen.getByTestId("add-library-path"),
      "/media/anime",
    );
    await userEvent.click(screen.getByTestId("add-library-submit"));
    await waitFor(() => expect(addMutate).toHaveBeenCalledOnce());
    const call = addMutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(call).toEqual({
      name: "Anime",
      collection_type: "tvshows",
      paths: ["/media/anime"],
    });
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("toasts an error when name or path is missing", async () => {
    librariesState.data = {
      live: [],
      configured: [
        { name: "Movies", collection_type: "movies", paths: ["/media/movies"] },
      ],
      source: "defaults",
      media_server: "jellyfin",
    };
    renderWithProviders(<LibrariesTable />);
    await userEvent.click(screen.getByTestId("add-library-trigger"));
    await screen.findByTestId("add-library-dialog");
    await userEvent.click(screen.getByTestId("add-library-submit"));
    expect(addMutate).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalled();
  });
});
