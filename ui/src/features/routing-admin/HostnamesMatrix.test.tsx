import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { HostnamesMatrix } from "./HostnamesMatrix";
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
  hosts: [
    {
      role: "media_server",
      service_id: "jellyfin",
      canonical: "jf.iomio.io",
      aliases: ["jellyfin.iomio.io"],
      tls: { cert_id: "wildcard", force_https: true },
      auth: { gate: "required", provider: "authelia" },
    },
    {
      role: "auth",
      service_id: "authelia",
      canonical: "auth.iomio.io",
      auth: { gate: "none" },
    },
  ],
  path_aliases: [],
  apex: { action: "none" },
  catch_all: { action: "404" },
  certs: [],
};

beforeEach(() => {
  vi.mocked(useRoutingV2).mockReset();
});

describe("HostnamesMatrix", () => {
  it("renders one row per host", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<HostnamesMatrix />);
    expect(screen.getByTestId("hostnames-canonical-0")).toHaveTextContent("jf.iomio.io");
    expect(screen.getByTestId("hostnames-canonical-1")).toHaveTextContent("auth.iomio.io");
  });

  it("displays alias hostnames inline", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<HostnamesMatrix />);
    expect(screen.getByText("jellyfin.iomio.io")).toBeInTheDocument();
  });

  it("badges TLS-bound hosts with the cert id", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<HostnamesMatrix />);
    expect(screen.getByTestId("hostnames-tls-0")).toHaveTextContent("wildcard");
  });

  it("shows '—' for hosts without TLS", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<HostnamesMatrix />);
    expect(screen.getByTestId("hostnames-tls-1-none")).toBeInTheDocument();
  });

  it("badges required-auth hosts distinctly from gateless ones", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: baseConfig, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<HostnamesMatrix />);
    expect(screen.getByTestId("hostnames-auth-0-required")).toBeInTheDocument();
    expect(screen.getByTestId("hostnames-auth-1-none")).toBeInTheDocument();
  });

  it("renders empty-state when there are no hosts", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: { config: { ...baseConfig, hosts: [] }, validation: [] },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<HostnamesMatrix />);
    expect(screen.getByTestId("hostnames-matrix-empty")).toBeInTheDocument();
  });

  it("renders skeleton during loading", () => {
    vi.mocked(useRoutingV2).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as unknown as ReturnType<typeof useRoutingV2>);
    renderWithProviders(<HostnamesMatrix />);
    expect(screen.getByTestId("hostnames-matrix-loading")).toBeInTheDocument();
  });
});
