import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { PathAliasesCard } from "./PathAliasesCard";
import type { RoutingV2Response } from "./hooks";

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return { ...actual, useRoutingV2: vi.fn() };
});
const { useRoutingV2 } = await import("./hooks");

const baseConfig: RoutingV2Response["config"] = {
  version: 2,
  base_domain: "iomio.io",
  stack_subdomain: "m",
  gateway_host: "m.iomio.io",
  gateway_port: 443,
  strategy: "hybrid",
  scheme: "",
  app_path_prefix: "/app",
  exposure: { enabled: true, binding: "k8s_ingress", public_hostnames: [] },
  hosts: [],
  path_aliases: [
    { from: "/app/jellyfin", to: "/app/jf", code: 301 },
    { from: "/app/media-stack-ui", to: "/app/ui", code: 301 },
  ],
  apex: { action: "none" },
  catch_all: { action: "404" },
  certs: [],
};

beforeEach(() => vi.mocked(useRoutingV2).mockReset());

describe("PathAliasesCard", () => {
  it("renders one row per alias", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<PathAliasesCard />);
    expect(screen.getByTestId("path-alias-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("path-alias-row-1")).toBeInTheDocument();
  });

  it("displays from / to / code per alias", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<PathAliasesCard />);
    expect(screen.getByText("/app/jellyfin")).toBeInTheDocument();
    expect(screen.getByText("/app/jf")).toBeInTheDocument();
    expect(screen.getAllByText("301")).toHaveLength(2);
  });

  it("renders empty-state when no aliases", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: { ...baseConfig, path_aliases: [] }, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<PathAliasesCard />);
    expect(screen.getByTestId("path-aliases-empty")).toBeInTheDocument();
  });

  it("renders skeleton during loading", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: undefined, isLoading: true, error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<PathAliasesCard />);
    expect(screen.getByTestId("path-aliases-card-loading")).toBeInTheDocument();
  });
});
