import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  buildEventsUrl,
  parseEventsSseFrame,
  useEventStream,
  type EventTopic,
} from "./useEventStream";

// Module-scoped reference to the most recently constructed stub.
// A static field on the class would also work, but the strict-TS
// build narrows ``lastSse()`` to its assigned literal
// after a ``= null`` reset and never re-broadens — even though the
// constructor reassigns it later. Using a module-level ``let`` with
// an explicit annotation sidesteps the control-flow narrowing.
let lastStub: StubEventSource | null = null;
function lastSse(): StubEventSource | null {
  return lastStub;
}
function resetStub(): void {
  lastStub = null;
}

class StubEventSource {
  url: string;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  listeners: Map<string, EventListener> = new Map();
  constructor(url: string) {
    this.url = url;
    lastStub = this;
  }
  addEventListener(name: string, fn: EventListener): void {
    this.listeners.set(name, fn);
  }
  removeEventListener(name: string): void {
    this.listeners.delete(name);
  }
  dispatch(name: string, data: string): void {
    const listener = this.listeners.get(name);
    if (listener) {
      listener(new MessageEvent(name, { data }));
    } else if (name === "message" && this.onmessage) {
      this.onmessage(new MessageEvent("message", { data }));
    }
  }
  raiseError(): void {
    this.onerror?.(new Event("error"));
  }
  close(): void {
    this.closed = true;
  }
}

describe("buildEventsUrl", () => {
  it("encodes the topics csv as a query param", () => {
    expect(
      buildEventsUrl(["jobs", "sessions"] satisfies EventTopic[], ""),
    ).toBe("api/events?topics=jobs%2Csessions");
  });

  it("omits topics when the list is empty", () => {
    expect(buildEventsUrl([], "")).toBe("api/events");
  });

  it("prepends the api base when present", () => {
    expect(buildEventsUrl(["jobs"], "/api")).toBe(
      "/api/api/events?topics=jobs",
    );
  });
});

describe("parseEventsSseFrame", () => {
  it("returns the parsed payload for a valid frame", () => {
    const out = parseEventsSseFrame(
      JSON.stringify({
        event_type: "job.started",
        ts: "2026-04-27T18:00:00Z",
        run_id: "01J5",
      }),
    );
    expect(out).toEqual({
      event_type: "job.started",
      ts: "2026-04-27T18:00:00Z",
      run_id: "01J5",
    });
  });

  it("returns null on malformed JSON", () => {
    expect(parseEventsSseFrame("{not-json")).toBeNull();
  });

  it("returns null when event_type is missing", () => {
    expect(parseEventsSseFrame(JSON.stringify({ ts: "x" }))).toBeNull();
  });

  it("returns null when event_type is empty", () => {
    expect(
      parseEventsSseFrame(JSON.stringify({ event_type: "", ts: "x" })),
    ).toBeNull();
  });

  it("backfills ts to '' when missing", () => {
    const out = parseEventsSseFrame(
      JSON.stringify({ event_type: "job.started" }),
    );
    expect(out?.ts).toBe("");
  });
});

describe("useEventStream", () => {
  it("opens a connection when enabled with topics", async () => {
    resetStub();
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(result.current.isOpen).toBe(true));
    expect(lastSse()?.url).toContain("api/events?topics=jobs");
  });

  it("stays closed when enabled is false", () => {
    resetStub();
    const { result } = renderHook(() =>
      useEventStream({
        enabled: false,
        topics: ["jobs"],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    expect(result.current.isOpen).toBe(false);
    expect(lastSse()).toBeNull();
  });

  it("stays closed when topics is empty", () => {
    resetStub();
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        topics: [],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    expect(result.current.isOpen).toBe(false);
    expect(lastSse()).toBeNull();
  });

  it("invokes onEvent for an addEventListener-delivered frame", async () => {
    resetStub();
    const onEvent = vi.fn();
    renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        onEvent,
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(lastSse()).not.toBeNull());
    act(() => {
      lastSse()?.dispatch(
        "job.completed",
        JSON.stringify({
          event_type: "job.completed",
          ts: "2026-04-27T18:00:00Z",
          run_id: "01J5",
          status: "ok",
        }),
      );
    });
    expect(onEvent).toHaveBeenCalledWith(
      "job.completed",
      expect.objectContaining({
        event_type: "job.completed",
        run_id: "01J5",
      }),
    );
  });

  it("invokes onEvent for a default 'message' frame", async () => {
    resetStub();
    const onEvent = vi.fn();
    renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        onEvent,
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(lastSse()).not.toBeNull());
    act(() => {
      lastSse()?.dispatch(
        "message",
        JSON.stringify({ event_type: "job.started", ts: "x" }),
      );
    });
    expect(onEvent).toHaveBeenCalledWith(
      "job.started",
      expect.objectContaining({ event_type: "job.started" }),
    );
  });

  it("silently drops malformed frames", async () => {
    resetStub();
    const onEvent = vi.fn();
    renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        onEvent,
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(lastSse()).not.toBeNull());
    act(() => {
      lastSse()?.dispatch("job.started", "{not-json");
    });
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("closes and surfaces an error when EventSource raises onerror", async () => {
    resetStub();
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(result.current.isOpen).toBe(true));
    act(() => {
      lastSse()?.raiseError();
    });
    await waitFor(() => expect(result.current.isOpen).toBe(false));
    expect(result.current.error?.message).toMatch(/SSE/i);
    expect(lastSse()?.closed).toBe(true);
  });

  it("surfaces an error when EventSource is unavailable", () => {
    resetStub();
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        eventSourceCtor: undefined,
      }),
    );
    // happy-dom provides EventSource so this branch only fires when
    // we explicitly pass undefined AND the global is missing — skip
    // when the runtime has it.
    if (typeof EventSource !== "undefined") {
      expect(result.current.error).toBeNull();
      return;
    }
    expect(result.current.error?.message).toMatch(/EventSource/);
  });
});
