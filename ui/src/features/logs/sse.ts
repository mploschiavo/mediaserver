// SSE bridge for the controller's `/api/logs/stream` endpoint.
//
// Distinct from `useMultiLogs` (which polls the per-service `kubectl
// logs --tail=N` endpoint on a 3-second cadence): this hook taps the
// controller's in-memory ring buffer directly and pushes new lines as
// they arrive. The wins:
//
//   - Sub-second feel for live tail mode instead of 3s rounds.
//   - Filters (action / level / q) are evaluated server-side, so the
//     UI only ships pre-filtered lines instead of dumping the whole
//     buffer through a useMemo for client-side filtering.
//   - No request thrash on quiet streams — the SSE socket idles until
//     `state.wait_for_log` fires.
//
// The hook degrades gracefully:
//
//   - When EventSource is unavailable (or `enabled=false`), it returns
//     an empty buffer with `isOpen=false` and the page falls back to
//     polling via `useMultiLogs`.
//   - On error, it sets `error` and stops re-opening; the consumer
//     decides whether to retry.

import { useEffect, useState } from "react";
import { getBaseUrl } from "@/api/client";

export interface LogsSseFilters {
  /** Filter to lines whose recorded `action` field equals this name. */
  action?: string;
  /** Backend-side level token (`error`, `warning`, `info`, `debug`). */
  level?: "error" | "warning" | "info" | "debug";
  /** Free text or `/regex/i`. Same syntax as the polling endpoint. */
  q?: string;
  /** Resume from this seq value (inclusive). 0 ≡ "from the start of the buffer". */
  afterSeq?: number;
}

export interface LogsSseLine {
  seq: number;
  ts: string;
  msg: string;
  action: string;
}

export interface LogsSseState {
  /** Most recent lines, oldest-first, capped at `maxLines`. */
  lines: readonly LogsSseLine[];
  /** True between socket-open and socket-close. */
  isOpen: boolean;
  /** Last error surfaced by the EventSource, if any. */
  error: Error | null;
}

interface UseLogsSseOptions {
  /** Master switch — false unmounts the EventSource. */
  enabled: boolean;
  /** Max lines kept in client buffer. Drops oldest when exceeded. */
  maxLines?: number;
  /** Server-side filters threaded onto the URL. */
  filters?: LogsSseFilters;
  /**
   * Test seam: pass a constructor that returns a stand-in for
   * EventSource. The hook calls `new ctor(url)` exactly as the
   * browser's EventSource requires.
   */
  eventSourceCtor?: typeof EventSource;
}

/**
 * Build the `/api/logs/stream` URL with the requested filters.
 * Exposed so tests can verify the query encoding without spinning up
 * the hook.
 */
export function buildLogsSseUrl(
  filters: LogsSseFilters = {},
  base: string = getBaseUrl(),
): string {
  const params = new URLSearchParams();
  if (filters.action) params.set("action", filters.action);
  if (filters.level) params.set("level", filters.level);
  if (filters.q) params.set("q", filters.q);
  if (filters.afterSeq !== undefined && filters.afterSeq > 0) {
    params.set("after_seq", String(filters.afterSeq));
  }
  const qs = params.toString();
  const path = qs ? `api/logs/stream?${qs}` : "api/logs/stream";
  if (!base) return path;
  return `${base}/${path}`;
}

/**
 * Parse an SSE message payload into a typed log line. Returns null
 * when the JSON is malformed or required fields are missing — the
 * hook silently drops malformed lines so a rogue server emission
 * can't poison the buffer.
 */
export function parseLogsSseEvent(data: string): LogsSseLine | null {
  try {
    const obj = JSON.parse(data) as Partial<LogsSseLine>;
    if (
      typeof obj.seq !== "number" ||
      typeof obj.ts !== "string" ||
      typeof obj.msg !== "string"
    ) {
      return null;
    }
    return {
      seq: obj.seq,
      ts: obj.ts,
      msg: obj.msg,
      action: typeof obj.action === "string" ? obj.action : "",
    };
  } catch {
    return null;
  }
}

const DEFAULT_MAX_LINES = 5_000;

export function useLogsSSE(opts: UseLogsSseOptions): LogsSseState {
  const { enabled, maxLines = DEFAULT_MAX_LINES, filters, eventSourceCtor } =
    opts;
  const [lines, setLines] = useState<readonly LogsSseLine[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  // Track the latest filter shape via a ref so the effect identity
  // depends only on the JSON-serialised value (a primitive string),
  // and we can rebuild the URL inside the effect cheaply.
  const filtersKey = JSON.stringify(filters ?? {});

  useEffect(() => {
    if (!enabled) {
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
    const url = buildLogsSseUrl(filters);
    const es = new Ctor(url);
    setIsOpen(true);
    setError(null);

    es.onmessage = (ev: MessageEvent) => {
      const line = parseLogsSseEvent(ev.data);
      if (!line) return;
      setLines((prev) => {
        const next =
          prev.length >= maxLines
            ? [...prev.slice(prev.length - maxLines + 1), line]
            : [...prev, line];
        return next;
      });
    };
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
  }, [enabled, filtersKey, maxLines, eventSourceCtor]);

  return { lines, isOpen, error };
}
