// SSE bridge for the controller's `/api/events` unified domain-event
// bus. Distinct from `useLogsSSE` (which streams raw log lines from a
// per-service ring buffer): this hook taps the typed `EventBus` and
// delivers `job.*`, `session.*`, `media_integrity.*` events as they
// happen so cards can refresh their TanStack Query cache without
// polling.
//
// API shape: the hook is a connection-only primitive. Consumers pass
// `onEvent(eventType, payload)` for their domain logic; the hook
// owns the EventSource lifecycle, reconnect-on-error, and graceful
// teardown. Returning the connection state lets the header pill flip
// between `● live` (SSE connected) and `◐ polling` (fall back to the
// existing 5-30s query refetch).
//
// The hook degrades gracefully:
//   - When EventSource is unavailable (or `enabled=false`), it
//     returns `isOpen=false` and never fires `onEvent`.
//   - Malformed `data:` payloads are silently dropped — a rogue
//     server emission can't crash the consumer.
//   - On error the EventSource closes and `error` surfaces; the
//     consumer decides whether to retry (the EventStreamProvider
//     wraps this hook with a backoff retry).

import { useEffect, useRef, useState } from "react";
import { getBaseUrl } from "@/api/client";

export type EventTopic =
  | "jobs"
  | "sessions"
  | "media_integrity"
  // ADR-0008 Phase 4: disk-pressure guardrail topic.
  | "storage";

export interface EventStreamPayload {
  /** Dotted event-type string (e.g. ``"job.started"``). */
  event_type: string;
  /** ISO-8601 UTC timestamp from the publisher. */
  ts: string;
  /** Remaining event fields are class-specific; consumers narrow. */
  [key: string]: unknown;
}

export interface UseEventStreamOptions {
  /** Master switch — false unmounts the EventSource. */
  enabled: boolean;
  /** Topics to subscribe to. Empty list = no subscription. */
  topics: readonly EventTopic[];
  /**
   * Callback invoked for every received event. Implementers decide
   * what to do (invalidate queries, push to state, etc). The hook
   * does not buffer — events not handled here are gone.
   */
  onEvent?: (eventType: string, payload: EventStreamPayload) => void;
  /**
   * Test seam: pass a constructor that returns a stand-in for
   * EventSource. The hook calls `new ctor(url)` exactly as the
   * browser's EventSource requires.
   */
  eventSourceCtor?: typeof EventSource;
}

export interface EventStreamState {
  /** True between socket-open and socket-close. */
  isOpen: boolean;
  /** Last error surfaced by the EventSource, if any. */
  error: Error | null;
}

/**
 * Build the `/api/events` URL with the requested topics. Exposed
 * for unit tests so the query encoding can be verified without
 * spinning up the hook.
 */
export function buildEventsUrl(
  topics: readonly EventTopic[],
  base: string = getBaseUrl(),
): string {
  const path =
    topics.length > 0
      ? `api/events?topics=${encodeURIComponent(topics.join(","))}`
      : "api/events";
  if (!base) return path;
  return `${base}/${path}`;
}

/**
 * Parse an SSE message payload into a typed event payload. Returns
 * null when JSON is malformed or the required ``event_type`` field
 * is missing.
 */
export function parseEventsSseFrame(data: string): EventStreamPayload | null {
  try {
    const obj = JSON.parse(data) as Record<string, unknown>;
    const eventType = obj["event_type"];
    if (typeof eventType !== "string" || eventType.length === 0) {
      return null;
    }
    const ts = typeof obj["ts"] === "string" ? obj["ts"] : "";
    return { ...obj, event_type: eventType, ts } as EventStreamPayload;
  } catch {
    return null;
  }
}

// Event-type names we re-listen for explicitly so the
// `addEventListener("job.started", …)` form (recommended by the
// SSE spec for typed events) actually fires. Without registering
// each name, the browser only delivers events to the default
// `message` channel — and the SSE handler emits `event: <type>`
// frames precisely so consumers can subscribe per-type.
const KNOWN_EVENT_TYPES: readonly string[] = [
  "job.started",
  "job.completed",
  "login.succeeded",
  "login.failed",
  "login.blocked",
  "session.created",
  "session.revoked",
  "ban.applied",
  "ban.removed",
  "password.changed",
  "security.emergency_revoke",
  "media_integrity.duplicate.review_needed",
  "media_integrity.duplicate.resolved",
  "media_integrity.config.enforced",
  "media_integrity.config.enforce_failed",
  "media_integrity.reconcile.failed",
];

export function useEventStream(
  opts: UseEventStreamOptions,
): EventStreamState {
  const { enabled, topics, onEvent, eventSourceCtor } = opts;
  const [isOpen, setIsOpen] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  // Pin the latest callback in a ref so re-renders of the consumer
  // don't churn the EventSource — only `enabled` / `topics` /
  // `eventSourceCtor` should rebuild the connection.
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  // String key so React's effect-dep array doesn't compare arrays
  // by reference (which would rebuild on every render).
  const topicsKey = topics.join(",");

  useEffect(() => {
    if (!enabled || topics.length === 0) {
      setIsOpen(false);
      return;
    }
    const Ctor =
      eventSourceCtor ??
      (typeof EventSource !== "undefined" ? EventSource : undefined);
    if (!Ctor) {
      setError(new Error("EventSource unavailable"));
      return;
    }
    const url = buildEventsUrl(topics);
    const es = new Ctor(url);
    setIsOpen(true);
    setError(null);

    const handle = (ev: MessageEvent) => {
      const payload = parseEventsSseFrame(ev.data);
      if (!payload) return;
      onEventRef.current?.(payload.event_type, payload);
    };
    for (const t of KNOWN_EVENT_TYPES) {
      es.addEventListener(t, handle as EventListener);
    }
    // Default ``message`` events are still possible if a future
    // server-side change drops the named ``event:`` line. Keeping
    // the ``onmessage`` hook means we don't go silent on that
    // accidental shape change.
    es.onmessage = handle;
    es.onerror = () => {
      setError(new Error("SSE connection error"));
      setIsOpen(false);
      es.close();
    };

    return () => {
      es.close();
      setIsOpen(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, topicsKey, eventSourceCtor]);

  return { isOpen, error };
}
