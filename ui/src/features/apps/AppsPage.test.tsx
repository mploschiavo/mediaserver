import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { ServiceEntry } from "./hooks";

const queryState = vi.hoisted(() => ({
  data: undefined as { services: readonly ServiceEntry[] } | undefined,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useServices: () => queryState,
  };
});

import { AppsPage } from "./AppsPage";

function reset() {
  queryState.data = { services: [] };
  queryState.isLoading = false;
  queryState.error = null;
}

function svc(overrides: Partial<ServiceEntry>): ServiceEntry {
  return {
    id: "test",
    name: "Test",
    web_ui: true,
    enabled: true,
    profiles: [],
    ...overrides,
  };
}

describe("AppsPage", () => {
  it("renders skeleton placeholders while loading", () => {
    reset();
    queryState.isLoading = true;
    renderWithProviders(<AppsPage />);
    expect(screen.getByTestId("apps-loading")).toBeInTheDocument();
  });

  it("renders an error alert when the services fetch fails", () => {
    reset();
    queryState.error = new Error("boom");
    renderWithProviders(<AppsPage />);
    expect(screen.getByTestId("apps-error")).toHaveTextContent(/boom/);
  });

  it("renders the empty state when no launchable services are reported", () => {
    reset();
    renderWithProviders(<AppsPage />);
    expect(screen.getByTestId("apps-empty")).toBeInTheDocument();
  });

  it("hides services with web_ui: false (core operations, media integrity)", () => {
    reset();
    queryState.data = {
      services: [
        svc({ id: "core", name: "Core Operations", web_ui: false }),
        svc({
          id: "media_integrity",
          name: "Media Integrity",
          web_ui: false,
        }),
        svc({ id: "sonarr", name: "Sonarr", category: "automation" }),
      ],
    };
    renderWithProviders(<AppsPage />);
    expect(screen.queryByTestId("apps-card-core")).toBeNull();
    expect(screen.queryByTestId("apps-card-media_integrity")).toBeNull();
    expect(screen.getByTestId("apps-card-sonarr")).toBeInTheDocument();
  });

  it("hides services where the compose-profile gate is not active", () => {
    reset();
    queryState.data = {
      services: [
        svc({
          id: "plex",
          name: "Plex",
          category: "media",
          profiles: ["plex"],
          enabled: false,
        }),
        svc({ id: "sonarr", name: "Sonarr", category: "automation" }),
      ],
    };
    renderWithProviders(<AppsPage />);
    expect(screen.queryByTestId("apps-card-plex")).toBeNull();
    expect(screen.getByTestId("apps-card-sonarr")).toBeInTheDocument();
  });

  it("hides the controller / envoy / flaresolverr / unpackerr launchers", () => {
    reset();
    queryState.data = {
      services: [
        svc({ id: "controller", name: "Controller" }),
        svc({ id: "envoy", name: "Envoy" }),
        svc({ id: "flaresolverr", name: "FlareSolverr" }),
        svc({ id: "unpackerr", name: "Unpackerr" }),
        svc({ id: "sonarr", name: "Sonarr", category: "automation" }),
      ],
    };
    renderWithProviders(<AppsPage />);
    expect(screen.queryByTestId("apps-card-controller")).toBeNull();
    expect(screen.queryByTestId("apps-card-envoy")).toBeNull();
    expect(screen.queryByTestId("apps-card-flaresolverr")).toBeNull();
    expect(screen.queryByTestId("apps-card-unpackerr")).toBeNull();
    expect(screen.getByTestId("apps-card-sonarr")).toBeInTheDocument();
  });

  it("renders each launchable service grouped under its category", () => {
    reset();
    queryState.data = {
      services: [
        svc({ id: "sonarr", name: "Sonarr", category: "automation" }),
        svc({ id: "radarr", name: "Radarr", category: "automation" }),
        svc({ id: "jellyfin", name: "Jellyfin", category: "media" }),
      ],
    };
    renderWithProviders(<AppsPage />);
    expect(screen.getByTestId("apps-card-sonarr")).toBeInTheDocument();
    expect(screen.getByTestId("apps-card-radarr")).toBeInTheDocument();
    expect(screen.getByTestId("apps-card-jellyfin")).toBeInTheDocument();
  });

  it("renders the default CDN icon when no override is set", () => {
    reset();
    queryState.data = {
      services: [svc({ id: "sonarr", name: "Sonarr", category: "automation" })],
    };
    renderWithProviders(<AppsPage />);
    const icon = screen.getByTestId("apps-icon-sonarr") as HTMLImageElement;
    expect(icon.src).toContain("dashboard-icons");
    expect(icon.src).toContain("sonarr");
  });

  it("uses the explicit icon_url override when one is set", () => {
    reset();
    queryState.data = {
      services: [
        svc({
          id: "sonarr",
          name: "Sonarr",
          category: "automation",
          icon_url: "https://example.test/custom.svg",
        }),
      ],
    };
    renderWithProviders(<AppsPage />);
    const icon = screen.getByTestId("apps-icon-sonarr") as HTMLImageElement;
    expect(icon.src).toBe("https://example.test/custom.svg");
  });

  it("falls back to the AppWindow glyph when the icon image errors", () => {
    reset();
    queryState.data = {
      services: [svc({ id: "sonarr", name: "Sonarr", category: "automation" })],
    };
    renderWithProviders(<AppsPage />);
    const icon = screen.getByTestId("apps-icon-sonarr") as HTMLImageElement;
    fireEvent.error(icon);
    expect(screen.queryByTestId("apps-icon-sonarr")).toBeNull();
  });

  it("places services with unrecognised categories under 'Other'", () => {
    reset();
    queryState.data = {
      services: [
        svc({
          id: "weird-service",
          name: "Weird Service",
          category: "made-up-category",
        }),
      ],
    };
    renderWithProviders(<AppsPage />);
    expect(
      screen.getByTestId("apps-card-weird-service"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Other/)).toBeInTheDocument();
  });

  it("renders the launcher href as /app/<id>/", () => {
    reset();
    queryState.data = {
      services: [svc({ id: "sonarr", name: "Sonarr", category: "automation" })],
    };
    renderWithProviders(<AppsPage />);
    const link = screen.getByTestId("apps-open-sonarr") as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/app/sonarr/");
  });
});
