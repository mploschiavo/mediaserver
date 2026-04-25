import type { ComponentType } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const multiState = vi.hoisted(() => ({
  data: [] as { source: string; lines: (string | object)[]; error?: string }[],
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("@/features/logs/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/features/logs/hooks")>(
    "@/features/logs/hooks",
  );
  return {
    ...actual,
    useMultiLogs: () => multiState,
  };
});

import { Route as LogsRoute, formatLogLine } from "./logs";

const LogsPage = LogsRoute.options.component as ComponentType;

describe("logs route", () => {
  beforeEach(() => {
    multiState.data = [{ source: "controller", lines: [] }];
    multiState.isLoading = false;
    multiState.error = null;
    window.localStorage.clear();
    window.history.replaceState({}, "", "/logs");
  });

  it("registers at /logs", () => {
    expect((LogsRoute.options as unknown as { path: string }).path).toBe("/logs");
  });

  it("renders the LogsPage shell with toolbar + table testids", () => {
    multiState.data = [
      {
        source: "controller",
        lines: ["[2026-04-07 12:00:01] INFO: boot ok"],
      },
    ];
    renderWithProviders(<LogsPage />);
    expect(screen.getByTestId("logs-page")).toBeInTheDocument();
    expect(screen.getByTestId("logs-toolbar")).toBeInTheDocument();
    expect(screen.getByTestId("logs-stats")).toBeInTheDocument();
    expect(screen.getByTestId("logs-table-body")).toBeInTheDocument();
    // Source chips legend.
    expect(
      screen.getByTestId("logs-source-chip-controller"),
    ).toBeInTheDocument();
  });

  it("renders the empty-buffer state when sources are picked but lines are empty", () => {
    multiState.data = [{ source: "controller", lines: [] }];
    renderWithProviders(<LogsPage />);
    expect(screen.getByTestId("logs-empty")).toBeInTheDocument();
  });

  it("surfaces a payload-side error string in the stats row", () => {
    multiState.data = [
      {
        source: "controller",
        lines: [],
        error: "No pods found for controller",
      },
    ];
    renderWithProviders(<LogsPage />);
    expect(screen.getByTestId("logs-payload-error")).toHaveTextContent(
      /No pods found for controller/,
    );
  });

  it("formatLogLine routes levels to the right color class", () => {
    expect(
      formatLogLine({ ts: "", level: "error", message: "x" }).className,
    ).toContain("danger");
    expect(
      formatLogLine({ ts: "", level: "warn", message: "x" }).className,
    ).toContain("warning");
    expect(
      formatLogLine({ ts: "", level: "info", message: "x" }).className,
    ).toContain("muted");
  });

  it("validateSearch normalises ?service=... and ?filter=...", () => {
    const validate = (LogsRoute.options as unknown as {
      validateSearch: (raw: Record<string, unknown>) => Record<string, unknown>;
    }).validateSearch;
    expect(validate({ service: "controller", filter: "scan" })).toEqual({
      service: "controller",
      filter: "scan",
    });
    expect(validate({ service: "not-a-source" })).toEqual({});
    expect(validate({ filter: "" })).toEqual({});
  });
});
