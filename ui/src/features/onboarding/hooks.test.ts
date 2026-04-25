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

  it("GETs /api/onboarding", async () => {
    fetchMock.mockResolvedValueOnce({
      step: "indexers",
      completed: ["welcome"],
      pending: [{ id: "indexers", label: "Indexers", route: "/indexers" }],
    });
    const wrapper = makeWrapper();
    const { result } = renderHook(() => useOnboarding(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith("api/onboarding", undefined);
    expect(result.current.data?.step).toBe("indexers");
    expect(result.current.data?.completed).toHaveLength(1);
    expect(result.current.data?.pending).toHaveLength(1);
  });
});
