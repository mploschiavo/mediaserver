import { useMemo } from "react";
import {
  BookOpen,
  Disc3,
  Film,
  Tv,
  type LucideIcon,
} from "lucide-react";
import { asArray } from "@/lib/coerce";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useLibraries,
  type ConfiguredLibraryEntry,
  type LiveLibraryEntry,
} from "./hooks";

/**
 * Map a Jellyfin `collection_type` value onto the dashboard's tile
 * key. The 4 tiles correspond to the canonical library kinds the
 * controller seeds by default (movies / tvshows / music / books).
 * Anything else is grouped into the closest match or ignored — we
 * don't surface a 5th tile for `mixed` / `homevideos` etc.
 */
type TileKey = "movies" | "tv" | "tracks" | "books";

const COLLECTION_TO_TILE: Record<string, TileKey> = {
  movies: "movies",
  tvshows: "tv",
  music: "tracks",
  books: "books",
};

const STAT_ICONS: Record<TileKey, LucideIcon> = {
  movies: Film,
  tv: Tv,
  tracks: Disc3,
  books: BookOpen,
};

const STAT_LABELS: Record<TileKey, string> = {
  movies: "Movies",
  tv: "TV",
  tracks: "Tracks",
  books: "Books",
};

interface StatCardProps {
  label: string;
  value: number;
  icon: LucideIcon;
}

function StatCard({ label, value, icon: Icon }: StatCardProps) {
  return (
    <Card data-testid={`library-stat-${label.toLowerCase()}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-fg-muted">
          {label}
        </CardTitle>
        <Icon className="size-4 text-fg-muted" aria-hidden />
      </CardHeader>
      <CardContent>
        <div className="font-mono text-3xl font-semibold tabular-nums text-fg">
          {value.toLocaleString()}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * 4-tile movies/tv/tracks/books overview. Reads from the tightened
 * `/api/libraries` shape: tile counts come from `live[].item_count`
 * when Jellyfin is reachable, falling back to the count of configured
 * libraries per `collection_type` otherwise. The fallback ensures the
 * tiles stay informative on a fresh stack where Jellyfin is still
 * coming up.
 */
export function LibraryStatsTiles() {
  const libraries = useLibraries();

  const counts = useMemo<Record<TileKey, number>>(() => {
    const acc: Record<TileKey, number> = {
      movies: 0,
      tv: 0,
      tracks: 0,
      books: 0,
    };
    const live = asArray<LiveLibraryEntry>(libraries.data?.live);
    const configured = asArray<ConfiguredLibraryEntry>(
      libraries.data?.configured,
    );

    // Live data wins when present (Jellyfin is reachable and the
    // entry has a real item_count). Sum item_counts per tile.
    let liveSurfaced = false;
    for (const entry of live) {
      const tile = entry.collection_type
        ? COLLECTION_TO_TILE[entry.collection_type]
        : undefined;
      if (!tile) continue;
      if (typeof entry.item_count === "number" && entry.item_count > 0) {
        acc[tile] += entry.item_count;
        liveSurfaced = true;
      }
    }
    if (liveSurfaced) return acc;

    // Fallback: count configured libraries per collection_type.
    for (const entry of configured) {
      const tile = COLLECTION_TO_TILE[entry.collection_type];
      if (!tile) continue;
      acc[tile] += 1;
    }
    return acc;
  }, [libraries.data]);

  return (
    <>
      {libraries.error ? (
        <div
          role="alert"
          data-testid="library-stats-error"
          className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
        >
          <p className="font-medium">Failed to load library stats</p>
          <p className="mt-1 text-fg-muted">{libraries.error.message}</p>
          <button
            type="button"
            onClick={() => libraries.refetch()}
            className="mt-2 rounded-md border border-border px-3 py-1 text-xs"
          >
            Retry
          </button>
        </div>
      ) : null}

      <div
        className="grid grid-cols-2 gap-4 sm:grid-cols-2 md:grid-cols-4"
        data-testid="library-stats"
      >
        {libraries.isLoading
          ? [0, 1, 2, 3].map((i) => (
              <Card key={i} data-testid="library-stats-skeleton">
                <CardHeader className="pb-2">
                  <Skeleton className="h-4 w-20" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-8 w-16" />
                </CardContent>
              </Card>
            ))
          : (Object.keys(STAT_LABELS) as TileKey[]).map((k) => (
              <StatCard
                key={k}
                label={STAT_LABELS[k]}
                value={counts[k]}
                icon={STAT_ICONS[k]}
              />
            ))}
      </div>
    </>
  );
}
