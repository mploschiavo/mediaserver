import { useEffect, useRef, useState } from "react";
import { Download, Pause, Play, Search } from "lucide-react";
import type { LogSource } from "@/api/shapes";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { LEVELS, type LevelTag } from "./hooks";
import { hashSource } from "./format";

/** All supported services, in display order. */
export const ALL_SOURCES: readonly { value: LogSource; label: string }[] = [
  { value: "controller", label: "Controller" },
  { value: "sonarr", label: "Sonarr" },
  { value: "radarr", label: "Radarr" },
  { value: "lidarr", label: "Lidarr" },
  { value: "readarr", label: "Readarr" },
  { value: "bazarr", label: "Bazarr" },
  { value: "prowlarr", label: "Prowlarr" },
  { value: "qbittorrent", label: "qBittorrent" },
];

interface LogsToolbarProps {
  sources: readonly LogSource[];
  onSourcesChange: (next: readonly LogSource[]) => void;
  tailing: boolean;
  onTailingChange: (next: boolean) => void;
  search: string;
  onSearchChange: (next: string) => void;
  enabledLevels: ReadonlySet<LevelTag>;
  onToggleLevel: (level: LevelTag) => void;
  onExport: () => void;
  /** Disable export when there's nothing visible to export. */
  exportDisabled?: boolean;
}

const LEVEL_VARIANT: Record<LevelTag, "default" | "danger" | "warning" | "info" | "success"> = {
  "[ERR]": "danger",
  "[WARN]": "warning",
  "[INFO]": "info",
  "[DBG]": "default",
  "[LOG]": "default",
};

/**
 * Single-row toolbar for the Logs page. Lays out:
 *   sources multi-select | tail/pause | level chips | search | export
 *
 * The multi-select is a "click to toggle" chip row rather than a
 * Radix dropdown — tested all eight services flat fits comfortably
 * on a >=`sm:` viewport, and chips give the operator a visible
 * legend of which are active without opening a menu.
 */
export function LogsToolbar({
  sources,
  onSourcesChange,
  tailing,
  onTailingChange,
  search,
  onSearchChange,
  enabledLevels,
  onToggleLevel,
  onExport,
  exportDisabled,
}: LogsToolbarProps) {
  // Local state so each keystroke renders without rebuilding the
  // parent on every char. The parent only sees the debounced value
  // through `onSearchChange`, but we render `localSearch` so the
  // input feels instant even with a slow URL writer upstream.
  const [localSearch, setLocalSearch] = useState(search);
  const lastReceivedRef = useRef(search);
  useEffect(() => {
    if (search !== lastReceivedRef.current) {
      lastReceivedRef.current = search;
      setLocalSearch(search);
    }
  }, [search]);

  const selected = new Set(sources);
  const toggleSource = (s: LogSource) => {
    const next = new Set(selected);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    // Preserve the canonical SOURCES order so the chips don't jitter
    // when the operator clicks them out of sequence.
    onSourcesChange(ALL_SOURCES.map((x) => x.value).filter((v) => next.has(v)));
  };

  return (
    <div
      className="flex flex-col gap-3 border-b border-border p-4 sm:p-6"
      data-testid="logs-toolbar"
    >
      <div className="flex flex-wrap items-center gap-2" data-testid="logs-source-chips">
        <span className="mr-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
          Sources
        </span>
        {ALL_SOURCES.map((s) => {
          const active = selected.has(s.value);
          const tone = hashSource(s.value);
          return (
            <button
              key={s.value}
              type="button"
              role="checkbox"
              aria-checked={active}
              data-testid={`logs-source-chip-${s.value}`}
              onClick={() => toggleSource(s.value)}
              className={cn(
                // 44px touch target floor, dense from sm+.
                "inline-flex h-11 sm:h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg",
                active
                  ? "border-border-strong bg-bg-2 text-fg"
                  : "border-border bg-transparent text-fg-muted [@media(hover:hover)]:hover:bg-bg-2",
              )}
            >
              <span
                aria-hidden
                className="inline-block size-2 rounded-full"
                style={{ backgroundColor: tone.fg }}
              />
              {s.label}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant={tailing ? "primary" : "secondary"}
          size="sm"
          onClick={() => onTailingChange(!tailing)}
          data-testid="logs-tail-toggle"
          aria-pressed={tailing}
        >
          {tailing ? (
            <>
              <Pause className="size-3.5" aria-hidden /> Pause
            </>
          ) : (
            <>
              <Play className="size-3.5" aria-hidden /> Tail
            </>
          )}
        </Button>

        <div className="flex items-center gap-1" data-testid="logs-level-filter">
          {LEVELS.map((lvl) => {
            const active = enabledLevels.has(lvl);
            return (
              <button
                key={lvl}
                type="button"
                role="checkbox"
                aria-checked={active}
                data-testid={`logs-level-chip-${lvl.replaceAll(/[[\]]/g, "")}`}
                onClick={() => onToggleLevel(lvl)}
                className={cn(
                  "inline-flex h-11 sm:h-7 items-center rounded-md border px-2 font-mono text-[10px] tracking-wider transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg",
                  active
                    ? "border-border-strong bg-bg-2 text-fg"
                    : "border-border bg-transparent text-fg-faint line-through",
                )}
              >
                <Badge
                  variant={active ? LEVEL_VARIANT[lvl] : "outline"}
                  className="rounded-sm border-0 px-1 py-0 font-mono text-[10px] tracking-wider"
                >
                  {lvl}
                </Badge>
              </button>
            );
          })}
        </div>

        <div className="relative ml-auto min-w-0 flex-1 sm:max-w-xs">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-fg-faint"
          />
          <Input
            type="search"
            value={localSearch}
            onChange={(e) => {
              setLocalSearch(e.target.value);
              onSearchChange(e.target.value);
            }}
            placeholder="Search… (use /regex/i for regex)"
            aria-label="Search log lines"
            data-testid="logs-search"
            className="pl-8"
          />
        </div>

        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onExport}
          disabled={exportDisabled}
          data-testid="logs-export"
        >
          <Download className="size-3.5" aria-hidden /> Export
        </Button>
      </div>
    </div>
  );
}
