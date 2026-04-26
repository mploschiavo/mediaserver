import { useEffect, useMemo, useRef } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import type { LogSource } from "@/api/shapes";
import { DataTable } from "@/components/data-table";
import { cn } from "@/lib/cn";
import type { ParsedLine } from "./hooks";
import { hashSource, parseSearch, type ParsedSearch } from "./format";
import { ALL_SOURCES } from "./LogsToolbar";

interface LogsTableProps {
  /** Lines to render. The page is responsible for filtering/sorting. */
  lines: readonly ParsedLine[];
  /** Search term used for inline highlight. Re-parsed locally. */
  search: string;
  /**
   * When true, the table auto-scrolls to the bottom whenever the
   * line count changes. When false the operator can scroll back to
   * read history without the page yanking back to the tail.
   */
  tailing: boolean;
}

const SOURCE_LABELS: Record<LogSource, string> = (() => {
  const out = {} as Record<LogSource, string>;
  for (const s of ALL_SOURCES) out[s.value] = s.label;
  return out;
})();

/**
 * Render the line stream as a `<DataTable>`. We wrap the primitive in
 * a sized `max-h-[60vh] overflow-auto` scroll container — that
 * wrapper is the actual scroll viewport (DataTable's own internal
 * `overflow-auto` div has no height cap and never triggers its own
 * scrollbar). The `tailing` auto-scroll effect drives `scrollTop` on
 * the wrapper so the page header stays put while the tail advances.
 *
 * Three semantics are preserved from the legacy raw HTML table:
 *   1. Tail-mode auto-scroll — `useEffect` watching `lines.length`.
 *   2. Search-mark highlighting — message cell maps `parsed.split(...)`
 *      to alternating text + `<mark data-testid="logs-search-hit">`.
 *   3. Per-row data attrs (`data-source`, `data-level`, `data-tone`)
 *      via DataTable's `renderRowAttributes` prop, which existing CSS
 *      and tests depend on.
 *
 * Sorting / filtering / column-visibility are deliberately disabled on
 * the underlying `<DataTable>`. The page already orchestrates filter
 * state via `LogsToolbar` and the controller emits lines in
 * insertion-order — re-sorting client-side would jumble the live tail.
 */
export function LogsTable({ lines, search, tailing }: LogsTableProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const parsed: ParsedSearch = parseSearch(search);

  useEffect(() => {
    if (!tailing) return;
    const el = scrollerRef.current;
    if (!el) return;
    // The wrapper itself owns `max-h-[60vh] overflow-auto` so it's the
    // actual scroll viewport. DataTable's internal `overflow-auto` div
    // has no height cap and never triggers its own scrollbar, so all
    // scroll happens here.
    //
    // Run after the DOM mutation flushes — `requestAnimationFrame`
    // is enough; the new rows are committed by then.
    const id = window.requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => window.cancelAnimationFrame(id);
  }, [lines.length, tailing]);

  const columns = useMemo<ColumnDef<ParsedLine>[]>(
    () => [
      {
        id: "timestamp",
        header: "Timestamp",
        accessorFn: (l) => l.sortKey,
        sortingFn: "basic",
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="whitespace-nowrap tabular-nums text-fg-muted">
            {row.original.ts ?? "—"}
          </span>
        ),
      },
      {
        id: "source",
        header: "Source",
        accessorFn: (l) => l.source,
        enableColumnFilter: false,
        cell: ({ row }) => {
          const l = row.original;
          const tone = hashSource(l.source);
          return (
            <span
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-bg-2 px-2 py-0.5 text-[10px] font-medium"
              data-testid={`logs-source-cell-${l.source}`}
              data-tone={tone.fg}
              style={{ color: tone.fg }}
            >
              <span
                aria-hidden
                className="inline-block size-1.5 rounded-full"
                style={{ backgroundColor: tone.fg }}
              />
              {SOURCE_LABELS[l.source] ?? l.source}
            </span>
          );
        },
      },
      {
        id: "level",
        header: "Level",
        accessorFn: (l) => l.level,
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span
            className={cn(
              "whitespace-nowrap font-mono",
              row.original.levelClassName,
            )}
          >
            {row.original.level}
          </span>
        ),
      },
      {
        id: "message",
        header: "Message",
        accessorFn: (l) => l.message,
        enableSorting: false,
        enableColumnFilter: false,
        cell: ({ row }) => {
          const segs = parsed.split(row.original.message);
          return (
            <span className="whitespace-pre-wrap break-words font-mono text-xs text-fg">
              {segs.map((seg, j) =>
                seg.match ? (
                  <mark
                    key={j}
                    className="rounded-sm bg-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] px-0.5 text-fg"
                    data-testid="logs-search-hit"
                  >
                    {seg.text}
                  </mark>
                ) : (
                  <span key={j}>{seg.text}</span>
                ),
              )}
            </span>
          );
        },
      },
    ],
    // `parsed` is a fresh object each render, but only its `split` closure
    // depends on `search`. Keying on `search` keeps the columns stable
    // across renders that don't change the highlight needle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [search],
  );

  return (
    <div
      ref={scrollerRef}
      className="max-h-[60vh] overflow-auto"
      data-testid="logs-table-scroller"
      data-tailing={tailing ? "true" : "false"}
    >
      <DataTable<ParsedLine>
        data={lines}
        columns={columns}
        testId="logs-data-table"
        getRowId={(l, i) => `${l.source}:${l.insertion}:${i}`}
        enableColumnVisibility={false}
        enableFiltering={false}
        enableSorting={false}
        renderRowAttributes={(row) => ({
          "data-source": row.source,
          "data-level": row.level,
          "data-tone": hashSource(row.source).fg,
        })}
      />
    </div>
  );
}
