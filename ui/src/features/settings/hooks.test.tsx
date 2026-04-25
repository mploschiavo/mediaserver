import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  isSensitiveKey,
  settingsKeys,
  useConfigDrift,
  useDeleteEnvVar,
  useDisplayPreferences,
  useEffectiveEnv,
  useEnvVars,
  useLogLevel,
  useProfileYaml,
  useSaveDisplayPreferences,
  useSaveProfile,
  useSetLogLevel,
  type EnvVarsResponse,
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

describe("settings feature hooks", () => {
  it("useProfileYaml hits GET /api/profile", async () => {
    fetchMock.mockResolvedValue(ok({ yaml: "name: ok" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useProfileYaml(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/api\/profile$/);
  });

  it("useEffectiveEnv hits GET /api/env", async () => {
    fetchMock.mockResolvedValue(ok({ env: [] }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useEffectiveEnv(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/api\/env$/);
  });

  it("useEnvVars hits GET /api/envvars", async () => {
    fetchMock.mockResolvedValue(ok({ vars: [] }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useEnvVars(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/api\/envvars$/);
  });

  it("useConfigDrift hits GET /api/config-drift", async () => {
    fetchMock.mockResolvedValue(ok({ drift: [] }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useConfigDrift(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(
      /api\/config-drift$/,
    );
  });

  it("useDisplayPreferences hits GET /api/display-preferences", async () => {
    fetchMock.mockResolvedValue(ok({ theme: "dark" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useDisplayPreferences(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(
      /api\/display-preferences$/,
    );
  });

  it("useLogLevel hits GET /api/log-level", async () => {
    fetchMock.mockResolvedValue(ok({ level: "info" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useLogLevel(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/api\/log-level$/);
  });

  it("useSaveProfile POSTs /api/profile with the YAML body", async () => {
    fetchMock.mockResolvedValue(ok({ saved_at: "2024-01-01T00:00:00Z" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useSaveProfile(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync({ yaml: "version: 1" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/profile$/);
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).body).toContain("version");
  });

  it("useSaveDisplayPreferences POSTs /api/display-preferences", async () => {
    fetchMock.mockResolvedValue(ok({ theme: "dark" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useSaveDisplayPreferences(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync({ theme: "dark" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/display-preferences$/);
    expect((init as RequestInit).method).toBe("POST");
  });

  it("useSetLogLevel POSTs /api/log-level with the level body", async () => {
    fetchMock.mockResolvedValue(ok({ level: "debug" }));
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useSetLogLevel(), {
      wrapper: Wrapper,
    });
    await result.current.mutateAsync({ level: "debug" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/api\/log-level$/);
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).body).toContain("debug");
  });

  describe("useDeleteEnvVar", () => {
    it("POSTs /api/envvars/delete with the key body", async () => {
      fetchMock.mockResolvedValue(
        ok({ status: "deleted", key: "TZ", existed: true }),
      );
      const { Wrapper } = createWrapper();
      const { result } = renderHook(() => useDeleteEnvVar(), {
        wrapper: Wrapper,
      });
      await result.current.mutateAsync({ key: "TZ" });
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(String(url)).toMatch(/api\/envvars\/delete$/);
      expect((init as RequestInit).method).toBe("POST");
      expect((init as RequestInit).body).toContain("TZ");
    });

    it("optimistically drops the row from the cached env-vars list", async () => {
      fetchMock.mockImplementation(
        () =>
          // Hold the response open so the optimistic snapshot can be
          // observed before the mutation settles.
          new Promise<Response>((resolve) =>
            setTimeout(
              () => resolve(ok({ status: "deleted", key: "TZ", existed: true })),
              30,
            ),
          ),
      );
      // Cache entries set via ``setQueryData`` without an active
      // ``useQuery`` observer are subject to immediate GC under the
      // shared ``gcTime: 0`` config. Override here so the
      // optimistic-update path can read the seeded data.
      const qc = new QueryClient({
        defaultOptions: {
          queries: { retry: false, gcTime: Infinity, staleTime: Infinity },
          mutations: { retry: false },
        },
      });
      const Wrapper = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      );
      qc.setQueryData<EnvVarsResponse>(settingsKeys.envVars, {
        vars: [
          { key: "TZ", value: "UTC" },
          { key: "BOOTSTRAP_PROFILE", value: "/etc/profile" },
        ],
      });
      const { result } = renderHook(() => useDeleteEnvVar(), {
        wrapper: Wrapper,
      });
      const promise = result.current.mutateAsync({ key: "TZ" });
      // Allow onMutate to run before we snapshot the cache.
      await waitFor(() => {
        const cached = qc.getQueryData<EnvVarsResponse>(settingsKeys.envVars);
        expect(cached?.vars).toEqual([
          { key: "BOOTSTRAP_PROFILE", value: "/etc/profile" },
        ]);
      });
      await promise;
    });

    it("rolls the cache back when the server rejects the delete", async () => {
      fetchMock.mockResolvedValue(
        new Response(JSON.stringify({ error: "key field required" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      );
      const { qc, Wrapper } = createWrapper();
      const initial: EnvVarsResponse = {
        vars: [
          { key: "TZ", value: "UTC" },
          { key: "BOOTSTRAP_PROFILE", value: "/etc/profile" },
        ],
      };
      qc.setQueryData<EnvVarsResponse>(settingsKeys.envVars, initial);
      const { result } = renderHook(() => useDeleteEnvVar(), {
        wrapper: Wrapper,
      });
      await expect(
        result.current.mutateAsync({ key: "TZ" }),
      ).rejects.toBeInstanceOf(Error);
      // After onError + onSettled the cache is invalidated; until the
      // refetch lands the rolled-back snapshot is what the UI sees.
      const cached = qc.getQueryData<EnvVarsResponse>(settingsKeys.envVars);
      expect(cached?.vars).toEqual(initial.vars);
    });
  });

  describe("isSensitiveKey", () => {
    it("flags keys containing PASSWORD/SECRET/KEY/TOKEN", () => {
      expect(isSensitiveKey("DB_PASSWORD")).toBe(true);
      expect(isSensitiveKey("api_secret")).toBe(true);
      expect(isSensitiveKey("ANTHROPIC_API_KEY")).toBe(true);
      expect(isSensitiveKey("auth_token")).toBe(true);
    });

    it("returns false for benign keys", () => {
      expect(isSensitiveKey("LOG_LEVEL")).toBe(false);
      expect(isSensitiveKey("HOSTNAME")).toBe(false);
      expect(isSensitiveKey("")).toBe(false);
    });
  });
});
