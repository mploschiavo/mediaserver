import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { ApexCatchAllCard } from "./ApexCatchAllCard";
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
  path_aliases: [],
  apex: { action: "redirect", target: "/apps", code: 302 },
  catch_all: { action: "404" },
  certs: [],
};

beforeEach(() => vi.mocked(useRoutingV2).mockReset());

describe("ApexCatchAllCard", () => {
  it("displays apex redirect with target + code", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ApexCatchAllCard />);
    expect(screen.getByTestId("apex-action-badge")).toHaveTextContent(/Redirect/);
    expect(screen.getByText("/apps")).toBeInTheDocument();
    expect(screen.getByText("302")).toBeInTheDocument();
  });

  it("labels apex 'none' as fall-through", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: { ...baseConfig, apex: { action: "none" } }, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ApexCatchAllCard />);
    expect(screen.getByTestId("apex-action-badge")).toHaveTextContent(/Fall through/);
  });

  it("displays catch-all 404 action", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ApexCatchAllCard />);
    expect(screen.getByTestId("catch-all-action-badge")).toHaveTextContent(/Plain 404/);
  });

  it("displays catch-all redirect with target + code", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: {
        config: {
          ...baseConfig,
          catch_all: { action: "redirect", target: "/apps", code: 302 },
        },
        validation: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ApexCatchAllCard />);
    expect(screen.getByTestId("catch-all-action-badge")).toHaveTextContent(/Redirect/);
  });

  it("displays catch-all block action", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: {
        config: { ...baseConfig, catch_all: { action: "block" } },
        validation: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ApexCatchAllCard />);
    expect(screen.getByTestId("catch-all-action-badge")).toHaveTextContent(/Block/);
  });

  it("renders skeleton during loading", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: undefined, isLoading: true, error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ApexCatchAllCard />);
    expect(screen.getByTestId("apex-catchall-card-loading")).toBeInTheDocument();
  });
});
