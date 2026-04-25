import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  useGenerateToken,
  useMe,
  useMeLoginHistory,
  useMeMfaState,
  useMeSessions,
  useMeTokens,
  useRevokeMySession,
  useRevokeOthers,
  useRevokeToken,
  useThisWasntMe,
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

describe("me feature hooks", () => {
  it("useMe hits GET /api/me", async () => {
    fetchMock.mockResolvedValue(ok({ id: "u1", display_name: "Matt" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useMe(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/me$/);
  });

  it("useMeSessions hits GET /api/me/sessions", async () => {
    fetchMock.mockResolvedValue(ok({ sessions: [] }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useMeSessions(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/api\/me\/sessions$/);
  });

  it("useMeTokens hits GET /api/me/tokens", async () => {
    fetchMock.mockResolvedValue(ok({ tokens: [] }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useMeTokens(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/api\/me\/tokens$/);
  });

  it("useMeMfaState hits GET /api/me/mfa-state", async () => {
    fetchMock.mockResolvedValue(ok({ enabled: false }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useMeMfaState(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(
      /api\/me\/mfa-state$/,
    );
  });

  it("useMeLoginHistory skips when userId is empty", () => {
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useMeLoginHistory(undefined), {
      wrapper: Wrapper,
    });
    expect(result.current.isLoading).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("useMeLoginHistory hits GET /api/users/{id}/login-history", async () => {
    fetchMock.mockResolvedValue(ok({ entries: [] }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useMeLoginHistory("u1"), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(
      /api\/users\/u1\/login-history$/,
    );
  });

  it("useRevokeOthers POSTs /api/me/revoke-others", async () => {
    fetchMock.mockResolvedValue(ok({ revoked: 3 }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useRevokeOthers(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/me\/revoke-others$/);
    expect((init as RequestInit).method).toBe("POST");
  });

  it("useThisWasntMe POSTs /api/me/this-wasnt-me with the body", async () => {
    fetchMock.mockResolvedValue(ok({}));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useThisWasntMe(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync({ session_id: "s1" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/me\/this-wasnt-me$/);
    expect((init as RequestInit).body).toContain("s1");
  });

  it("useGenerateToken POSTs /api/tokens", async () => {
    fetchMock.mockResolvedValue(ok({ id: "t1", token: "raw" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useGenerateToken(), {
      wrapper: Wrapper,
    });
    const res = await result.current.mutateAsync({ name: "ci" });
    expect(res.token).toBe("raw");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/tokens$/);
    expect((init as RequestInit).method).toBe("POST");
  });

  it("useRevokeToken POSTs /api/tokens/{id}", async () => {
    fetchMock.mockResolvedValue(ok({}));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useRevokeToken(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync("t1");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/tokens\/t1$/);
    expect((init as RequestInit).method).toBe("POST");
  });

  it("useRevokeMySession POSTs /api/users/{user}/sessions/{sid}/revoke", async () => {
    fetchMock.mockResolvedValue(ok({}));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useRevokeMySession(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync({ userId: "u1", sessionId: "s2" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/users\/u1\/sessions\/s2\/revoke$/);
    expect((init as RequestInit).method).toBe("POST");
  });
});
