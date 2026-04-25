import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const librariesState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const healthState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useLibraries: () => librariesState,
  };
});

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>(
    "@/api/hooks",
  );
  return {
    ...actual,
    useHealth: () => healthState,
  };
});

import {
  LibraryDataSourceBanner,
  __INTERNAL__,
  isJellyfinReachable,
  shouldShowBanner,
} from "./LibraryDataSourceBanner";

function reset() {
  librariesState.data = undefined;
  healthState.data = undefined;
  window.localStorage.removeItem(__INTERNAL__.DISMISS_KEY);
}

describe("LibraryDataSourceBanner", () => {
  beforeEach(reset);

  it("renders when source=defaults AND jellyfin is reachable", () => {
    librariesState.data = {
      live: [],
      configured: [],
      source: "defaults",
      media_server: "jellyfin",
    };
    healthState.data = { services: { jellyfin: { status: "ok" } } };
    renderWithProviders(<LibraryDataSourceBanner />);
    expect(screen.getByTestId("library-defaults-banner")).toBeInTheDocument();
  });

  it("does not render when source is not 'defaults'", () => {
    librariesState.data = {
      live: [],
      configured: [],
      source: "live",
      media_server: "jellyfin",
    };
    healthState.data = { services: { jellyfin: { status: "ok" } } };
    renderWithProviders(<LibraryDataSourceBanner />);
    expect(screen.queryByTestId("library-defaults-banner")).toBeNull();
  });

  it("does not render when jellyfin is unreachable", () => {
    librariesState.data = {
      live: [],
      configured: [],
      source: "defaults",
      media_server: "jellyfin",
    };
    healthState.data = { services: { jellyfin: { status: "error" } } };
    renderWithProviders(<LibraryDataSourceBanner />);
    expect(screen.queryByTestId("library-defaults-banner")).toBeNull();
  });

  it("links the diagnose CTA to /jobs?filter=discover-api-keys", () => {
    librariesState.data = { live: [], configured: [], source: "defaults" };
    healthState.data = { services: { jellyfin: { status: "ok" } } };
    renderWithProviders(<LibraryDataSourceBanner />);
    const link = screen.getByTestId(
      "library-defaults-banner-diagnose",
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(
      "/jobs?filter=discover-api-keys",
    );
  });

  it("hides itself when the operator dismisses it", async () => {
    librariesState.data = { live: [], configured: [], source: "defaults" };
    healthState.data = { services: { jellyfin: { status: "ok" } } };
    renderWithProviders(<LibraryDataSourceBanner />);
    await userEvent.click(
      screen.getByTestId("library-defaults-banner-dismiss"),
    );
    expect(screen.queryByTestId("library-defaults-banner")).toBeNull();
  });

  it("persists the dismissal flag in localStorage", async () => {
    librariesState.data = { live: [], configured: [], source: "defaults" };
    healthState.data = { services: { jellyfin: { status: "ok" } } };
    renderWithProviders(<LibraryDataSourceBanner />);
    await userEvent.click(
      screen.getByTestId("library-defaults-banner-dismiss"),
    );
    expect(window.localStorage.getItem(__INTERNAL__.DISMISS_KEY)).toBe(
      String(__INTERNAL__.SESSION_START),
    );
  });

  it("ignores a stale dismissal flag from a previous tab session", () => {
    // Simulate a previous tab's dismissal — different timestamp.
    window.localStorage.setItem(__INTERNAL__.DISMISS_KEY, "1");
    librariesState.data = { live: [], configured: [], source: "defaults" };
    healthState.data = { services: { jellyfin: { status: "ok" } } };
    renderWithProviders(<LibraryDataSourceBanner />);
    // Stale flag (ts=1) doesn't match the current SESSION_START, so
    // the banner re-renders.
    expect(screen.getByTestId("library-defaults-banner")).toBeInTheDocument();
  });
});

describe("isJellyfinReachable", () => {
  it("matches the documented services map", () => {
    expect(
      isJellyfinReachable({
        services: { jellyfin: { status: "ok" } },
      } as unknown as Parameters<typeof isJellyfinReachable>[0]),
    ).toBe(true);
  });
  it("matches a flat-field shape", () => {
    expect(isJellyfinReachable({ jellyfin: "up" } as unknown as Parameters<typeof isJellyfinReachable>[0])).toBe(true);
  });
  it("returns false for an error status", () => {
    expect(
      isJellyfinReachable({
        services: { jellyfin: { status: "error" } },
      } as unknown as Parameters<typeof isJellyfinReachable>[0]),
    ).toBe(false);
  });
  it("returns false for missing health", () => {
    expect(isJellyfinReachable(undefined)).toBe(false);
  });
});

describe("shouldShowBanner", () => {
  it("returns true only when both predicates hold", () => {
    expect(
      shouldShowBanner(
        {
          live: [],
          configured: [],
          source: "defaults",
          media_server: "jellyfin",
        },
        { services: { jellyfin: { status: "ok" } } } as never,
      ),
    ).toBe(true);
  });
  it("returns false on persisted source", () => {
    expect(
      shouldShowBanner(
        {
          live: [],
          configured: [],
          source: "persisted",
          media_server: "jellyfin",
        },
        { services: { jellyfin: { status: "ok" } } } as never,
      ),
    ).toBe(false);
  });
});
