import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

import {
  useDisableGuardrail,
  useGuardrails,
  useTestGuardrail,
  useUpdateGuardrail,
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

describe("guardrails feature hooks", () => {
  beforeEach(() => {
    fetcherMock.mockReset();
  });
  afterEach(() => {
    fetcherMock.mockReset();
  });

  it("useGuardrails GETs api/guardrails and coerces non-array payload", async () => {
    fetcherMock.mockResolvedValue({
      guardrails: [
        { id: "storage:per_mount_threshold", domain: "storage" },
      ],
    });
    const { result } = renderHook(() => useGuardrails(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(fetcherMock).toHaveBeenCalledWith("api/guardrails");
    expect(result.current.data?.guardrails.length).toBe(1);
  });

  it("useGuardrails collapses non-array payload to empty list", async () => {
    fetcherMock.mockResolvedValue({ guardrails: "not an array" });
    const { result } = renderHook(() => useGuardrails(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.guardrails).toEqual([]);
  });

  it("useUpdateGuardrail POSTs threshold body to api/guardrails/{id}", async () => {
    fetcherMock.mockResolvedValue({ rule_id: "x", threshold: { a: 1 } });
    const { result } = renderHook(
      () => useUpdateGuardrail("storage:per_mount_threshold"),
      { wrapper: makeWrapper() },
    );
    await result.current.mutateAsync({ max_percent: 90 });
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/guardrails/storage%3Aper_mount_threshold",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(
      (fetcherMock.mock.calls[0]?.[1] as { body: string }).body,
    );
    expect(body).toEqual({ threshold: { max_percent: 90 } });
  });

  it("useTestGuardrail POSTs api/guardrails/{id}/test (no body)", async () => {
    fetcherMock.mockResolvedValue({
      would_trigger: true,
      severity: "warning",
      current_value: 80,
      threshold: { max_percent: 75 },
    });
    const { result } = renderHook(
      () => useTestGuardrail("storage:per_mount_threshold"),
      { wrapper: makeWrapper() },
    );
    const data = await result.current.mutateAsync();
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/guardrails/storage%3Aper_mount_threshold/test",
      expect.objectContaining({ method: "POST" }),
    );
    expect(data.would_trigger).toBe(true);
    expect(data.severity).toBe("warning");
  });

  it("useDisableGuardrail POSTs disable body to api/guardrails/{id}/disable", async () => {
    fetcherMock.mockResolvedValue({ rule_id: "x", disabled: true });
    const { result } = renderHook(
      () => useDisableGuardrail("auth:failed_login_spike"),
      { wrapper: makeWrapper() },
    );
    await result.current.mutateAsync(true);
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/guardrails/auth%3Afailed_login_spike/disable",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(
      (fetcherMock.mock.calls[0]?.[1] as { body: string }).body,
    );
    expect(body).toEqual({ disabled: true });
  });
});
