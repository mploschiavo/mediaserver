// Feature-local hooks for the Logs operator surface.
//
// The shared `useLogs(source)` hook in `src/api/hooks.ts` polls a
// SINGLE service endpoint. The new dashboard wants to stream
// several at once — controller + sonarr + radarr — so the operator
// can correlate a Servarr error with whatever the controller logged
// at the same instant.
//
// We layer on top of `@tanstack/react-query`'s `useQueries` and a
// thin parser so the table can sort by timestamp, color the source
// column, and filter by level without re-deriving the same data
// per render.
//
// Polling cadence is 3000ms when the operator has tail mode ON.
// When OFF we set `refetchInterval: false` so the queries idle —
// React Query keeps the last cached payload, which is exactly what
// "pause and read history" wants.

import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import { fetcher } from "@/api/client";
import type { LogLineShape, LogSource, LogStreamShape } from "@/api/shapes";
import { asArray } from "@/lib/coerce";
import { extractTimestamp } from "./format";

/**
 * One entry in the Logs UI's filter dropdown. Returned by
 * `GET /api/logs/sources` — driven by the controller's SERVICES
 * registry plus platform pods (controller, ui).
 */
export interface LogSourceOption {
  id: string;
  label: string;
  kind: "platform" | "service";
}

const _LOG_SOURCES_KEY = ["logs", "sources"] as const;

/**
 * Fetches the dynamic list of log sources. Replaces the hardcoded
 * 8-source list operators were stuck with — every service in the
 * registry plus the controller / ui pods. Cached for 5 minutes; the
 * registry is static during a controller's lifetime.
 */
export function useLogSources(): UseQueryResult<readonly LogSourceOption[]> {
  return useQuery({
    queryKey: _LOG_SOURCES_KEY,
    queryFn: async () => {
      const data = await fetcher<{ sources?: readonly LogSourceOption[] }>(
        "api/logs/sources",
      );
      return data.sources ?? [];
    },
    staleTime: 300_000,
    retry: 1,
  });
}

/** Result of parsing one raw log row into something the table can render. */
export interface ParsedLine {
  /** Source service the line came from. */
  source: LogSource;
  /** Extracted timestamp (string, as printed) or null when not present. */
  ts: string | null;
  /** Parsed level token in the bracketed form the table renders. */
  level: "[ERR]" | "[WARN]" | "[INFO]" | "[DBG]" | "[LOG]";
  /** Tailwind color class for the level chip. */
  levelClassName: string;
  /** Message body with the timestamp prefix stripped (if any). */
  message: string;
  /** Original raw line — useful for "export current view" downloads. */
  raw: string;
  /**
   * Numeric sort key. Date.parse() of the extracted timestamp when
   * present; falls back to insertion order so we don't shuffle
   * untimestamped lines around.
   */
  sortKey: number;
  /** Stable per-source insertion index so equal-ts lines stay grouped. */
  insertion: number;
}

/** All five level tags emitted by the parser, in display order. */
export const LEVELS = ["[ERR]", "[WARN]", "[INFO]", "[DBG]", "[LOG]"] as const;
export type LevelTag = (typeof LEVELS)[number];

const LEVEL_CLASSES: Record<LevelTag, string> = {
  "[ERR]": "text-danger",
  "[WARN]": "text-warning",
  "[INFO]": "text-fg-muted",
  "[DBG]": "text-fg-faint",
  "[LOG]": "text-fg-muted",
};

// Bracketed-token vocabulary, case-insensitive, anchored to the start
// of the message. Mapping a token to a `LevelTag` is the single
// source-of-truth for the parser. Adding a new token here keeps the
// per-row tone routing automatic.
const BRACKET_TOKEN_TO_LEVEL: Record<string, LevelTag> = {
  ERROR: "[ERR]",
  ERR: "[ERR]",
  FATAL: "[ERR]",
  CRIT: "[ERR]",
  CRITICAL: "[ERR]",
  WARN: "[WARN]",
  WARNING: "[WARN]",
  INFO: "[INFO]",
  NOTICE: "[INFO]",
  OK: "[INFO]",
  JOB: "[INFO]",
  ACTION: "[INFO]",
  DEBUG: "[DBG]",
  DBG: "[DBG]",
  TRACE: "[DBG]",
};

