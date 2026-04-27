import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { useNavigate } from "@tanstack/react-router";
import { ScrollText, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import type { LogSource } from "@/api/shapes";
import { LogsToolbar, ALL_SOURCES } from "./LogsToolbar";
import { LogsTable } from "./LogsTable";
import {
  parseLogLine,
  useMultiLogs,
  LEVELS,
  type LevelTag,
  type ParsedLine,
} from "./hooks";
import { parseSearch } from "./format";

const STORAGE_KEY = "media-stack:logs-sources";
const URL_DEBOUNCE_MS = 300;
const VALID_SOURCES = new Set<LogSource>(ALL_SOURCES.map((s) => s.value));

// Operator-pickable limits. The backend hard cap is 50000 (see
// LOG_LINES_HARD_CAP in src/media_stack/api/services/ops.py); the
// dashboard exposes everything from 100 up to 50k. Each step is
// roughly 5x so the picker stays short. Default raised to 5000 in
// v1.3.65 — 100 was unusable for actual debugging (operators
// flagged it; the previous default predates the controller's
// 50k cap and never got bumped).
export const LIMIT_OPTIONS: ReadonlyArray<number> = [
  100, 500, 1000, 5000, 10000, 50000,
];
const DEFAULT_LIMIT = 5000;

// Time-range presets. ``""`` = no filter (rely on ``lines`` cap
// alone). Anything else is passed through to the backend as
// ``?since=`` and resolved against the docker/k8s log timestamp.
export const SINCE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "All available" },
  { value: "5m", label: "Last 5 min" },
  { value: "30m", label: "Last 30 min" },
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "Last 24h" },
  { value: "7d", label: "Last 7 days" },
];

// How many lines we render in the DOM at once. Bumped from the
// previous 1000 to 5000 — even a slow Chromebook scrolls 5k log
// rows fine, and the operator's whole point is to NOT have to ssh
// into the container.
const MAX_RENDER_LINES = 5000;

/** Read the last-used source set from localStorage. Defaults to controller. */
function loadStoredSources(): readonly LogSource[] {
  if (typeof window === "undefined") return ["controller"];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return ["controller"];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return ["controller"];
    const seen = new Set<LogSource>();
    for (const v of parsed) {
      if (typeof v === "string" && VALID_SOURCES.has(v as LogSource)) {
        seen.add(v as LogSource);
      }
    }
    if (seen.size === 0) return ["controller"];
    return ALL_SOURCES.map((s) => s.value).filter((v) => seen.has(v));
  } catch {
    return ["controller"];
  }
}

/** Read the URL once on mount. */
function readUrlState(): {
  service: LogSource | null;
  filter: string;
  limit: number;
  since: string;
  action: string;
} {
  if (typeof window === "undefined") {
    return {
      service: null, filter: "", limit: DEFAULT_LIMIT, since: "", action: "",
    };
  }
  const params = new URLSearchParams(window.location.search);
  const svc = params.get("service");
  const filter = params.get("filter") ?? "";
  const rawLimit = Number.parseInt(params.get("limit") ?? "", 10);
  const limit =
    Number.isFinite(rawLimit) && LIMIT_OPTIONS.includes(rawLimit)
      ? rawLimit
      : DEFAULT_LIMIT;
  const since = params.get("since") ?? "";
  const action = params.get("action") ?? "";
  return {
    service: svc && VALID_SOURCES.has(svc as LogSource) ? (svc as LogSource) : null,
    filter,
    limit,
    since,
    action,
  };
}

/**
 * Compose the Logs page. Owns:
 *   - source selection (with localStorage persistence + URL hydration)
 *   - tail/pause toggle
 *   - level filter set
 *   - debounced URL write-through for `service`/`filter` query params
 *   - export-current-view download
 *
 * The hooks/components below it are pure presentation; they receive
 * arrays/state through props and emit changes via callbacks.
 */
