/**
 * Adapter tests for `useUserProviders` and `useUsersReconcile`.
 *
 * Both hooks reshape the controller's wire format into the per-user
 * / per-orphan rows that `ProviderReconcileCard` renders. Without
 * the adapters the card showed three ghost rows of all-dashes
 * (provider-system records masquerading as user rows) plus three
 * "diff (unknown)" wrapper entries (per-provider diff containers
 * masquerading as flat per-orphan entries). These tests pin the
 * adapter contracts so a refactor can't silently bring back the
 * "Provider reconciliation" all-dashes regression.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/api/client", () => ({
  fetcher: vi.fn(),
}));

import { fetcher } from "@/api/client";
import { useUserProviders, useUsersReconcile } from "./hooks";

const mockedFetcher = vi.mocked(fetcher);

function withQueryClient() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("useUserProviders adapter", () => {
  beforeEach(() => {
    mockedFetcher.mockReset();
  });

  it("reshapes /api/users.provider_refs into the per-user table shape", async () => {
    mockedFetcher.mockResolvedValueOnce({
      users: [
        {
          id: "u-admin",
          username: "admin",
          email: "admin@local",
          provider_refs: { authelia: "admin" },
        },
        {
          id: "u-2510",
          username: "2510",
          provider_refs: { authelia: "2510", jellyfin: "abc-123" },
        },
        {
          id: "u-noprov",
          username: "lonely",
          // No provider_refs — must still produce a row, just with
          // an empty providers map (UI renders dashes per cell).
        },
      ],
    });
    const { result } = renderHook(() => useUserProviders(), {
      wrapper: withQueryClient(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const list = result.current.data?.providers ?? [];
    expect(list).toHaveLength(3);
    expect(list[0]).toEqual({
      user_id: "u-admin",
      username: "admin",
      providers: { authelia: { external_id: "admin" } },
    });
    expect(list[1]?.providers).toEqual({
      authelia: { external_id: "2510" },
      jellyfin: { external_id: "abc-123" },
    });
    expect(list[2]?.providers).toEqual({});
    expect(mockedFetcher).toHaveBeenCalledWith("api/users");
  });

  it("ignores empty-string external_ids (treat as not linked)", async () => {
    // The controller occasionally emits empty strings when a
    // provider_ref slot exists but the user has no binding there.
    // The card's `cell ? <Badge/> : <—/>` ternary must NOT render
    // a badge with text "" — drop these on the floor.
    mockedFetcher.mockResolvedValueOnce({
      users: [{
        id: "u-1",
        username: "u",
        provider_refs: { authelia: "u", jellyfin: "" },
      }],
    });
    const { result } = renderHook(() => useUserProviders(), {
      wrapper: withQueryClient(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.providers[0]?.providers).toEqual({
      authelia: { external_id: "u" },
    });
  });
});

describe("useUsersReconcile adapter", () => {
  beforeEach(() => {
    mockedFetcher.mockReset();
  });

  it("flattens per-provider orphans+ghosts into one diff per row", async () => {
    mockedFetcher.mockResolvedValueOnce({
      diffs: [
        {
          provider: "authelia",
          matched: 2,
          orphans: [],
          ghosts: [],
        },
        {
          provider: "jellyfin",
          matched: 0,
          orphans: [
            { external_id: "8ea2…", username: "2510", email: "" },
            { external_id: "674b…", username: "admin", email: "" },
          ],
          ghosts: [],
        },
        {
          provider: "jellyseerr",
          matched: 0,
          orphans: [{ external_id: "1", username: "admin" }],
          ghosts: [{ external_id: "stale-id", username: "deleted-user" }],
        },
      ],
    });
    const { result } = renderHook(() => useUsersReconcile(), {
      wrapper: withQueryClient(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const flat = result.current.data?.diffs ?? [];
    // 2 jellyfin orphans + 1 jellyseerr orphan + 1 jellyseerr ghost = 4
    expect(flat).toHaveLength(4);
    expect(flat[0]).toMatchObject({
      provider_name: "jellyfin",
      external_id: "8ea2…",
      kind: "orphan",
    });
    expect(flat[2]).toMatchObject({
      provider_name: "jellyseerr",
      kind: "orphan",
    });
    expect(flat[3]).toMatchObject({
      provider_name: "jellyseerr",
      external_id: "stale-id",
      kind: "ghost",
    });
  });

  it("emits an empty diff list when every provider is fully matched", async () => {
    mockedFetcher.mockResolvedValueOnce({
      diffs: [
        { provider: "authelia", matched: 2, orphans: [], ghosts: [] },
        { provider: "jellyfin", matched: 5, orphans: [], ghosts: [] },
      ],
    });
    const { result } = renderHook(() => useUsersReconcile(), {
      wrapper: withQueryClient(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.diffs).toEqual([]);
  });

  it("handles missing fields without crashing (defensive)", async () => {
    mockedFetcher.mockResolvedValueOnce({});
    const { result } = renderHook(() => useUsersReconcile(), {
      wrapper: withQueryClient(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.diffs).toEqual([]);
  });
});
