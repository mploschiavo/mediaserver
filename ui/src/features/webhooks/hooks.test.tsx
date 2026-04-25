import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
}));

import {
  useAddWebhook,
  useArrWebhooks,
  useDeleteWebhook,
  useTestWebhooks,
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

describe("webhooks feature hooks", () => {
  beforeEach(() => {
    fetcherMock.mockReset();
  });
  afterEach(() => {
    fetcherMock.mockReset();
  });

  it("useAddWebhook posts to /webhooks with the body", async () => {
    fetcherMock.mockResolvedValue({ webhook_urls: ["https://e.test/x"] });
    const { result } = renderHook(() => useAddWebhook(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync({
      url: "https://e.test/x",
      event_type: "movie.imported",
    });
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/webhooks",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          url: "https://e.test/x",
          event_type: "movie.imported",
        }),
      }),
    );
  });

  it("useDeleteWebhook DELETEs with the id query parameter", async () => {
    fetcherMock.mockResolvedValue({});
    const { result } = renderHook(() => useDeleteWebhook(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync({ id: "wh-77" });
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/webhooks?id=wh-77",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("useTestWebhooks POSTs to /webhooks/test", async () => {
    fetcherMock.mockResolvedValue({ status: "tested", tested: 1 });
    const { result } = renderHook(() => useTestWebhooks(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync();
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/webhooks/test",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("useArrWebhooks GETs /api/arr-webhooks and returns the data", async () => {
    fetcherMock.mockResolvedValue({
      services: [{ service: "sonarr", configured: true }],
    });
    const { result } = renderHook(() => useArrWebhooks(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(fetcherMock).toHaveBeenCalledWith("api/arr-webhooks");
    expect(result.current.data?.services?.[0]?.service).toBe("sonarr");
  });
});
