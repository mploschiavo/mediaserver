import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>(
    "@/api/client",
  );
  return {
    ...actual,
    fetcher: fetcherMock,
    getBaseUrl: () => "",
  };
});

import { ApiError } from "@/api/client";
import {
  useRunLifecycleEnsurer,
  type LifecycleEnsurerInvokeResult,
} from "../hooks";

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

describe("useRunLifecycleEnsurer (ADR-0005 Phase 5b step 3)", () => {
  beforeEach(() => {
    fetcherMock.mockReset();
  });
  afterEach(() => {
    fetcherMock.mockReset();
  });

  it("POSTs to api/lifecycle-ensurers/{service}/{method} on dispatch", async () => {
    const successOutcome: LifecycleEnsurerInvokeResult = {
      status: "success",
      message: "lifecycle ensurer succeeded",
      source: "operator",
      evidence: { reason: "minted" },
      attempts: 1,
      elapsed_seconds: 0.42,
    };
    fetcherMock.mockResolvedValue(successOutcome);
    const { result } = renderHook(
      () => useRunLifecycleEnsurer("jellyfin", "mint_api_key"),
      { wrapper: makeWrapper() },
    );
    const out = await result.current.mutateAsync();
    expect(out.status).toBe("success");
    expect(out.evidence).toEqual({ reason: "minted" });
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/lifecycle-ensurers/jellyfin/mint_api_key",
      expect.objectContaining({ method: "POST" }),
    );
    // No body when the caller passes nothing — defaults are server-side.
    const init = fetcherMock.mock.calls[0]?.[1] as { body?: unknown };
    expect(init.body).toBeUndefined();
  });

  it("encodes service and method path segments", async () => {
    fetcherMock.mockResolvedValue({
      status: "success",
      message: "ok",
      source: "operator",
      evidence: {},
    } satisfies LifecycleEnsurerInvokeResult);
    const { result } = renderHook(
      () => useRunLifecycleEnsurer("svc with space", "method/with-slash"),
      { wrapper: makeWrapper() },
    );
    await result.current.mutateAsync();
    const calledPath = fetcherMock.mock.calls[0]?.[0] as string;
    expect(calledPath).toBe(
      `api/lifecycle-ensurers/${encodeURIComponent(
        "svc with space",
      )}/${encodeURIComponent("method/with-slash")}`,
    );
  });

  it("forwards source + overrides as the JSON body when provided", async () => {
    fetcherMock.mockResolvedValue({
      status: "success",
      message: "ok",
      source: "auto-heal",
      evidence: {},
    } satisfies LifecycleEnsurerInvokeResult);
    const { result } = renderHook(
      () => useRunLifecycleEnsurer("jellyfin", "mint_api_key"),
      { wrapper: makeWrapper() },
    );
    await result.current.mutateAsync({
      source: "auto-heal",
      overrides: { force: true },
    });
    const init = fetcherMock.mock.calls[0]?.[1] as { body?: string };
    expect(init.body).toBeDefined();
    expect(JSON.parse(init.body as string)).toEqual({
      source: "auto-heal",
      overrides: { force: true },
    });
  });

  it("surfaces a transient outcome inline (no thrown error)", async () => {
    const transient: LifecycleEnsurerInvokeResult = {
      status: "transient",
      message: "unreachable at http://...",
      source: "operator",
      evidence: { url: "http://jellyfin:8096", error: "Connection refused" },
      attempts: 1,
      elapsed_seconds: 0.1,
    };
    fetcherMock.mockResolvedValue(transient);
    const { result } = renderHook(
      () => useRunLifecycleEnsurer("jellyfin", "mint_api_key"),
      { wrapper: makeWrapper() },
    );
    const out = await result.current.mutateAsync();
    expect(out.status).toBe("transient");
    expect(out.message).toMatch(/unreachable/);
    // 200-with-non-success-status is NOT an error — caller decides
    // whether to render a warning toast vs. a danger toast.
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.error).toBeNull();
  });

  it("surfaces a permanent outcome inline (no thrown error)", async () => {
    const permanent: LifecycleEnsurerInvokeResult = {
      status: "permanent",
      message: "HTTP 422 from /api/v3/indexer",
      source: "operator",
      evidence: { http_status: 422, url: "/api/v3/indexer" },
      attempts: 1,
      elapsed_seconds: 0.05,
    };
    fetcherMock.mockResolvedValue(permanent);
    const { result } = renderHook(
      () =>
        useRunLifecycleEnsurer("sonarr", "ensure_indexer", {
          invalidateKeys: [["indexers"]],
        }),
      { wrapper: makeWrapper() },
    );
    const out = await result.current.mutateAsync();
    expect(out.status).toBe("permanent");
    expect(out.evidence).toMatchObject({ http_status: 422 });
  });

  it("surfaces a 404 unknown-ensurer as ApiError on the mutation error", async () => {
    fetcherMock.mockRejectedValue(
      new ApiError("unknown ensurer", 404, {
        error: "unknown ensurer",
        service: "jellyfin",
        method: "mint_api_key_typo",
      }),
    );
    const { result } = renderHook(
      () => useRunLifecycleEnsurer("jellyfin", "mint_api_key_typo"),
      { wrapper: makeWrapper() },
    );
    await expect(result.current.mutateAsync()).rejects.toBeInstanceOf(ApiError);
    await waitFor(() => expect(result.current.isError).toBe(true));
    const err = result.current.error;
    expect(err).toBeInstanceOf(ApiError);
    if (err instanceof ApiError) {
      expect(err.status).toBe(404);
    }
  });

  it("invalidates jobs + jobs/running + caller-supplied keys on success", async () => {
    fetcherMock.mockResolvedValue({
      status: "success",
      message: "ok",
      source: "operator",
      evidence: {},
    } satisfies LifecycleEnsurerInvokeResult);
    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0, staleTime: 0 },
        mutations: { retry: false },
      },
    });
    const spy = vi.spyOn(qc, "invalidateQueries");
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () =>
        useRunLifecycleEnsurer("sonarr", "ensure_indexer", {
          invalidateKeys: [["indexers"], ["jellyfin", "libraries"]],
        }),
      { wrapper },
    );
    await result.current.mutateAsync();
    const calls = spy.mock.calls.map((c) => c[0]?.queryKey);
    expect(calls).toContainEqual(["jobs"]);
    expect(calls).toContainEqual(["jobs", "running"]);
    expect(calls).toContainEqual(["indexers"]);
    expect(calls).toContainEqual(["jellyfin", "libraries"]);
  });
});
