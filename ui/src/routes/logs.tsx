// /logs route — thin shell over the LogsPage feature component.
//
// Earlier builds inlined a single-source poller directly in this file;
// the rebuilt UI lives in `src/features/logs/LogsPage.tsx`. We keep
// the route module slim — registration + search-param contract — and
// re-export the legacy `formatLogLine` helper so any consumer that
// imported it from here keeps compiling.

import { createRoute } from "@tanstack/react-router";
import type { LogLineShape, LogSource } from "@/api";
import { LogsPage } from "@/features/logs/LogsPage";
import { ALL_SOURCES } from "@/features/logs/LogsToolbar";
import { Route as RootRoute } from "@/routes/__root";

const VALID_SOURCES: ReadonlySet<string> = new Set(
  ALL_SOURCES.map((s) => s.value),
);

/**
 * Search-param shape for the /logs route. Both fields are optional —
 * the route renders fine without any query string. The shape is
 * deliberately minimal so it can be appended to via deep-links from
 * sibling features (e.g. `/jobs` "View logs" button).
 *
 * Note: a sibling agent's JobDetailPanel deep-links via `?filter=...`;
 * the page reads that param on mount and writes through to the URL on
 * change. The route validator below normalises the inbound shape.
 */
export interface LogsSearch {
  service?: LogSource;
  filter?: string;
}

/**
 * Render a single log line with a level tag the eye can scan. Accepts
 * either the structured `LogLineShape` or a raw string (which is what
 * the controller's `/api/logs/{service}` actually returns — see
 * `contracts/api/openapi.yaml`). Strings are scanned for an
 * ERROR/WARN/INFO/DEBUG token to derive the level + colour.
 *
 * Re-exported here so existing imports (`from "@/routes/logs"`) keep
 * working; the new feature surface uses `parseLogLine` in `hooks.ts`
 * which returns richer per-line data.
 */
export function formatLogLine(line: LogLineShape | string): {
  className: string;
  prefix: string;
  text: string;
} {
  if (typeof line === "string") {
    return formatRawLogLine(line);
  }
  const ts = line.ts ? `[${line.ts}] ` : "";
  if (line.level === "error") {
    return { className: "text-danger", prefix: "[ERR]", text: `${ts}${line.message}` };
  }
  if (line.level === "warn") {
    return { className: "text-warning", prefix: "[WARN]", text: `${ts}${line.message}` };
  }
  if (line.level === "info") {
    return { className: "text-fg-muted", prefix: "[INFO]", text: `${ts}${line.message}` };
  }
  return { className: "text-fg-faint", prefix: "[DBG]", text: `${ts}${line.message}` };
}

function formatRawLogLine(raw: string): {
  className: string;
  prefix: string;
  text: string;
} {
  const upper = raw.toUpperCase();
  if (/\b(ERROR|ERR|FATAL|CRIT|CRITICAL)\b/.test(upper)) {
    return { className: "text-danger", prefix: "[ERR]", text: raw };
  }
  if (/\b(WARN|WARNING)\b/.test(upper)) {
    return { className: "text-warning", prefix: "[WARN]", text: raw };
  }
  if (/\b(INFO|NOTICE)\b/.test(upper)) {
    return { className: "text-fg-muted", prefix: "[INFO]", text: raw };
  }
  if (/\b(DEBUG|DBG|TRACE)\b/.test(upper)) {
    return { className: "text-fg-faint", prefix: "[DBG]", text: raw };
  }
  return { className: "text-fg-muted", prefix: "[LOG]", text: raw };
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/logs",
  component: LogsPage,
  validateSearch: (raw: Record<string, unknown>): LogsSearch => {
    const out: LogsSearch = {};
    const svc = raw.service;
    if (typeof svc === "string" && VALID_SOURCES.has(svc)) {
      out.service = svc as LogSource;
    }
    const filter = raw.filter;
    if (typeof filter === "string" && filter.length > 0) {
      out.filter = filter;
    }
    return out;
  },
});
