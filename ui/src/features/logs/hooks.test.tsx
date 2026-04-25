import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { LogSource } from "@/api/shapes";
import { parseLogLine, useMultiLogs } from "./hooks";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("parseLogLine", () => {
  it("classifies a string ERROR line as [ERR]", () => {
    const out = parseLogLine(
      "[2026-04-07 12:00:01] ERROR: kaboom",
      "controller",
      0,
    );
    expect(out.level).toBe("[ERR]");
    expect(out.levelClassName).toContain("danger");
    expect(out.ts).toBe("2026-04-07 12:00:01");
    expect(out.message).toBe("ERROR: kaboom");
  });

  it("classifies a structured info LogLineShape as [INFO]", () => {
    const out = parseLogLine(
      { ts: "2024-01-01T00:00:00Z", level: "info", message: "boot" },
      "sonarr",
      0,
    );
    expect(out.level).toBe("[INFO]");
    expect(out.ts).toBe("2024-01-01T00:00:00Z");
    expect(out.message).toBe("boot");
  });

  it("falls through to [LOG] for unrecognised raw lines", () => {
    const out = parseLogLine("just a message", "sonarr", 0);
    expect(out.level).toBe("[LOG]");
    expect(out.ts).toBeNull();
  });

  it("uses insertion as the sortKey when no ts is present", () => {
    const a = parseLogLine("a line", "sonarr", 0);
    const b = parseLogLine("b line", "sonarr", 1);
    expect(a.sortKey).toBeLessThan(b.sortKey);
  });
});

describe("useMultiLogs", () => {
  beforeEach(() => {
    fetcherMock.mockReset();
  });
  afterEach(() => {
    fetcherMock.mockReset();
  });

  it("fans out to /api/logs/{source} per source and aggregates results", async () => {
    fetcherMock.mockImplementation((path: string) => {
      if (path === "api/logs/controller") {
        return Promise.resolve({
          source: "controller",
          lines: ["controller line"],
        });
      }
      if (path === "api/logs/sonarr") {
        return Promise.resolve({ source: "sonarr", lines: ["sonarr line"] });
      }
      throw new Error(`unexpected ${path}`);
    });

    const sources: LogSource[] = ["controller", "sonarr"];
    const { result } = renderHook(
      () => useMultiLogs(sources, { tailing: true }),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.data[0]?.lines.length).toBe(1);
      expect(result.current.data[1]?.lines.length).toBe(1);
    });
    expect(fetcherMock).toHaveBeenCalledWith("api/logs/controller");
    expect(fetcherMock).toHaveBeenCalledWith("api/logs/sonarr");
  });

  it("propagates a payload-side error string per bucket", async () => {
    fetcherMock.mockResolvedValue({
      source: "controller",
      lines: [],
      error: "No pods found for controller",
    });
    const { result } = renderHook(
      () => useMultiLogs(["controller"], { tailing: true }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => {
      expect(result.current.data[0]?.error).toBeDefined();
    });
    expect(result.current.data[0]?.error).toMatch(/No pods found/);
  });

  it("coerces a non-array `lines` payload to an empty array", async () => {
    fetcherMock.mockResolvedValue({ source: "controller", lines: null });
    const { result } = renderHook(
      () => useMultiLogs(["controller"], { tailing: true }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    expect(result.current.data[0]?.lines).toEqual([]);
  });

  it("returns an empty data array when no sources are passed", () => {
    const { result } = renderHook(
      () => useMultiLogs([], { tailing: true }),
      { wrapper: makeWrapper() },
    );
    expect(result.current.data).toEqual([]);
    expect(result.current.isLoading).toBe(false);
  });
});
