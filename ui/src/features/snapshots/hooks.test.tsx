import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

import {
  useDownloadBackup,
  useRestoreBackup,
  useSnapshotContent,
  useSnapshotDiff,
  useSnapshots,
  useTakeSnapshot,
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

describe("snapshots feature hooks", () => {
  beforeEach(() => {
    fetcherMock.mockReset();
  });
  afterEach(() => {
    fetcherMock.mockReset();
  });

  it("useSnapshots GETs /api/snapshots", async () => {
    fetcherMock.mockResolvedValue({ snapshots: [] });
    const { result } = renderHook(() => useSnapshots(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(fetcherMock).toHaveBeenCalledWith("api/snapshots");
  });

  it("useSnapshotContent GETs /api/snapshots/{filename}", async () => {
    fetcherMock.mockResolvedValue({ file: "x.json", snapshot: {} });
    const { result } = renderHook(() => useSnapshotContent("x.json"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(fetcherMock).toHaveBeenCalledWith("api/snapshots/x.json");
  });

  it("useSnapshotDiff GETs /api/snapshot-diff with both query params", async () => {
    fetcherMock.mockResolvedValue({ diffs: [] });
    const { result } = renderHook(() => useSnapshotDiff("a.json", "b.json"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/snapshot-diff?a=a.json&b=b.json",
    );
  });

  it("useSnapshotDiff is disabled when a === b", async () => {
    fetcherMock.mockResolvedValue({ diffs: [] });
    const { result } = renderHook(() => useSnapshotDiff("x.json", "x.json"), {
      wrapper: makeWrapper(),
    });
    // Wait one tick — query should NOT have fired.
    await new Promise((r) => setTimeout(r, 5));
    expect(fetcherMock).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
  });

  it("useTakeSnapshot POSTs /api/snapshot", async () => {
    fetcherMock.mockResolvedValue({
      status: "created",
      file: "x.json",
      configs: 5,
    });
    const { result } = renderHook(() => useTakeSnapshot(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync();
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/snapshot",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("useDownloadBackup triggers an anchor click on /api/backup", async () => {
    const { result } = renderHook(() => useDownloadBackup(), {
      wrapper: makeWrapper(),
    });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click");
    await result.current.mutateAsync();
    expect(clickSpy).toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it("useRestoreBackup parses the file and POSTs to /api/restore", async () => {
    fetcherMock.mockResolvedValue({ status: "ok", restored: ["x.cfg"] });
    const file = new File(
      [JSON.stringify({ service_configs: { "x.cfg": "1" } })],
      "backup.json",
      { type: "application/json" },
    );
    const { result } = renderHook(() => useRestoreBackup(), {
      wrapper: makeWrapper(),
    });
    await result.current.mutateAsync({ file });
    expect(fetcherMock).toHaveBeenCalledWith(
      "api/restore",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ service_configs: { "x.cfg": "1" } }),
      }),
    );
  });

  it("useRestoreBackup rejects when the file lacks service_configs", async () => {
    const file = new File([JSON.stringify({ other: 1 })], "backup.json", {
      type: "application/json",
    });
    const { result } = renderHook(() => useRestoreBackup(), {
      wrapper: makeWrapper(),
    });
    await expect(result.current.mutateAsync({ file })).rejects.toThrow(
      /service_configs/,
    );
  });
});
