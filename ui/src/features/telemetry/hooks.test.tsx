import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  pickCategories,
  pickConsentLevel,
  useSaveTelemetry,
  useTelemetry,
} from "./hooks";

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, Wrapper };
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("telemetry feature hooks", () => {
  it("useTelemetry hits GET /api/telemetry", async () => {
    fetchMock.mockResolvedValue(ok({ consent: "minimal", categories: [] }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useTelemetry(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/api\/telemetry$/);
    expect(result.current.data?.consent).toBe("minimal");
  });

  it("useSaveTelemetry POSTs the payload as JSON", async () => {
    fetchMock.mockResolvedValue(
      ok({ consent: "standard", categories: ["health_probes"] }),
    );
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useSaveTelemetry(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync({
      consent: "standard",
      categories: ["health_probes"],
    });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/telemetry$/);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      consent: "standard",
      categories: ["health_probes"],
    });
  });

  it("pickConsentLevel falls back to 'none' on garbage", () => {
    expect(pickConsentLevel(undefined)).toBe("none");
    expect(pickConsentLevel({ consent: "wat" })).toBe("none");
    expect(pickConsentLevel({ consent: "FULL" })).toBe("full");
    expect(pickConsentLevel({ consent: "minimal" })).toBe("minimal");
  });

  it("pickCategories coerces non-arrays to []", () => {
    expect(pickCategories(undefined)).toEqual([]);
    expect(
      pickCategories({
        categories: "nope" as unknown as readonly string[],
      }),
    ).toEqual([]);
    expect(
      pickCategories({
        categories: ["a", 1, "b"] as unknown as readonly string[],
      }),
    ).toEqual(["a", "b"]);
  });
});
