import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const multiState = vi.hoisted(() => ({
  data: [] as { source: string; lines: (string | object)[]; error?: string }[],
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useMultiLogs: () => multiState,
  };
});

import { LogsPage } from "./LogsPage";

const sampleLines = [
  "[2026-04-07 12:00:01] INFO: boot ok",
  "[2026-04-07 12:00:02] ERROR: boom",
  "[2026-04-07 12:00:03] WARN slow",
];

describe("LogsPage", () => {
  beforeEach(() => {
    multiState.data = [{ source: "controller", lines: sampleLines.slice() }];
    multiState.isLoading = false;
    multiState.error = null;
    window.localStorage.clear();
    window.history.replaceState({}, "", "/logs");
  });

  it("loads with the controller source pre-selected by default", () => {
    renderWithProviders(<LogsPage />);
    expect(
      screen.getByTestId("logs-source-chip-controller"),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("logs-source-chip-sonarr")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("hydrates the search input from ?filter= on mount", () => {
    window.history.replaceState({}, "", "/logs?filter=boom");
    renderWithProviders(<LogsPage />);
    const input = screen.getByTestId("logs-search") as HTMLInputElement;
    expect(input.value).toBe("boom");
  });

  it("hydrates the source from ?service= on mount", () => {
    window.history.replaceState({}, "", "/logs?service=sonarr");
    renderWithProviders(<LogsPage />);
    expect(screen.getByTestId("logs-source-chip-sonarr")).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("renders all three sample lines as table rows", () => {
    renderWithProviders(<LogsPage />);
    const rows = screen.getAllByTestId("logs-row");
    expect(rows.length).toBe(3);
    expect(screen.getByTestId("logs-stat-visible")).toHaveTextContent("3");
    expect(screen.getByTestId("logs-stat-total")).toHaveTextContent("3");
  });

  it("filters by level when a chip is toggled off", async () => {
    renderWithProviders(<LogsPage />);
    expect(screen.getAllByTestId("logs-row").length).toBe(3);
    await userEvent.click(screen.getByTestId("logs-level-chip-INFO"));
    // Filtering removes the [INFO] line, leaving 2.
    await waitFor(() => {
      expect(screen.getAllByTestId("logs-row").length).toBe(2);
    });
  });

  it("filters by substring search input (case-insensitive)", async () => {
    renderWithProviders(<LogsPage />);
    await userEvent.type(screen.getByTestId("logs-search"), "BOOM");
    await waitFor(() => {
      expect(screen.getAllByTestId("logs-row").length).toBe(1);
    });
  });

  it("filters by regex when the input starts with /…/", async () => {
    renderWithProviders(<LogsPage />);
    await userEvent.type(screen.getByTestId("logs-search"), "/^ERROR/");
    await waitFor(() => {
      expect(screen.getAllByTestId("logs-row").length).toBe(1);
    });
  });

  it("flips the Tailing badge when the operator clicks Pause", async () => {
    renderWithProviders(<LogsPage />);
    expect(screen.getByTestId("logs-tailing-badge")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("logs-tail-toggle"));
    expect(screen.getByTestId("logs-paused-badge")).toBeInTheDocument();
  });

  it("renders the multi-source aggregate (one row per bucket)", () => {
    multiState.data = [
      { source: "controller", lines: ["[2026-04-07 12:00:01] INFO: a"] },
      { source: "sonarr", lines: ["[2026-04-07 12:00:02] INFO: b"] },
    ];
    renderWithProviders(<LogsPage />);
    const sources = screen
      .getAllByTestId("logs-row")
      .map((r) => r.getAttribute("data-source"));
    expect(sources).toContain("controller");
    expect(sources).toContain("sonarr");
  });

  it("disables export when there's nothing visible", async () => {
    renderWithProviders(<LogsPage />);
    // Filter to nothing.
    await userEvent.type(screen.getByTestId("logs-search"), "no-such-thing");
    await waitFor(() => {
      expect(screen.getByTestId("logs-export")).toBeDisabled();
    });
  });

  it("persists source selection to localStorage on change", async () => {
    renderWithProviders(<LogsPage />);
    await userEvent.click(screen.getByTestId("logs-source-chip-sonarr"));
    await waitFor(() => {
      const stored = window.localStorage.getItem("media-stack:logs-sources");
      expect(stored).toBeTruthy();
      const arr = JSON.parse(stored ?? "[]") as string[];
      expect(arr).toContain("sonarr");
    });
  });

  it("writes ?filter= back to the URL after a debounce", async () => {
    renderWithProviders(<LogsPage />);
    await userEvent.type(screen.getByTestId("logs-search"), "boom");
    // The page debounces writes by 300ms; wait for the URL update.
    await waitFor(
      () => {
        expect(window.location.search).toContain("filter=boom");
      },
      { timeout: 1500 },
    );
  });

  it("renders a removable filter chip when a deep-linked filter is active", async () => {
    window.history.replaceState({}, "", "/logs?filter=boom");
    renderWithProviders(<LogsPage />);
    const chip = await screen.findByTestId("logs-filter-chip");
    expect(chip).toHaveTextContent(/boom/);
    expect(screen.getByTestId("logs-filter-chip-clear")).toBeInTheDocument();
  });

  it("clears the filter when the chip's clear button is clicked", async () => {
    window.history.replaceState({}, "", "/logs?filter=boom");
    renderWithProviders(<LogsPage />);
    const clearBtn = await screen.findByTestId("logs-filter-chip-clear");
    await userEvent.click(clearBtn);
    await waitFor(() => {
      expect(screen.queryByTestId("logs-filter-chip")).toBeNull();
    });
  });

  it("does NOT render the filter chip when no filter is active", () => {
    renderWithProviders(<LogsPage />);
    expect(screen.queryByTestId("logs-filter-chip")).toBeNull();
  });
});
