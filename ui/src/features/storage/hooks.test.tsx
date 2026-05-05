import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
  ApiError: class ApiError extends Error {},
}));

import {
  useDiskGuardrailsStatus,
  useEngageLockdown,
  useForceEvaluate,
  usePauseGuardrails,
  useReleaseLockdown,
  useRunCleanup,
  useUpdateThresholds,
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

describe("storage feature hooks", () => {
  beforeEach(() => {
    fetcherMock.mockReset();
  });
  afterEach(() => {
    fetcherMock.mockReset();
  });

  it("useDiskGuardrailsStatus GETs api/disk-guardrails", async () => {
    const snapshot = {
      state: "NORMAL",
      used_percent_by_mount: { config: 42.1, data: 65.8 },
      thresholds: { lockdown_percent: 75, release_percent: 60 },
      engaged_at: 0,
      engaged_by: "",
      trigger: null,
      auto_check_paused_until: null,
      paused_clients: [],
      last_failures: [],
      transitions: [],
    };
    fetcherMock.mockResolvedValue(snapshot);
    const { result } = renderHook(() => useDiskGuardrailsStatus(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(fetcherMock).toHaveBeenCalledWith("api/disk-guardrails");
    expect(result.current.data?.state).toBe("NORMAL");
  });

  it("useRunCleanup POSTs to /cleanup with snake_case body", async () => {
    fetcherMock.mockResolvedValue({
      deleted: 14,
      freed_gb: 32.5,
      kept: 0,
      candidates_evaluated: 14,
      strategy: "oldest_first",
    });
    const { result } = renderHook(() => useRunCleanup(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync({
      categories: ["tv-sonarr"],
      max_delete: 5,
    });
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/disk-guardrails/cleanup",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          categories: ["tv-sonarr"],
          max_delete: 5,
        }),
      }),
    );
  });

  it("useRunCleanup omits the body when called without args", async () => {
    fetcherMock.mockResolvedValue({});
    const { result } = renderHook(() => useRunCleanup(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync();
    const call = fetcherMock.mock.calls[0]!;
    expect(call[0]).toBe("api/disk-guardrails/cleanup");
    expect((call[1] as { body?: unknown }).body).toBeUndefined();
  });

  it("useEngageLockdown POSTs to /lockdown", async () => {
    fetcherMock.mockResolvedValue({
      state: "MANUAL_LOCKDOWN",
      paused_clients: [],
      failures: [],
    });
    const { result } = renderHook(() => useEngageLockdown(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync();
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/disk-guardrails/lockdown",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("useReleaseLockdown POSTs to /release", async () => {
    fetcherMock.mockResolvedValue({ state: "NORMAL", released_clients: [] });
    const { result } = renderHook(() => useReleaseLockdown(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync();
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/disk-guardrails/release",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("useForceEvaluate POSTs to /evaluate", async () => {
    fetcherMock.mockResolvedValue({});
    const { result } = renderHook(() => useForceEvaluate(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync();
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/disk-guardrails/evaluate",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("usePauseGuardrails encodes hours as a query param", async () => {
    fetcherMock.mockResolvedValue({ paused_until: 1, hours: 3 });
    const { result } = renderHook(() => usePauseGuardrails(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync({ hours: 3 });
    const url = String(fetcherMock.mock.calls[0]?.[0]);
    expect(url).toBe("api/disk-guardrails/pause-auto?hours=3");
  });

  it("useUpdateThresholds fans out to lockdown + cleanup rules", async () => {
    fetcherMock.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useUpdateThresholds(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync({
      watchPercent: 50,
      cleanupPercent: 70,
      lockdownPercent: 75,
      releasePercent: 60,
    });
    expect(fetcherMock).toHaveBeenCalledTimes(2);
    const urls = fetcherMock.mock.calls.map((c) => String(c[0]));
    expect(
      urls.some((u) => u.includes("storage") && u.includes("lockdown_threshold")),
    ).toBe(true);
    expect(
      urls.some((u) => u.includes("storage") && u.includes("per_mount_threshold")),
    ).toBe(true);
    const bodies = fetcherMock.mock.calls.map((c) =>
      JSON.parse(String((c[1] as { body: string }).body)),
    );
    const lockdown = bodies.find(
      (b) => b.threshold && "lockdown_percent" in b.threshold,
    );
    expect(lockdown.threshold).toMatchObject({
      lockdown_percent: 75,
      release_percent: 60,
      watch_percent: 50,
    });
    const cleanupBody = bodies.find(
      (b) => b.threshold && "cleanup_percent" in b.threshold,
    );
    expect(cleanupBody.threshold).toMatchObject({ cleanup_percent: 70 });
  });

  it("useUpdateThresholds resolves when one branch fails (partial save)", async () => {
    // First (lockdown) succeeds, second (cleanup) rejects.
    fetcherMock
      .mockResolvedValueOnce({ ok: true })
      .mockRejectedValueOnce(new Error("not found"));
    const { result } = renderHook(() => useUpdateThresholds(), {
      wrapper: makeWrapper(),
    });
    await expect(
      result.current.mutateAsync({
        watchPercent: 50,
        cleanupPercent: 70,
        lockdownPercent: 75,
        releasePercent: 60,
      }),
    ).resolves.toBeTruthy();
  });
});
