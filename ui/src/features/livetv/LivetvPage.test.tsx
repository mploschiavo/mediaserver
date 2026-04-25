import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const idleQuery = { data: undefined, isLoading: false, error: null };
const idleMutation = {
  mutate: vi.fn(),
  mutateAsync: vi.fn(),
  isPending: false,
  error: null,
};

vi.mock("./hooks", () => ({
  livetvKeys: {
    sources: ["livetv", "sources"],
    iptvCountries: ["livetv", "iptv-countries"],
    epgProviders: ["livetv", "epg-providers"],
    epgHealth: ["livetv", "epg-health"],
  },
  useLivetvSources: () => ({ ...idleQuery, data: { sources: [] } }),
  useSaveLivetvSources: () => idleMutation,
  useIptvCountries: () => ({ ...idleQuery, data: { countries: [] } }),
  useEpgProviders: () => ({ ...idleQuery, data: { providers: [] } }),
  useEpgHealth: () => ({ ...idleQuery, data: { ok: true, last_run: "" } }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { LivetvPage } from "./LivetvPage";

describe("LivetvPage", () => {
  // The page header (title + description) lives in the /livetv route
  // wrapper now — see `src/routes/livetv.tsx`. This test file asserts
  // only the in-column card composition, which is what this
  // component owns.
  it("composes the four Live-TV cards", () => {
    renderWithProviders(<LivetvPage />);
    expect(screen.getByTestId("livetv-page")).toBeInTheDocument();
    expect(screen.getByTestId("livetv-sources-card")).toBeInTheDocument();
    expect(screen.getByTestId("iptv-countries-card")).toBeInTheDocument();
    expect(screen.getByTestId("epg-providers-card")).toBeInTheDocument();
    expect(screen.getByTestId("epg-health-card")).toBeInTheDocument();
  });
});
