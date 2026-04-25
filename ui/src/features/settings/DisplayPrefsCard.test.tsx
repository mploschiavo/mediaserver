import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const prefsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const saveMutate = vi.hoisted(() => vi.fn());
const saveState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useDisplayPreferences: () => prefsState,
    useSaveDisplayPreferences: () => ({
      mutate: saveMutate,
      isPending: saveState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { DisplayPrefsCard } from "./DisplayPrefsCard";

// Real /api/display-preferences payload (sourced from
// ui/.ratchets/notes/API-RESPONSE-SHAPES-2026-04-25.txt). These are
// JELLYFIN client display knobs, NOT the dashboard's browser theme.
const LIVE_PAYLOAD = {
  enabled: true,
  show_backdrop: true,
  custom_prefs: {
    enableNextVideoInfoOverlay: true,
    enableBackdrops: true,
    homesection0: "smalllibrarytiles",
    homesection1: "resume",
  },
  per_library_prefs: {
    movies: { SortBy: "DateCreated,SortName", SortOrder: "Descending" },
    tv: { SortBy: "DateCreated,SortName", SortOrder: "Descending" },
  },
  clients: ["emby", "jellyfin-web"],
};

function reset() {
  prefsState.data = undefined;
  prefsState.isLoading = false;
  prefsState.error = null;
  saveMutate.mockReset();
  saveState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
}

describe("DisplayPrefsCard (Jellyfin display preferences)", () => {
  beforeEach(reset);

  it("renders a skeleton while loading", () => {
    prefsState.isLoading = true;
    renderWithProviders(<DisplayPrefsCard />);
    expect(screen.getByTestId("display-prefs-loading")).toBeInTheDocument();
  });

  it("renders the error banner on failure", () => {
    prefsState.error = new Error("nope");
    renderWithProviders(<DisplayPrefsCard />);
    expect(screen.getByTestId("display-prefs-error")).toHaveTextContent(
      "nope",
    );
  });

  it("seeds the toggles from the live payload (enabled + backdrop)", () => {
    prefsState.data = LIVE_PAYLOAD;
    renderWithProviders(<DisplayPrefsCard />);
    expect(screen.getByTestId("display-prefs-enabled")).toBeInTheDocument();
    expect(screen.getByTestId("display-prefs-backdrop")).toBeInTheDocument();
  });

  it("surfaces the configured Jellyfin clients as badges", () => {
    prefsState.data = LIVE_PAYLOAD;
    renderWithProviders(<DisplayPrefsCard />);
    const clientsRegion = screen.getByTestId("display-prefs-clients");
    expect(clientsRegion).toHaveTextContent("emby");
    expect(clientsRegion).toHaveTextContent("jellyfin-web");
  });

  it("surfaces the per-library override keys as badges", () => {
    prefsState.data = LIVE_PAYLOAD;
    renderWithProviders(<DisplayPrefsCard />);
    const region = screen.getByTestId("display-prefs-libraries");
    expect(region).toHaveTextContent("movies");
    expect(region).toHaveTextContent("tv");
  });

  it("surfaces a sample of custom_prefs keys", () => {
    prefsState.data = LIVE_PAYLOAD;
    renderWithProviders(<DisplayPrefsCard />);
    const region = screen.getByTestId("display-prefs-custom");
    expect(region).toHaveTextContent("homesection0");
    expect(region).toHaveTextContent("smalllibrarytiles");
  });

  it("fires the mutation with the merged payload when Save is clicked", async () => {
    prefsState.data = LIVE_PAYLOAD;
    renderWithProviders(<DisplayPrefsCard />);
    await userEvent.click(screen.getByTestId("display-prefs-save"));
    expect(saveMutate).toHaveBeenCalledOnce();
    expect(saveMutate.mock.calls[0]?.[0]).toMatchObject({
      enabled: true,
      show_backdrop: true,
      // Merged payload preserves the rest of the wire shape.
      clients: ["emby", "jellyfin-web"],
    });
  });

  it("toasts on save success", async () => {
    prefsState.data = LIVE_PAYLOAD;
    saveMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<DisplayPrefsCard />);
    await userEvent.click(screen.getByTestId("display-prefs-save"));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        "Jellyfin display preferences saved",
      ),
    );
  });

  it("toasts on save failure", async () => {
    prefsState.data = LIVE_PAYLOAD;
    saveMutate.mockImplementation(
      (_vars: unknown, opts: { onError: (e: Error) => void }) =>
        opts.onError(new Error("oops")),
    );
    renderWithProviders(<DisplayPrefsCard />);
    await userEvent.click(screen.getByTestId("display-prefs-save"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("oops"));
  });
});
