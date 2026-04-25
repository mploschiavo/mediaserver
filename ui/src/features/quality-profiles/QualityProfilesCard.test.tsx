import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const sonarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const radarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const lidarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const readarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const toggleMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useQualityProfiles: (service: string) => {
      if (service === "sonarr") return sonarrState;
      if (service === "radarr") return radarrState;
      if (service === "lidarr") return lidarrState;
      return readarrState;
    },
    useToggleQualityProfile: () => ({
      mutate: toggleMutate,
      isPending: false,
    }),
  };
});

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { QualityProfilesCard } from "./QualityProfilesCard";

describe("QualityProfilesCard", () => {
  beforeEach(() => {
    sonarrState.data = undefined;
    sonarrState.isLoading = false;
    sonarrState.error = null;
    radarrState.data = undefined;
    radarrState.isLoading = false;
    radarrState.error = null;
    lidarrState.data = undefined;
    lidarrState.isLoading = false;
    lidarrState.error = null;
    readarrState.data = undefined;
    readarrState.isLoading = false;
    readarrState.error = null;
    toggleMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders skeletons in the active tab while loading", () => {
    sonarrState.isLoading = true;
    renderWithProviders(<QualityProfilesCard />);
    expect(screen.getByTestId("quality-loading-sonarr")).toBeInTheDocument();
  });

  it("renders an empty state per service when no profiles exist", () => {
    sonarrState.data = { profiles: [] };
    renderWithProviders(<QualityProfilesCard />);
    expect(
      screen.getByText(/No quality profiles for sonarr/i),
    ).toBeInTheDocument();
  });

  it("renders sonarr profiles by default", () => {
    sonarrState.data = {
      profiles: [
        { id: 1, name: "HD-1080p", enabled: true },
        { id: 4, name: "Ultra-HD", enabled: false },
      ],
    };
    renderWithProviders(<QualityProfilesCard />);
    expect(screen.getByText("HD-1080p")).toBeInTheDocument();
    expect(screen.getByText("Ultra-HD")).toBeInTheDocument();
  });

  it("fires the toggle mutation when a switch flips", async () => {
    sonarrState.data = {
      profiles: [{ id: 1, name: "HD-1080p", enabled: false }],
    };
    toggleMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<QualityProfilesCard />);
    await userEvent.click(screen.getByTestId("quality-toggle-sonarr-1"));
    await waitFor(() => expect(toggleMutate).toHaveBeenCalledOnce());
    const args = toggleMutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(args).toEqual({ service: "sonarr", profileId: 1, enabled: true });
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("switches to the radarr tab when clicked", async () => {
    sonarrState.data = { profiles: [] };
    radarrState.data = {
      profiles: [{ id: 9, name: "HD-Bluray-1080p", enabled: true }],
    };
    renderWithProviders(<QualityProfilesCard />);
    await userEvent.click(screen.getByTestId("quality-tab-radarr"));
    expect(await screen.findByText("HD-Bluray-1080p")).toBeInTheDocument();
  });
});
