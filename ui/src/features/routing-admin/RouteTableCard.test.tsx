import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { RouteTableCard } from "./RouteTableCard";
import type { RouteTableResponse } from "./hooks";

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return { ...actual, useRouteTable: vi.fn() };
});
const { useRouteTable } = await import("./hooks");

const baseTable: RouteTableResponse = {
  rows: [
    {
      host: "m.iomio.io",
      match: "/app/jellyfin/",
      target: "jellyfin",
      target_kind: "service",
      kind: "auto_path",
      source: "strategy=hybrid, app_path_prefix=/app (derived per-service)",
    },
    {
      host: "m.iomio.io",
      match: "/app/sonarr/",
      target: "sonarr",
      target_kind: "service",
      kind: "auto_path",
      source: "strategy=hybrid, app_path_prefix=/app (derived per-service)",
    },
    {
      host: "jf.iomio.io",
      match: "/",
      target: "jellyfin",
      target_kind: "service",
      kind: "subdomain",
      source: "hosts[] entry (role=media_server)",
    },
    {
      host: "m.iomio.io",
      match: "/app/jellyfin",
      target: "/app/jf",
      target_kind: "redirect",
      kind: "path_alias",
      source: "path_aliases[] (301)",
    },
    {
      host: "m.iomio.io",
      match: "/ (catch-all)",
      target: "/apps",
      target_kind: "redirect",
      kind: "catch_all",
      source: "catch_all.action",
    },
  ],
  summary: {
    strategy: "hybrid",
    gateway_host: "m.iomio.io",
    app_path_prefix: "/app",
    active_service_count: 27,
  },
};

beforeEach(() => vi.mocked(useRouteTable).mockReset());

describe("RouteTableCard", () => {
  it("renders the summary chips", () => {
    vi.mocked(useRouteTable).mockReturnValue({
      data: baseTable, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useRouteTable>);
    renderWithProviders(<RouteTableCard />);
    const summary = screen.getByTestId("route-table-summary");
    expect(summary).toHaveTextContent(/hybrid/);
    expect(summary).toHaveTextContent(/m\.iomio\.io/);
    expect(summary).toHaveTextContent(/\/app/);
    expect(summary).toHaveTextContent(/27/);
  });

  it("renders one row per route", () => {
    vi.mocked(useRouteTable).mockReturnValue({
      data: baseTable, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useRouteTable>);
    renderWithProviders(<RouteTableCard />);
    expect(screen.getByText("/app/jellyfin/")).toBeInTheDocument();
    expect(screen.getByText("/app/sonarr/")).toBeInTheDocument();
    expect(screen.getByText("/ (catch-all)")).toBeInTheDocument();
  });

  it("badges auto-path routes distinctly from subdomain ones", () => {
    vi.mocked(useRouteTable).mockReturnValue({
      data: baseTable, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useRouteTable>);
    renderWithProviders(<RouteTableCard />);
    const kindBadges = screen.getAllByTestId(/^route-kind-/);
    const labels = kindBadges.map((b) => b.textContent);
    expect(labels).toContain("auto · path");
    expect(labels).toContain("subdomain");
    expect(labels).toContain("path alias");
    expect(labels).toContain("catch-all");
  });

  it("renders empty state when there are no rows", () => {
    vi.mocked(useRouteTable).mockReturnValue({
      data: { ...baseTable, rows: [] }, isLoading: false, error: null,
    } as unknown as ReturnType<typeof useRouteTable>);
    renderWithProviders(<RouteTableCard />);
    expect(screen.getByTestId("route-table-empty")).toBeInTheDocument();
  });

  it("renders skeleton during loading", () => {
    vi.mocked(useRouteTable).mockReturnValue({
      data: undefined, isLoading: true, error: null,
    } as unknown as ReturnType<typeof useRouteTable>);
    renderWithProviders(<RouteTableCard />);
    expect(screen.getByTestId("route-table-card-loading")).toBeInTheDocument();
  });
});
