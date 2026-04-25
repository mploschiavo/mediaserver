import { useEffect, useRef } from "react";
import type { LogSource } from "@/api/shapes";
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
 * Render the line stream as a table. We use a real `<table>` (not a
 * div grid) so the browser handles per-column alignment with
 * `tabular-nums`, and screen readers announce the columns.
 *
 * The body is wrapped in a `max-h` scroll container so long streams
 * stay inside the card. The auto-scroll effect targets that container,
 * not `window`, so the page header stays put while the tail advances.
 */
export function LogsTable({ lines, search, tailing }: LogsTableProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const parsed: ParsedSearch = parseSearch(search);

  useEffect(() => {
    if (!tailing) return;
    const el = scrollerRef.current;
    if (!el) return;
    // Run after the DOM mutation flushes — `requestAnimationFrame`
    // is enough; the new rows are committed by then.
    const id = window.requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => window.cancelAnimationFrame(id);
  }, [lines.length, tailing]);

  return (
    <div
      ref={scrollerRef}
      className="max-h-[60vh] overflow-auto"
      data-testid="logs-table-scroller"
      data-tailing={tailing ? "true" : "false"}
    >
      <table className="w-full caption-bottom font-mono text-xs">
        <thead className="sticky top-0 z-10 bg-bg-1 [&_tr]:border-b [&_tr]:border-border">
          <tr>
            <th className="h-8 px-3 text-left align-middle text-[10px] font-medium uppercase tracking-wide text-fg-muted">
              Timestamp
            </th>
            <th className="h-8 px-3 text-left align-middle text-[10px] font-medium uppercase tracking-wide text-fg-muted">
              Source
            </th>
            <th className="h-8 px-3 text-left align-middle text-[10px] font-medium uppercase tracking-wide text-fg-muted">
              Level
            </th>
            <th className="h-8 px-3 text-left align-middle text-[10px] font-medium uppercase tracking-wide text-fg-muted">
              Message
            </th>
          </tr>
        </thead>
        <tbody data-testid="logs-table-body">
          {lines.map((l, i) => {
            const tone = hashSource(l.source);
            const segs = parsed.split(l.message);
            return (
              <tr
                key={`${l.source}:${l.insertion}:${i}`}
                className="border-b border-border/60 align-top even:bg-bg-1/40"
                data-testid="logs-row"
                data-source={l.source}
                data-level={l.level}
              >
                <td className="whitespace-nowrap px-3 py-1.5 tabular-nums text-fg-muted">
                  {l.ts ?? "—"}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5">
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
                </td>
                <td className={cn("whitespace-nowrap px-3 py-1.5", l.levelClassName)}>
                  {l.level}
                </td>
                <td className="px-3 py-1.5 text-fg">
                  <span className="whitespace-pre-wrap break-words">
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
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