/**
 * Extract a level tag from the leading bracketed token of a
 * (timestamp-stripped) log line. Free-text matches inside the
 * message body are NOT considered — see the comment in
 * `parseLogLine` for the bug class this avoids.
 *
 * Examples:
 *   "[ERROR] kaboom"          -> [ERR]
 *   "[OK] reconcile complete" -> [INFO]
 *   "retrying after err=..."  -> [LOG]   (no leading bracket)
 *   "Stack trace: Error: ..." -> [LOG]   (no leading bracket)
 */
export function extractLevelFromBracketedPrefix(message: string): LevelTag {
  // Trim only leading whitespace; the bracket must immediately follow
  // any whitespace at the start of the timestamp-stripped line.
  let i = 0;
  while (i < message.length && (message[i] === " " || message[i] === "\t")) {
    i++;
  }
  if (message[i] !== "[") return "[LOG]";
  const close = message.indexOf("]", i + 1);
  if (close < 0) return "[LOG]";
  const token = message.slice(i + 1, close).trim().toUpperCase();
  if (!token) return "[LOG]";
  return BRACKET_TOKEN_TO_LEVEL[token] ?? "[LOG]";
}

/**
 * Parse one raw or structured log line into a `ParsedLine`. The
 * controller emits arrays of raw strings today (`fetcher` returns
 * `{lines: string[]}`), but `LogStreamShape.lines` is typed as
 * `(LogLineShape | string)[]` for forward-compat — accept either.
 */
export function parseLogLine(
  line: LogLineShape | string,
  source: LogSource,
  insertion: number,
): ParsedLine {
  if (typeof line !== "string") {
    const tag: LevelTag =
      line.level === "error"
        ? "[ERR]"
        : line.level === "warn"
          ? "[WARN]"
          : line.level === "info"
            ? "[INFO]"
            : "[DBG]";
    const ts = line.ts || null;
    return {
      source,
      ts,
      level: tag,
      levelClassName: LEVEL_CLASSES[tag],
      message: line.message,
      raw: ts ? `[${ts}] ${tag} ${line.message}` : `${tag} ${line.message}`,
      sortKey: ts ? Date.parse(ts) || insertion : insertion,
      insertion,
    };
  }
  const { ts, rest } = extractTimestamp(line);
  // Level extraction is anchored to the START of the line (after the
  // timestamp prefix is stripped) and only looks at a BRACKETED token.
  // Free-text "error=" / "Error:" / "Exception" substrings inside the
  // message body are intentionally NOT promoted — retry-loop messages
  // legitimately embed the prior attempt's error string for context,
  // and the previous \b…\b matcher misclassified ~180/184 lines as
  // [ERR] on a typical controller boot, breaking the level filter.
  // Recognised tokens (case-insensitive):
  //   [ERROR] / [ERR] / [FATAL] / [CRIT] / [CRITICAL]   -> [ERR]
  //   [WARN]  / [WARNING]                               -> [WARN]
  //   [INFO]  / [NOTICE] / [OK] / [JOB] / [ACTION]      -> [INFO]
  //   [DEBUG] / [DBG] / [TRACE]                         -> [DBG]
  //   anything else (or no leading bracket)             -> [LOG]
  const tag = extractLevelFromBracketedPrefix(rest);
  // Date.parse on a SQL-ish "2026-04-07 12:00:01" returns NaN in some
  // engines; replace the space with a "T" before falling through.
  const parsed = ts
    ? Date.parse(ts.replace(" ", "T")) || Date.parse(ts) || insertion
    : insertion;
  return {
    source,
    ts,
    level: tag,
    levelClassName: LEVEL_CLASSES[tag],
    message: rest.trimStart(),
    raw: line,
    sortKey: parsed,
    insertion,
  };
}

