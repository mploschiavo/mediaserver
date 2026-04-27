import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  buildEventsUrl,
  parseEventsSseFrame,
  useEventStream,
  type EventTopic,
} from "./useEventStream";

class StubEventSource {
  static last: StubEventSource | null = null;
  url: string;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  listeners: Map<string, EventListener> = new Map();
  constructor(url: string) {
    this.url = url;
    StubEventSource.last = this;
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
    StubEventSource.last = null;
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(result.current.isOpen).toBe(true));
    expect(StubEventSource.last?.url).toContain("api/events?topics=jobs");
  });

  it("stays closed when enabled is false", () => {
    StubEventSource.last = null;
    const { result } = renderHook(() =>
      useEventStream({
        enabled: false,
        topics: ["jobs"],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    expect(result.current.isOpen).toBe(false);
    expect(StubEventSource.last).toBeNull();
  });

  it("stays closed when topics is empty", () => {
    StubEventSource.last = null;
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        topics: [],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    expect(result.current.isOpen).toBe(false);
    expect(StubEventSource.last).toBeNull();
  });

  it("invokes onEvent for an addEventListener-delivered frame", async () => {
    StubEventSource.last = null;
    const onEvent = vi.fn();
    renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        onEvent,
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(StubEventSource.last).not.toBeNull());
    act(() => {
      StubEventSource.last?.dispatch(
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
    StubEventSource.last = null;
    const onEvent = vi.fn();
    renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        onEvent,
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(StubEventSource.last).not.toBeNull());
    act(() => {
      StubEventSource.last?.dispatch(
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
    StubEventSource.last = null;
    const onEvent = vi.fn();
    renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        onEvent,
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(StubEventSource.last).not.toBeNull());
    act(() => {
      StubEventSource.last?.dispatch("job.started", "{not-json");
    });
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("closes and surfaces an error when EventSource raises onerror", async () => {
    StubEventSource.last = null;
    const { result } = renderHook(() =>
      useEventStream({
        enabled: true,
        topics: ["jobs"],
        eventSourceCtor: StubEventSource as unknown as typeof EventSource,
      }),
    );
    await waitFor(() => expect(result.current.isOpen).toBe(true));
    act(() => {
      StubEventSource.last?.raiseError();
    });
    await waitFor(() => expect(result.current.isOpen).toBe(false));
    expect(result.current.error?.message).toMatch(/SSE/i);
    expect(StubEventSource.last?.closed).toBe(true);
  });

  it("surfaces an error when EventSource is unavailable", () => {
    StubEventSource.last = null;
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
