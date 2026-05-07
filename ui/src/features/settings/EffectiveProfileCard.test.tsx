import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const profileState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useProfileYaml: () => profileState,
  };
});

import {
  EffectiveProfileCard,
  extractProfile,
  readProfileYaml,
} from "./EffectiveProfileCard";

const SAMPLE_YAML = `
network:
  gateway_host: media.example.com
  base_domain: example.com
  strategy: subdomain
services:
  - sonarr
  - radarr
  - jellyfin
auth:
  mode: forwardauth
  oidc:
    enabled: true
routing:
  direct_hosts:
    - admin.example.com
    - bypass.example.com
media-server:
  api_url: http://jellyfin:8096
  api_key: very-real-jellyfin-key
iptv:
  tuner: http://hdhomerun.local:5004
  guide: http://hdhomerun.local:5004/guide.xml
`.trim();

describe("EffectiveProfileCard", () => {
  beforeEach(() => {
    profileState.data = undefined;
    profileState.isLoading = false;
    profileState.error = null;
  });

  it("renders skeletons while the profile is loading", () => {
    profileState.isLoading = true;
    renderWithProviders(<EffectiveProfileCard />);
    expect(screen.getByTestId("effective-profile-loading")).toBeInTheDocument();
  });

  it("renders the error state when the hook errors", () => {
    profileState.error = new Error("kaboom");
    renderWithProviders(<EffectiveProfileCard />);
    // EffectiveProfileCard now delegates to the shared ApiErrorTile;
    // the generic (non-ApiError) variant renders under api-error-tile-generic.
    expect(screen.getByTestId("api-error-tile-generic")).toHaveTextContent(
      "kaboom",
    );
  });

  it("renders the empty-state when the YAML is missing", () => {
    profileState.data = { yaml: "" };
    renderWithProviders(<EffectiveProfileCard />);
    expect(screen.getByTestId("effective-profile-empty")).toBeInTheDocument();
  });

  it("renders all six sections with extracted values", () => {
    profileState.data = { yaml: SAMPLE_YAML };
    renderWithProviders(<EffectiveProfileCard />);

    expect(screen.getByTestId("profile-section-network")).toBeInTheDocument();
    expect(screen.getByTestId("profile-network-gateway-host")).toHaveTextContent(
      "media.example.com",
    );
    expect(screen.getByTestId("profile-network-base-domain")).toHaveTextContent(
      "example.com",
    );
    expect(screen.getByTestId("profile-network-strategy")).toHaveTextContent(
      "subdomain",
    );

    const services = screen.getByTestId("profile-services-list");
    expect(services).toHaveTextContent("sonarr");
    expect(services).toHaveTextContent("radarr");
    expect(services).toHaveTextContent("jellyfin");

    expect(screen.getByTestId("profile-auth-mode")).toHaveTextContent(
      "forwardauth",
    );
    expect(screen.getByTestId("profile-auth-oidc")).toHaveTextContent("yes");

    const direct = screen.getByTestId("profile-routing-direct-hosts");
    expect(direct).toHaveTextContent("admin.example.com");
    expect(direct).toHaveTextContent("bypass.example.com");

    expect(
      screen.getByTestId("profile-media-server-api-url"),
    ).toHaveTextContent("http://jellyfin:8096");
    expect(
      screen.getByTestId("profile-media-server-has-key"),
    ).toHaveTextContent("yes");
  });

  it("never renders the actual api_key value, only its presence", () => {
    profileState.data = { yaml: SAMPLE_YAML };
    renderWithProviders(<EffectiveProfileCard />);
    const card = screen.getByTestId("effective-profile-card");
    expect(card.textContent ?? "").not.toContain("very-real-jellyfin-key");
  });

  it("flags has_key=false when api_key is absent", () => {
    profileState.data = {
      yaml: `media-server:\n  api_url: http://jf:8096\n`,
    };
    renderWithProviders(<EffectiveProfileCard />);
    expect(
      screen.getByTestId("profile-media-server-has-key"),
    ).toHaveTextContent("no");
  });

  it("renders empty 'not configured' suffix when a section is bare", () => {
    profileState.data = {
      yaml: `services:\n  - sonarr\n`,
    };
    renderWithProviders(<EffectiveProfileCard />);
    const network = screen.getByTestId("profile-section-network");
    expect(network.textContent ?? "").toContain("not configured");
  });

  it("emits a TODO source footnote per section", () => {
    profileState.data = { yaml: SAMPLE_YAML };
    renderWithProviders(<EffectiveProfileCard />);
    expect(
      screen.getByTestId("profile-section-source-network"),
    ).toHaveTextContent(/Source: profile\.yaml/);
  });
});

describe("extractProfile", () => {
  it("returns an empty model for empty input", () => {
    const e = extractProfile("");
    expect(e.network).toEqual({});
    expect(e.services).toEqual([]);
    expect(e.routing.direct_hosts).toEqual([]);
    expect(e.mediaServer.has_key).toBe(false);
  });

  it("parses a flat services map (key: value form)", () => {
    const e = extractProfile(`services:\n  sonarr: true\n  radarr: true\n`);
    expect(e.services).toEqual(["sonarr", "radarr"]);
  });

  it("recognises tuner_url / guide_url variants for iptv", () => {
    const e = extractProfile(
      `iptv:\n  tuner_url: http://t.local\n  guide_url: http://g.local\n`,
    );
    expect(e.iptv.tuner).toBe("http://t.local");
    expect(e.iptv.guide).toBe("http://g.local");
  });

  it("strips quotes from quoted scalars", () => {
    const e = extractProfile(`network:\n  gateway_host: "foo.local"\n`);
    expect(e.network.gateway_host).toBe("foo.local");
  });
});

describe("readProfileYaml", () => {
  it("returns the yaml field when present", () => {
    expect(readProfileYaml({ yaml: "a: 1" })).toBe("a: 1");
  });
  it("falls back to content", () => {
    expect(readProfileYaml({ content: "b: 2" })).toBe("b: 2");
  });
  it("falls back to a wrapped 'profile' key", () => {
    // The wrapped-key path comes from older controller builds where the
    // YAML lived under `profile.profile`. ProfileResponse has an
    // `[key: string]: unknown` index signature, so the object literal
    // is structurally assignable.
    expect(readProfileYaml({ profile: "c: 3" })).toBe("c: 3");
  });
  it("returns '' when given undefined", () => {
    expect(readProfileYaml(undefined)).toBe("");
  });
});