export function LogsPage() {
  const reduce = useReducedMotion();
  const navigate = useNavigate({ from: "/logs" });
  const initialUrl = useRef(readUrlState());

  const [sources, setSources] = useState<readonly LogSource[]>(() => {
    const fromUrl = initialUrl.current.service;
    if (fromUrl) return [fromUrl];
    return loadStoredSources();
  });
  const [tailing, setTailing] = useState(true);
  const [search, setSearch] = useState(initialUrl.current.filter);
  const [limit, setLimit] = useState<number>(initialUrl.current.limit);
  const [since, setSince] = useState<string>(initialUrl.current.since);
  const [actionFilter, setActionFilter] = useState<string>(
    initialUrl.current.action,
  );
  const [enabledLevels, setEnabledLevels] = useState<ReadonlySet<LevelTag>>(
    () => new Set(LEVELS),
  );

  // Persist source selection so a returning operator gets back what
  // they had open. Skip the empty case so we don't clobber the
  // default by accident on first render.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (sources.length === 0) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sources));
    } catch {
      // Storage quota / privacy mode — silently fall back to default.
    }
  }, [sources]);

  // Debounced URL write-through so a wild typer doesn't trash the
  // browser history. Tracking `service` (single, first) and `filter`.
  //
  // Routed through Tanstack Router's `navigate` (not raw
  // `history.replaceState`) so the basepath is honored. Writing the
  // URL directly with `window.location.pathname` re-pushed the full
  // deployed prefix (`/app/media-stack-ui/logs`) back through the
  // router, which would re-evaluate against its bare-path route table
  // and fall to the splat 404 ("Lost your way?") about 300ms after
  // the page mounted. See `bug_class_history_replacestate_basepath`.
  useEffect(() => {
    const id = window.setTimeout(() => {
      void navigate({
        to: "/logs",
        replace: true,
        search: (prev) => ({
          ...prev,
          service: sources[0],
          filter: search || undefined,
          limit: limit !== DEFAULT_LIMIT ? limit : undefined,
          since: since || undefined,
          action: actionFilter || undefined,
        }),
      });
    }, URL_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [sources, search, limit, since, actionFilter, navigate]);

  const stream = useMultiLogs(sources, {
    tailing,
    filters: {
      lines: limit,
      ...(since && { since }),
      ...(actionFilter && { action: actionFilter }),
    },
  });

  // Parse + flatten + sort + filter. Memoised; otherwise the table
  // re-derives on every keystroke even when the streams are idle.
  const allLines = useMemo<ParsedLine[]>(() => {
    const out: ParsedLine[] = [];
    for (const bucket of stream.data) {
      let i = 0;
      for (const raw of bucket.lines) {
        out.push(parseLogLine(raw, bucket.source, i++));
      }
    }
    out.sort((a, b) => {
      if (a.sortKey !== b.sortKey) return a.sortKey - b.sortKey;
      return a.insertion - b.insertion;
    });
    return out;
  }, [stream.data]);

  const parsed = useMemo(() => parseSearch(search), [search]);

  const visibleLines = useMemo<ParsedLine[]>(() => {
    if (allLines.length === 0) return allLines;
    const filtered = allLines.filter(
      (l) => enabledLevels.has(l.level) && parsed.test(l.message),
    );
    // Cap render — extremely long buffers (>1000 lines) tank scroll
    // perf with no upside; the operator's looking at the tail anyway.
    return filtered.length > MAX_RENDER_LINES
      ? filtered.slice(-MAX_RENDER_LINES)
      : filtered;
  }, [allLines, enabledLevels, parsed]);

  const toggleLevel = useCallback((lvl: LevelTag) => {
    setEnabledLevels((prev) => {
      const next = new Set(prev);
      if (next.has(lvl)) next.delete(lvl);
      else next.add(lvl);
      return next;
    });
  }, []);

  const handleExport = useCallback(() => {
    if (typeof window === "undefined") return;
    const text = visibleLines
      .map((l) => `[${l.ts ?? ""}] [${l.source}] ${l.level} ${l.message}`)
      .join("\n");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    a.href = url;
    a.download = `logs-${stamp}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Defer revoke so Chromium has time to start the download.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [visibleLines]);

  const payloadErrors = stream.data.filter((b) => b.error);

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      data-testid="logs-page"
    >
      <PageHeader
        title="Logs"
        description="Unified log stream across services. Tail multiple sources, filter by level, search inline."
      />

      <Card data-testid="logs-card">
        <LogsToolbar
          sources={sources}
          onSourcesChange={setSources}
          tailing={tailing}
          onTailingChange={setTailing}
          search={search}
          onSearchChange={setSearch}
          enabledLevels={enabledLevels}
          onToggleLevel={toggleLevel}
          onExport={handleExport}
          exportDisabled={visibleLines.length === 0}
          limit={limit}
          onLimitChange={setLimit}
          limitOptions={LIMIT_OPTIONS}
          since={since}
          onSinceChange={setSince}
          sinceOptions={SINCE_OPTIONS}
          actionFilter={actionFilter}
          onActionFilterChange={setActionFilter}
        />

        <div
          className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2 text-xs text-fg-muted sm:px-6"
          data-testid="logs-stats"
        >
          <span data-testid="logs-stat-visible">
            <span className="tabular-nums text-fg">{visibleLines.length}</span>{" "}
            visible
          </span>
          <span aria-hidden className="text-fg-faint">
            ·
          </span>
          <span data-testid="logs-stat-total">
            <span className="tabular-nums text-fg">{allLines.length}</span> total
          </span>
          {allLines.length >= limit && limit < LIMIT_OPTIONS[LIMIT_OPTIONS.length - 1]! ? (
            <Badge
              variant="warning"
              data-testid="logs-cap-hint"
              title={`Showing the most recent ${limit} lines per source. Raise the limit (toolbar above) to load more.`}
            >
              at limit · raise to see more
            </Badge>
          ) : null}
          {tailing ? (
            <Badge variant="success" data-testid="logs-tailing-badge">
              Tailing
            </Badge>
          ) : (
            <Badge variant="outline" data-testid="logs-paused-badge">
              Paused
            </Badge>
          )}
          {payloadErrors.length > 0 ? (
            <span
              role="alert"
              className="ml-auto text-warning"
              data-testid="logs-payload-error"
            >
              {payloadErrors.map((b) => `${b.source}: ${b.error}`).join(" • ")}
            </span>
          ) : null}
        </div>

        {search ? (
          <div
            className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 text-xs sm:px-6"
            data-testid="logs-filter-chip"
          >
            <span className="text-fg-muted">Active filter:</span>
            <span className="inline-flex items-center gap-1 rounded-md border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-accent">
              {search}
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear filter"
                className="rounded text-accent/80 [@media(hover:hover)]:hover:text-accent"
                data-testid="logs-filter-chip-clear"
              >
                <X className="size-3" aria-hidden />
              </button>
            </span>
          </div>
        ) : null}

        <CardContent className="p-0 pt-0">
          {sources.length === 0 ? (
            <div data-testid="logs-empty-no-sources" className="p-4 sm:p-6">
              <EmptyState
                icon={ScrollText}
                title="No sources selected"
                description="Pick at least one source above to start tailing."
              />
            </div>
          ) : stream.isLoading && allLines.length === 0 ? (
            <div className="flex flex-col gap-2 p-4 sm:p-6" data-testid="logs-loading">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-3 w-full" />
              ))}
            </div>
          ) : stream.error && allLines.length === 0 ? (
            <div
              role="alert"
              data-testid="logs-error"
              className="m-4 rounded-md border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-3 text-sm text-danger sm:m-6"
            >
              <p className="font-medium">Failed to load logs</p>
              <p className="mt-1 text-fg-muted">{stream.error.message}</p>
            </div>
          ) : visibleLines.length === 0 ? (
            <div data-testid="logs-empty" className="p-4 sm:p-6">
              <EmptyState
                icon={ScrollText}
                title={allLines.length === 0 ? "No log lines" : "No matches"}
                description={
                  allLines.length === 0
                    ? "The buffer is empty — try toggling tail back on."
                    : "Adjust the search or level filter to see lines."
                }
              />
            </div>
          ) : (
            <LogsTable
              lines={visibleLines}
              search={search}
              tailing={tailing}
            />
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}
