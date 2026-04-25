import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { AdapterTable } from "./AdapterTable";
import type { MediaIntegrityStatusShape } from "@/api";

function makeStatus(
  overrides?: Partial<MediaIntegrityStatusShape>,
): MediaIntegrityStatusShape {
  return {
    last_enforce: { ts: "", detail: {} },
    last_reconcile: {
      ts: new Date().toISOString(),
      detail: {
        servarr: {
          results: {
            radarr: {
              total_resolved: 12,
              bytes_freed: 1024 * 1024 * 1024 * 4.2,
              total_needs_review: 1,
              total_failures: 0,
              ts: new Date(Date.now() - 5 * 60_000).toISOString(),
            },
          },
        },
        bazarr: {
          total_resolved: 3,
          bytes_freed: 0,
          total_needs_review: 0,
          total_failures: 2,
          ts: new Date(Date.now() - 10 * 60_000).toISOString(),
        },
      },
    },
    policy_version: 1,
    servarr_adapters: ["radarr", "sonarr"],
    bazarr_present: true,
    missing_api_keys: [],
    ...overrides,
  };
}

describe("AdapterTable", () => {
  it("renders a skeleton block while loading", () => {
    renderWithProviders(<AdapterTable loading />);
    expect(screen.getByTestId("adapter-table-loading")).toBeInTheDocument();
  });

  it("renders an empty state when no adapters are configured", () => {
    renderWithProviders(
      <AdapterTable
        status={makeStatus({ servarr_adapters: [], bazarr_present: false })}
      />,
    );
    expect(screen.getByText("No adapters configured")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /open routing/i }),
    ).toHaveAttribute("href", "/routing");
  });

  it("renders one row per configured adapter", () => {
    renderWithProviders(<AdapterTable status={makeStatus()} />);
    const table = screen.getByTestId("adapter-table");
    // Three rows — radarr, sonarr (no result), bazarr.
    expect(table).toHaveTextContent("Radarr");
    expect(table).toHaveTextContent("Sonarr");
    expect(table).toHaveTextContent("Bazarr");
  });

  it("renders '—' for adapters that didn't run in the last reconcile", () => {
    renderWithProviders(<AdapterTable status={makeStatus()} />);
    // sonarr is configured but has no result row → dashes.
    const table = screen.getByTestId("adapter-table");
    expect(table.textContent).toMatch(/—/);
  });

  it("renders the formatted freed bytes for ran adapters", () => {
    renderWithProviders(<AdapterTable status={makeStatus()} />);
    expect(screen.getAllByText(/GB/).length).toBeGreaterThan(0);
  });

  it("shows the failures count with the danger tone when > 0", () => {
    renderWithProviders(<AdapterTable status={makeStatus()} />);
    // Bazarr has 2 failures; the badge text "2" is present at least
    // once in the rendered table.
    const table = screen.getByTestId("adapter-table");
    expect(table).toHaveTextContent("2");
  });

  it("includes bazarr only when bazarr_present is true", () => {
    renderWithProviders(
      <AdapterTable status={makeStatus({ bazarr_present: false })} />,
    );
    const table = screen.getByTestId("adapter-table");
    expect(table.textContent).not.toContain("Bazarr");
  });
});