interface UseMultiLogsResult {
  data: {
    source: LogSource;
    lines: readonly (LogLineShape | string)[];
    error?: string;
  }[];
  isLoading: boolean;
  error: Error | null;
}

/**
 * Aggregate multiple `GET /api/logs/{source}` polls into one shape
 * the table can consume directly.
 *
 *   - Each source gets its own `useQuery` via `useQueries`, so
 *     React Query's cache, retry, and dedupe behave per-source
 *     (one slow `bazarr` doesn't block `controller`).
 *   - `tailing: false` disables refetch entirely — the cached
 *     pages stay visible and the operator can scroll back.
 *   - The shape mirrors the controller payload: `{lines, error}`.
 *     The 200-with-`error` fallback (label-selector mismatch in
 *     K8s, missing container, etc.) propagates up so the page can
 *     surface it inline.
 */
export interface MultiLogsFilters {
  /** Per-source line cap, threaded into the backend query. */
  lines?: number;
  /** Time window: relative shorthand (``5m``/``1h``/``24h``) OR ISO. */
  since?: string;
  /** Filter to lines mentioning ``[ACTION] <name>`` or ``[JOB] <name>``. */
  action?: string;
  /** Backend-side level filter (cheaper than client-side at 50k lines). */
  level?: "error" | "warning" | "info" | "debug";
  /** Free-text or ``/regex/i`` search. */
  q?: string;
  /** K8s only — attach previous container instance's logs (crashloop). */
  previous?: boolean;
}

export function useMultiLogs(
  sources: readonly LogSource[],
  opts: { tailing: boolean; filters?: MultiLogsFilters },
): UseMultiLogsResult {
  const refetchInterval = opts.tailing ? 3000 : (false as const);
  const filters = opts.filters ?? {};
  // `useQueries` infers a tuple per element; coerce to a uniform
  // `UseQueryResult<LogStreamShape>[]` for the map below — every entry
  // is the same shape (`LogStreamShape`) since each query hits the
  // same `api.logs(...)` endpoint.
  const results = useQueries({
    queries: sources.map((s) => ({
      queryKey: [
        "logs",
        s,
        filters.lines ?? 0,
        filters.since ?? "",
        filters.action ?? "",
        filters.level ?? "",
        filters.q ?? "",
        Boolean(filters.previous),
      ] as const,
      queryFn: () =>
        api.logs(s, {
          ...(filters.lines !== undefined && { lines: filters.lines }),
          ...(filters.since && { since: filters.since }),
          ...(filters.action && { action: filters.action }),
          ...(filters.level && { level: filters.level }),
          ...(filters.q && { q: filters.q }),
          ...(filters.previous && { previous: true }),
        }),
      refetchInterval,
      retry: false,
    })),
  }) as unknown as UseQueryResult<LogStreamShape>[];

  // We deliberately recompute every render — `useQueries` returns a
  // new array each time anyway (same reference identity won't hold),
  // and the work is O(sources.length) which is tiny (<= 8). A useMemo
  // here would just add cycles without saving renders.
  const data = sources.map((s, idx) => {
    const r = results[idx];
    const payload = r?.data;
    const errStr =
      payload && typeof (payload as { error?: unknown }).error === "string"
        ? (payload as { error?: string }).error
        : undefined;
    const out: {
      source: LogSource;
      lines: readonly (LogLineShape | string)[];
      error?: string;
    } = {
      source: s,
      lines: asArray<LogLineShape | string>(payload?.lines),
    };
    if (errStr !== undefined) out.error = errStr;
    return out;
  });
  const isLoading = results.some((r) => r?.isLoading);
  const error = (results.find((r) => r?.error)?.error ?? null) as Error | null;
  return { data, isLoading, error };
}
