import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement, type ReactNode } from "react";

import { ApiError } from "./client";
import {
  useMediaIntegrityStatus,
  useReconcile,
  useAuditLog,
} from "./hooks";

function makeWrapper(): {
  wrapper: ({ children }: { children: ReactNode }) => ReactNode;
  client: QueryClient;
} {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }): ReactNode =>
    createElement(QueryClientProvider, { client }, children);
  return { wrapper, client };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("hooks", () => {
  let spy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    spy = vi.fn();
    vi.stubGlobal("fetch", spy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("useMediaIntegrityStatus loads status data", async () => {
    spy.mockResolvedValueOnce(
      jsonResponse({
        last_enforce: { ts: "", detail: {} },
        last_reconcile: { ts: "", detail: {} },
        policy_version: 1,
        servarr_adapters: ["radarr"],
        bazarr_present: true,
        missing_api_keys: [],
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useMediaIntegrityStatus(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.policy_version).toBe(1);
    expect(result.current.data?.servarr_adapters).toEqual(["radarr"]);
  });

  it("useReconcile fires the mutation and invalidates the cache", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ dry_run: false, servarr: {} }));
    const { wrapper, client } = makeWrapper();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useReconcile(), { wrapper });

    result.current.mutate({ dryRun: true });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledTimes(1);
    const url = String(spy.mock.calls[0]?.[0]);
    expect(url).toContain("api/media-integrity/reconcile?dry_run=1");
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["media-integrity"],
    });
  });

  it("propagates ApiError to the query result", async () => {
    spy.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "denied" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useMediaIntegrityStatus(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(403);
  });

  it("useAuditLog defaults to limit=50 and threads the action filter", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ entries: [] }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useAuditLog({ action: "login" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = String(spy.mock.calls[0]?.[0]);
    expect(url).toContain("limit=50");
    expect(url).toContain("action=login");
  });

  it("mutation propagates errors to the caller", async () => {
    spy.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "in progress" }), {
        status: 409,
        headers: { "content-type": "application/json" },
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useReconcile(), { wrapper });
    result.current.mutate();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(409);
  });
});
