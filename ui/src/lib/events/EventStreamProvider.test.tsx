import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import {
  EventStreamProvider,
  handleEvent,
  useEventStreamStatus,
} from "./EventStreamProvider";
import type { EventStreamPayload } from "./useEventStream";

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
  removeEventListener(): void {}
  dispatch(name: string, data: string): void {
    const listener = this.listeners.get(name);
    if (listener) {
      listener(new MessageEvent(name, { data }));
    } else if (name === "message" && this.onmessage) {
      this.onmessage(new MessageEvent("message", { data }));
    }
  }
  close(): void {
    this.closed = true;
  }
}

function makeQc() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  });
  const spy = vi.spyOn(qc, "invalidateQueries");
  return { qc, spy };
}

function payload(
  eventType: string,
  extra: Partial<EventStreamPayload> = {},
): EventStreamPayload {
  return {
    event_type: eventType,
    ts: "2026-04-27T18:00:00Z",
    ...extra,
  };
}

describe("handleEvent", () => {
  it("invalidates runs + jobs on job.started", () => {
    const { qc, spy } = makeQc();
    handleEvent(qc, "job.started", payload("job.started"));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["runs"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["jobs"] });
  });

  it("also invalidates per-run + per-job-latest detail caches on job.completed", () => {
    const { qc, spy } = makeQc();
    handleEvent(
      qc,
      "job.completed",
      payload("job.completed", {
        run_id: "01J5RUN0000000000000",
        job_name: "scan",
      }),
    );
    expect(spy).toHaveBeenCalledWith({ queryKey: ["runs"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["jobs"] });
    expect(spy).toHaveBeenCalledWith({
      queryKey: ["runs", "detail", "01J5RUN0000000000000"],
    });
    expect(spy).toHaveBeenCalledWith({
      queryKey: ["runs", "latest", "scan"],
    });
  });

  it("skips per-run detail when run_id is missing on job.completed", () => {
    const { qc, spy } = makeQc();
    handleEvent(qc, "job.completed", payload("job.completed"));
    // Top-level keys still invalidated, but no per-run detail key.
    const calls = spy.mock.calls.map((c) => c[0]?.queryKey);
    expect(
      calls.some(
        (k) =>
          Array.isArray(k) && k.length === 3 && k[0] === "runs" && k[1] === "detail",
      ),
    ).toBe(false);
  });

  it("invalidates sessions + bans on session.created", () => {
    const { qc, spy } = makeQc();
    handleEvent(qc, "session.created", payload("session.created"));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["sessions"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["bans"] });
  });

  it("invalidates sessions + bans on ban.applied", () => {
    const { qc, spy } = makeQc();
    handleEvent(qc, "ban.applied", payload("ban.applied"));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["sessions"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["bans"] });
  });

  it("invalidates media-integrity on any media_integrity.* event", () => {
    const { qc, spy } = makeQc();
    handleEvent(
      qc,
      "media_integrity.duplicate.review_needed",
      payload("media_integrity.duplicate.review_needed"),
    );
    expect(spy).toHaveBeenCalledWith({
      queryKey: ["media-integrity"],
    });
  });

  it("does nothing for unknown event types", () => {
    const { qc, spy } = makeQc();
    handleEvent(qc, "widget.changed", payload("widget.changed"));
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("EventStreamProvider", () => {
  it("provides a closed status by default and an open status once the EventSource opens", async () => {
    StubEventSource.last = null;
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let captured: { isOpen: boolean } = { isOpen: false };
    function Probe(): JSX.Element {
      captured = useEventStreamStatus();
      return <div data-testid="probe">{captured.isOpen ? "open" : "closed"}</div>;
    }
    render(
      <QueryClientProvider client={qc}>
        <EventStreamProvider
          topics={["jobs"]}
          eventSourceCtor={StubEventSource as unknown as typeof EventSource}
        >
          <Probe />
        </EventStreamProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(captured.isOpen).toBe(true));
    expect(StubEventSource.last?.url).toContain("api/events?topics=jobs");
  });

  it("invalidates ['runs'] on a job.completed frame from the live stream", async () => {
    StubEventSource.last = null;
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const spy = vi.spyOn(qc, "invalidateQueries");
    render(
      <QueryClientProvider client={qc}>
        <EventStreamProvider
          topics={["jobs"]}
          eventSourceCtor={StubEventSource as unknown as typeof EventSource}
        >
          <div />
        </EventStreamProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(StubEventSource.last).not.toBeNull());
    act(() => {
      StubEventSource.last?.dispatch(
        "job.completed",
        JSON.stringify({
          event_type: "job.completed",
          ts: "2026-04-27T18:00:00Z",
          run_id: "01J5",
          job_name: "scan",
          status: "ok",
        }),
      );
    });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["runs"] });
    expect(spy).toHaveBeenCalledWith({
      queryKey: ["runs", "detail", "01J5"],
    });
  });

  it("does not open the stream when enabled is false", () => {
    StubEventSource.last = null;
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <EventStreamProvider
          enabled={false}
          topics={["jobs"]}
          eventSourceCtor={StubEventSource as unknown as typeof EventSource}
        >
          <div />
        </EventStreamProvider>
      </QueryClientProvider>,
    );
    expect(StubEventSource.last).toBeNull();
  });
});
