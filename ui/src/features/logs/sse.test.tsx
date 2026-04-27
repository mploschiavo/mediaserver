import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  buildLogsSseUrl,
  parseLogsSseEvent,
  useLogsSSE,
} from "./sse";

vi.mock("@/api/client", () => ({
  getBaseUrl: () => "",
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }
  fail() {
    this.onerror?.();
  }
}

beforeEach(() => {
  FakeEventSource.instances.length = 0;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("buildLogsSseUrl", () => {
  it("returns the bare path with no filters", () => {
    expect(buildLogsSseUrl({}, "")).toBe("api/logs/stream");
  });

  it("encodes action, level, q in the query string", () => {
    const url = buildLogsSseUrl(
      { action: "envoy-config", level: "error", q: "/timeout/i" },
      "",
    );
    expect(url).toBe(
      "api/logs/stream?action=envoy-config&level=error&q=%2Ftimeout%2Fi",
    );
  });

  it("includes after_seq only when > 0", () => {
    expect(buildLogsSseUrl({ afterSeq: 0 }, "")).toBe("api/logs/stream");
    expect(buildLogsSseUrl({ afterSeq: 5 }, "")).toBe(
      "api/logs/stream?after_seq=5",
    );
  });

  it("prefixes the base URL when set", () => {
    expect(buildLogsSseUrl({ action: "x" }, "https://api.test")).toBe(
      "https://api.test/api/logs/stream?action=x",
    );
  });
});

describe("parseLogsSseEvent", () => {
  it("returns null for invalid JSON", () => {
    expect(parseLogsSseEvent("not json")).toBeNull();
  });

  it("returns null when required fields are missing", () => {
    expect(parseLogsSseEvent('{"msg":"x"}')).toBeNull();
    expect(parseLogsSseEvent('{"seq":1,"msg":"x"}')).toBeNull();
    expect(parseLogsSseEvent('{"seq":1,"ts":"t"}')).toBeNull();
  });

  it("parses a valid payload and defaults action to empty string", () => {
    const out = parseLogsSseEvent('{"seq":3,"ts":"t","msg":"hello"}');
    expect(out).toEqual({ seq: 3, ts: "t", msg: "hello", action: "" });
  });

  it("preserves action when present", () => {
    const out = parseLogsSseEvent(
      '{"seq":1,"ts":"t","msg":"x","action":"envoy"}',
    );
    expect(out?.action).toBe("envoy");
  });
});

describe("useLogsSSE", () => {
  it("does not open a connection when disabled", () => {
    renderHook(() =>
      useLogsSSE({
        enabled: false,
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    expect(FakeEventSource.instances.length).toBe(0);
  });

  it("opens the EventSource and reports isOpen=true when enabled", () => {
    const { result } = renderHook(() =>
      useLogsSSE({
        enabled: true,
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    expect(FakeEventSource.instances.length).toBe(1);
    expect(result.current.isOpen).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("appends parsed lines on message events", () => {
    const { result } = renderHook(() =>
      useLogsSSE({
        enabled: true,
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    const es = FakeEventSource.instances[0]!;
    act(() => {
      es.emit('{"seq":1,"ts":"t1","msg":"a","action":""}');
      es.emit('{"seq":2,"ts":"t2","msg":"b","action":"x"}');
    });
    expect(result.current.lines).toHaveLength(2);
    expect(result.current.lines[1]?.msg).toBe("b");
  });

  it("ignores malformed payloads silently", () => {
    const { result } = renderHook(() =>
      useLogsSSE({
        enabled: true,
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    const es = FakeEventSource.instances[0]!;
    act(() => {
      es.emit("not json");
      es.emit('{"seq":1,"ts":"t","msg":"good"}');
    });
    expect(result.current.lines).toHaveLength(1);
    expect(result.current.lines[0]?.msg).toBe("good");
  });

  it("caps the buffer at maxLines (drops oldest)", () => {
    const { result } = renderHook(() =>
      useLogsSSE({
        enabled: true,
        maxLines: 3,
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    const es = FakeEventSource.instances[0]!;
    act(() => {
      for (let i = 1; i <= 5; i++) {
        es.emit(`{"seq":${i},"ts":"t","msg":"m${i}"}`);
      }
    });
    expect(result.current.lines.map((l) => l.msg)).toEqual([
      "m3",
      "m4",
      "m5",
    ]);
  });

  it("surfaces an error and closes the socket on failure", () => {
    const { result } = renderHook(() =>
      useLogsSSE({
        enabled: true,
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    const es = FakeEventSource.instances[0]!;
    act(() => {
      es.fail();
    });
    expect(result.current.error).not.toBeNull();
    expect(result.current.isOpen).toBe(false);
    expect(es.closed).toBe(true);
  });

  it("threads filters into the URL", () => {
    renderHook(() =>
      useLogsSSE({
        enabled: true,
        filters: { action: "scan", level: "error" },
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    expect(FakeEventSource.instances[0]?.url).toContain("action=scan");
    expect(FakeEventSource.instances[0]?.url).toContain("level=error");
  });

  it("rebuilds the connection when filters change", () => {
    const { rerender } = renderHook(
      ({ q }: { q: string }) =>
        useLogsSSE({
          enabled: true,
          filters: { q },
          eventSourceCtor:
            FakeEventSource as unknown as typeof EventSource,
        }),
      { initialProps: { q: "first" } },
    );
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0]?.closed).toBe(false);

    rerender({ q: "second" });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[0]?.closed).toBe(true);
    expect(FakeEventSource.instances[1]?.url).toContain("q=second");
  });

  it("closes the socket on unmount", () => {
    const { unmount } = renderHook(() =>
      useLogsSSE({
        enabled: true,
        eventSourceCtor: FakeEventSource as unknown as typeof EventSource,
      }),
    );
    const es = FakeEventSource.instances[0]!;
    unmount();
    expect(es.closed).toBe(true);
  });

  it("reports an error if neither EventSource nor a ctor is available", () => {
    const realES = (globalThis as { EventSource?: unknown }).EventSource;
    (globalThis as { EventSource?: unknown }).EventSource = undefined;
    try {
      const { result } = renderHook(() =>
        useLogsSSE({ enabled: true }),
      );
      expect(result.current.error).not.toBeNull();
    } finally {
      (globalThis as { EventSource?: unknown }).EventSource = realES;
    }
  });
});
