import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";
import { useOnboarding } from "./hooks";

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/api/client", () => ({
  fetcher: (path: string, init?: RequestInit) => fetchMock(path, init),
}));

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
}

describe("useOnboarding", () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });
  afterEach(() => {
    fetchMock.mockReset();
  });

  it("GETs /api/onboarding and exposes the auto-tracked step list", async () => {
    fetchMock.mockResolvedValueOnce({
      steps: [
        {
          id: "services_running",
          label: "Services running",
          status: "ok",
          detail: "12/14 healthy",
        },
        {
          id: "libraries",
          label: "Media libraries configured",
          status: "pending",
          detail: "No libraries — go to Config > Libraries",
        },
      ],
      completed: 1,
      total: 2,
      progress_pct: 50,
      is_first_run: true,
    });
    const wrapper = makeWrapper();
    const { result } = renderHook(() => useOnboarding(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith("api/onboarding", undefined);
    expect(result.current.data?.progress_pct).toBe(50);
    expect(result.current.data?.is_first_run).toBe(true);
    expect(result.current.data?.steps).toHaveLength(2);
    expect(result.current.data?.steps[0]?.status).toBe("ok");
  });
});
