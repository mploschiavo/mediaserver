// App-shell-level mount for the unified `/api/events` SSE bus.
//
// Mounted once at the route root. Subscribes to the `jobs` topic
// and translates incoming events into TanStack Query cache
// invalidations so cards refresh without polling — `job.completed`
// invalidates the runs list + per-run detail caches; `job.started`
// invalidates the runs list (so the new running row shows up).
//
// Keeping the React Query bridge here (rather than in each card)
// means cards stay declarative readers of their query keys; the
// bridge owns the inversion of "push event arrived → which cache
// keys to refresh." Adding more event types is a single switch arm.

import {
  createContext,
  useContext,
  useMemo,
  type JSX,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useEventStream,
  type EventStreamPayload,
  type EventStreamState,
  type EventTopic,
} from "./useEventStream";

const DEFAULT_TOPICS: readonly EventTopic[] = [
  "jobs",
  "sessions",
  "media_integrity",
];

const EventStreamContext = createContext<EventStreamState>({
  isOpen: false,
  error: null,
});

/**
 * Read the current SSE connection state. Used by the header pill
 * (`ConnectionStatus`) to surface `● live` vs `◐ polling`.
 */
export function useEventStreamStatus(): EventStreamState {
  return useContext(EventStreamContext);
}

interface EventStreamProviderProps {
  children: ReactNode;
  /** Override topics — production omits to subscribe to the default set. */
  topics?: readonly EventTopic[];
  /** Test seam — pass a stub EventSource constructor. */
  eventSourceCtor?: typeof EventSource;
  /** Master switch — falsey unmounts the EventSource. */
  enabled?: boolean;
}

export function EventStreamProvider({
  children,
  topics = DEFAULT_TOPICS,
  eventSourceCtor,
  enabled = true,
}: EventStreamProviderProps): JSX.Element {
  const qc = useQueryClient();
  const state = useEventStream({
    enabled,
    topics,
    eventSourceCtor,
    onEvent: (eventType, payload) => {
      handleEvent(qc, eventType, payload);
    },
  });
  // Stable identity for context value so consumers re-render only
  // when state actually changes.
  const value = useMemo(
    () => ({ isOpen: state.isOpen, error: state.error }),
    [state.isOpen, state.error],
  );
  return (
    <EventStreamContext.Provider value={value}>
      {children}
    </EventStreamContext.Provider>
  );
}

/**
 * Map an incoming event onto a TanStack Query cache invalidation.
 * Exposed for unit tests so the routing logic is verified without
 * having to mount the provider + a mock EventSource.
 */
export function handleEvent(
  qc: ReturnType<typeof useQueryClient>,
  eventType: string,
  payload: EventStreamPayload,
): void {
  if (eventType === "job.started" || eventType === "job.completed") {
    void qc.invalidateQueries({ queryKey: ["runs"] });
    void qc.invalidateQueries({ queryKey: ["jobs"] });
    // Onboarding's auto-tracked checklist derives its status from
    // service health and registry state — both of which can flip
    // when a bootstrap step finishes. Refresh on job events so the
    // first-run checklist transitions in real time.
    void qc.invalidateQueries({ queryKey: ["onboarding"] });
    void qc.invalidateQueries({ queryKey: ["controller", "status"] });
    // ``job.completed`` also touches the per-run detail cache so
    // any open RunDrawer / LastRunPanel re-fetches the settled
    // record without waiting for its 30s slow poll.
    if (eventType === "job.completed") {
      const runId = typeof payload.run_id === "string" ? payload.run_id : "";
      if (runId) {
        void qc.invalidateQueries({ queryKey: ["runs", "detail", runId] });
      }
      const jobName =
        typeof payload.job_name === "string" ? payload.job_name : "";
      if (jobName) {
        void qc.invalidateQueries({ queryKey: ["runs", "latest", jobName] });
      }
    }
    return;
  }
  if (
    eventType === "session.created" ||
    eventType === "session.revoked" ||
    eventType === "login.succeeded" ||
    eventType === "login.failed" ||
    eventType === "login.blocked" ||
    eventType === "ban.applied" ||
    eventType === "ban.removed" ||
    eventType === "password.changed" ||
    eventType === "security.emergency_revoke"
  ) {
    void qc.invalidateQueries({ queryKey: ["sessions"] });
    void qc.invalidateQueries({ queryKey: ["bans"] });
    return;
  }
  if (eventType.startsWith("media_integrity.")) {
    void qc.invalidateQueries({ queryKey: ["media-integrity"] });
    return;
  }
  // Unknown event types are silently ignored — the SSE handler
  // already filtered to topics we asked for, so an unmapped name
  // here is a future-feature signal we don't need to act on.
}
