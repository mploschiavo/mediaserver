import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { ExposureCard } from "./ExposureCard";
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
  scheme: "https",
  app_path_prefix: "/app",
  exposure: {
    enabled: true,
    binding: "k8s_ingress",
    public_hostnames: ["m.iomio.io", "jf.iomio.io"],
  },
  hosts: [],
  path_aliases: [],
  apex: { action: "none" },
  catch_all: { action: "404" },
  certs: [],
};

beforeEach(() => {
  vi.mocked(useRoutingV2).mockReset();
});

describe("ExposureCard", () => {
  it("renders the exposed badge when enabled", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ExposureCard />);
    expect(screen.getByTestId("exposure-status-badge")).toHaveTextContent(/Exposed/);
  });

  it("renders the internal badge when disabled", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: {
        config: { ...baseConfig, exposure: { ...baseConfig.exposure, enabled: false } },
        validation: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ExposureCard />);
    expect(screen.getByTestId("exposure-status-badge")).toHaveTextContent(/Internal only/);
  });

  it("displays every public hostname", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ExposureCard />);
    expect(screen.getByText("m.iomio.io")).toBeInTheDocument();
    expect(screen.getByText("jf.iomio.io")).toBeInTheDocument();
  });

  it("shows 'no hostnames' when public_hostnames is empty", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: {
        config: { ...baseConfig, exposure: { ...baseConfig.exposure, public_hostnames: [] } },
        validation: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ExposureCard />);
    expect(screen.getByText(/no inbound DNS/i)).toBeInTheDocument();
  });

  it("renders skeleton during loading", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ExposureCard />);
    expect(screen.getByTestId("exposure-card-loading")).toBeInTheDocument();
  });

  it("renders error state on failure", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("offline"),
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ExposureCard />);
    expect(screen.getByTestId("exposure-card-error")).toBeInTheDocument();
    expect(screen.getByText(/offline/)).toBeInTheDocument();
  });

  it("displays the binding-mode label", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: {
        config: {
          ...baseConfig,
          exposure: { ...baseConfig.exposure, binding: "k8s_loadbalancer" },
        },
        validation: [],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<ExposureCard />);
    expect(screen.getByTestId("exposure-binding-badge")).toHaveTextContent(
      /K8s LoadBalancer/,
    );
  });
});
