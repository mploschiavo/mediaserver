import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

import {
  useCancelAction,
  useJobs,
  useRunAction,
  type JobsResponse,
} from "./hooks";

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("jobs feature hooks", () => {
  beforeEach(() => {
    fetcherMock.mockReset();
  });
  afterEach(() => {
    fetcherMock.mockReset();
  });

  it("useJobs GETs api/jobs and coerces lists defensively", async () => {
    fetcherMock.mockResolvedValue({
      jobs: [{ name: "a" }, { name: "b" }],
      tree: [{ name: "a", sub_jobs: [] }],
      history: [{ ts: 1, ok: 1 }],
      count: 2,
    });
    const { result } = renderHook(() => useJobs(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(fetcherMock).toHaveBeenCalledWith("api/jobs");
    const data = result.current.data as JobsResponse;
    expect(data.jobs.length).toBe(2);
    expect(data.tree.length).toBe(1);
    expect(data.history.length).toBe(1);
    expect(data.count).toBe(2);
  });

  it("useJobs collapses non-array fields to empty arrays", async () => {
    fetcherMock.mockResolvedValue({
      jobs: { not: "an array" },
      tree: null,
      history: undefined,
      count: "oops",
    });
    const { result } = renderHook(() => useJobs(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const data = result.current.data as JobsResponse;
    expect(data.jobs).toEqual([]);
    expect(data.tree).toEqual([]);
    expect(data.history).toEqual([]);
    expect(data.count).toBeUndefined();
  });

  it("useRunAction POSTs /actions/{name} (NOT /api/actions/{name})", async () => {
    fetcherMock.mockResolvedValue({ task_id: "task-123" });
    const { result } = renderHook(
      () => useRunAction("scan-completed-downloads"),
      { wrapper: makeWrapper() },
    );
    const out = await result.current.mutateAsync();
    expect(out.task_id).toBe("task-123");
    expect(fetcherMock).toHaveBeenCalledWith(
      "/actions/scan-completed-downloads",
      expect.objectContaining({ method: "POST" }),
    );
    // Critically, the path is NOT prefixed with `/api/`.
    const calledPath = fetcherMock.mock.calls[0]?.[0];
    expect(typeof calledPath).toBe("string");
    expect(calledPath as string).not.toMatch(/^\/?api\//);
  });

  it("useRunAction encodes the action name", async () => {
    fetcherMock.mockResolvedValue({ task_id: "x" });
    const { result } = renderHook(
      () => useRunAction("with spaces & symbols"),
      { wrapper: makeWrapper() },
    );
    await result.current.mutateAsync();
    const calledPath = fetcherMock.mock.calls[0]?.[0] as string;
    expect(calledPath).toBe(
      `/actions/${encodeURIComponent("with spaces & symbols")}`,
    );
  });

  it("useCancelAction POSTs /actions/cancel", async () => {
    fetcherMock.mockResolvedValue({ cancelled: true });
    const { result } = renderHook(() => useCancelAction(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync();
    expect(fetcherMock).toHaveBeenCalledWith(
      "/actions/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
