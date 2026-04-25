import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// Stand in for every livetv hook the wired-up cards consume so the
// route can mount without making any network requests.
vi.mock("@/features/livetv/hooks", () => {
  const idleQuery = { data: undefined, isLoading: false, error: null };
  const idleMutation = {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    error: null,
  };
  return {
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
    useEpgHealth: () => ({
      ...idleQuery,
      data: { ok: true, last_run: new Date().toISOString() },
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { LivetvRoute } from "./livetv";

const LivetvPage = LivetvRoute.options.component as ComponentType;

describe("livetv route", () => {
  it("registers the route at /livetv", () => {
    expect(
      (LivetvRoute.options as unknown as { path: string }).path,
    ).toBe("/livetv");
  });

  it("mounts the LivetvPage with the four cards", () => {
    renderWithProviders(<LivetvPage />);
    expect(screen.getByTestId("livetv-page")).toBeInTheDocument();
    expect(screen.getByTestId("livetv-sources-card")).toBeInTheDocument();
    expect(screen.getByTestId("iptv-countries-card")).toBeInTheDocument();
    expect(screen.getByTestId("epg-providers-card")).toBeInTheDocument();
    expect(screen.getByTestId("epg-health-card")).toBeInTheDocument();
  });
});
