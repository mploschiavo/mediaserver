import { useMemo } from "react";
import { PieChart } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  useStorageBreakdown,
  type StorageBreakdownLibrary,
  type StorageBreakdownResponse,
  type StorageBreakdownItem,
} from "./hooks";

const ROW_HEIGHT = 22;
const BAR_HEIGHT = 10;
const BAR_INNER_W = 220;
const LABEL_W = 64;
const VALUE_W = 70;
const ROW_PAD_Y = 6;
const SVG_W = LABEL_W + BAR_INNER_W + VALUE_W;

const LEGACY_KEYED: readonly {
  key: "movies" | "tv" | "tracks" | "books";
  label: string;
}[] = [
  { key: "movies", label: "Movies" },
  { key: "tv", label: "TV" },
  { key: "tracks", label: "Tracks" },
  { key: "books", label: "Books" },
];

const PRETTY_NAME: Record<string, string> = {
  movies: "Movies",
  movie: "Movies",
  tv: "TV",
  "tv shows": "TV",
  shows: "TV",
  music: "Music",
  tracks: "Music",
  audiobooks: "Audiobooks",
  audiobook: "Audiobooks",
  books: "Books",
  anime: "Anime",
  podcasts: "Podcasts",
};

function prettyLabel(name: string): string {
  const k = name.trim().toLowerCase();
  return PRETTY_NAME[k] ?? name;
}

function slug(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "lib";
}

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const i = Math.min(
    Math.floor(Math.log(n) / Math.log(1024)),
    UNITS.length - 1,
  );
  const v = n / 1024 ** i;
  const formatted = v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : Math.round(v);
  return `${formatted} ${UNITS[i]}`;
}

function readBytes(lib: StorageBreakdownLibrary | undefined): number {
  if (!lib) return 0;
  if (typeof lib.bytes === "number" && Number.isFinite(lib.bytes)) {
    return lib.bytes;
  }
  // Some controller builds emit `by_kind` only.
  const bk = lib.by_kind;
  if (bk && typeof bk === "object") {
    let sum = 0;
    for (const v of Object.values(bk)) {
      if (typeof v === "number" && Number.isFinite(v)) sum += v;
    }
    return sum;
  }
  return 0;
}

interface BarRow {
  id: string;
  label: string;
  bytes: number;
}

function readItemBytes(item: StorageBreakdownItem | undefined): number {
  if (!item) return 0;
  if (typeof item.bytes === "number" && Number.isFinite(item.bytes)) {
    return item.bytes;
  }
  return 0;
}

function buildRows(data: StorageBreakdownResponse | undefined): BarRow[] {
  if (!data) return [];

  // Live controller shape (`/api/storage-breakdown` from disk.py):
  // a `breakdown[]` array of `{name, path, bytes, display}` keyed
  // off the actual MEDIA_ROOT subdirectories. Prefer this when
  // present — it reflects ground truth, not the v1.3.0 spec's
  // hard-coded movies/tv/tracks/books enum.
  if (Array.isArray(data.breakdown)) {
    const rows: BarRow[] = [];
    const seen = new Set<string>();
    for (const item of data.breakdown) {
      const name = (item.name ?? "").trim();
      if (!name) continue;
      const id = slug(name);
      // Tolerate duplicate basenames inside MEDIA_ROOT.
      let dedupId = id;
      let n = 2;
      while (seen.has(dedupId)) dedupId = `${id}-${n++}`;
      seen.add(dedupId);
      rows.push({ id: dedupId, label: prettyLabel(name), bytes: readItemBytes(item) });
    }
    return rows.filter((r) => r.bytes > 0);
  }

  // Legacy v1.3.0 keyed shape — kept so older controllers and
  // existing test fixtures continue to render.
  return LEGACY_KEYED.map((k) => {
    const lib = data[k.key] as StorageBreakdownLibrary | undefined;
    return { id: String(k.key), label: k.label, bytes: readBytes(lib) };
  }).filter((r) => r.bytes > 0);
}

export function StorageBreakdownCard() {
  const query = useStorageBreakdown();
  const rows = useMemo(() => buildRows(query.data), [query.data]);
  const max = useMemo(
    () => rows.reduce((m, r) => (r.bytes > m ? r.bytes : m), 0),
    [rows],
  );
  const svgHeight = rows.length * (ROW_HEIGHT + ROW_PAD_Y) + ROW_PAD_Y;

  return (
    <Card data-testid="storage-breakdown-card">
      <CardHeader>
        <CardTitle>Storage breakdown</CardTitle>
        <CardDescription>
          Bytes per library across the configured roots
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div
            className="flex flex-col gap-2"
            data-testid="storage-breakdown-loading"
          >
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-5 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="storage-breakdown-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={PieChart}
            title="No usage data"
            description="The controller hasn't reported any per-library bytes yet."
          />
        ) : (
          <svg
            width="100%"
            height={svgHeight}
            viewBox={`0 0 ${SVG_W} ${svgHeight}`}
            role="img"
            aria-label="Storage breakdown bar chart"
            data-testid="storage-breakdown-svg"
            preserveAspectRatio="xMinYMid meet"
          >
            {rows.map((row, i) => {
              const ratio = max > 0 ? row.bytes / max : 0;
              const barW = Math.max(1, ratio * BAR_INNER_W);
              const y = ROW_PAD_Y + i * (ROW_HEIGHT + ROW_PAD_Y);
              const labelY = y + ROW_HEIGHT / 2 + 4;
              const barY = y + (ROW_HEIGHT - BAR_HEIGHT) / 2;
              return (
                <g key={row.id} data-testid={`storage-row-${row.id}`}>
                  <text
                    x={0}
                    y={labelY}
                    fontSize={11}
                    fill="var(--color-fg)"
                    fontFamily="ui-sans-serif, system-ui, sans-serif"
                  >
                    {row.label}
                  </text>
                  <rect
                    x={LABEL_W}
                    y={barY}
                    width={BAR_INNER_W}
                    height={BAR_HEIGHT}
                    rx={2}
                    fill="color-mix(in oklab, var(--color-fg) 8%, transparent)"
                  />
                  <rect
                    x={LABEL_W}
                    y={barY}
                    width={barW}
                    height={BAR_HEIGHT}
                    rx={2}
                    fill="var(--color-info)"
                    data-testid={`storage-bar-${row.id}`}
                  />
                  <text
                    x={LABEL_W + BAR_INNER_W + 8}
                    y={labelY}
                    fontSize={11}
                    fill="var(--color-fg-muted, var(--color-fg))"
                    fontFamily="ui-monospace, SFMono-Regular, monospace"
                  >
                    {formatBytes(row.bytes)}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
      </CardContent>
    </Card>
  );
}
